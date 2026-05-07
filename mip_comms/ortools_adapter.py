from ortools.sat.python import cp_model

from .serializer import build_solution_proto


def _key_to_index(key) -> str:
    """Convert a dict key (tuple, int, or str) to a comma-joined index string."""
    if isinstance(key, tuple):
        return ",".join(str(k) for k in key)
    return str(key)


def _expected_name(family: str, key) -> str:
    index = _key_to_index(key)
    return f"{family}_{index}" if index else family


class _StreamingCallback(cp_model.CpSolverSolutionCallback):
    """CpSolverSolutionCallback that streams each incumbent via gRPC."""

    def __init__(self, var_map: dict[str, object], client) -> None:
        super().__init__()
        self._var_map = var_map  # {canonical_name: IntVar}
        self._client = client
        self._last_var_values: dict[str, float] = {}
        self._last_obj: float = 0.0

    def on_solution_callback(self) -> None:
        var_values = {
            name: float(self.value(var))
            for name, var in self._var_map.items()
        }
        self._last_var_values = var_values
        self._last_obj = float(self.objective_value)
        solution = build_solution_proto(
            var_values=var_values,
            objective_value=self.objective_value,
            feasible=True,
        )
        print(f"[mip_comms] OR-Tools incumbent: obj={self.objective_value:.4f} — streaming to Gurobi")
        self._client.send(solution)


class ORToolsAdapter:
    """Extracts OR-Tools CP-SAT incumbent solutions and streams them via gRPC.

    Pass a dict mapping family names to OR-Tools variable dicts. Each inner
    dict maps index keys (tuples, ints, or strings) to IntVar/BoolVar objects.

    At construction the adapter validates that each variable's `.name` matches
    the canonical name derived from the family + key, raising ValueError if not.

    Example::

        X = {(i,j,k,t): CP.NewBoolVar(f"X_{i},{j},{k},{t}") for ...}
        Y = {(j,t):     CP.NewBoolVar(f"Y_{j},{t}")          for ...}

        adapter = ORToolsAdapter({"X": X, "Y": Y})

        with SolutionClient("localhost:50051") as client:
            solver.solve(cp_model, adapter.make_streaming_callback(client))
    """

    def __init__(self, variable_groups: dict[str, dict]) -> None:
        var_map: dict[str, object] = {}

        for family, group in variable_groups.items():
            for key, var in group.items():
                expected = _expected_name(family, key)
                if var.name != expected:
                    raise ValueError(
                        f"Variable at key {key!r} in group {family!r} has "
                        f"name {var.name!r} but expected {expected!r}. "
                        f"Gurobi warm-start will look for {expected!r} - "
                        f"ensure variables are named e.g. "
                        f'f"{family}_{{...}}".'
                    )
                var_map[expected] = var

        self._var_map = var_map

    def make_streaming_callback(self, client) -> _StreamingCallback:
        """Return a CpSolverSolutionCallback that streams each incumbent."""
        return _StreamingCallback(self._var_map, client)

    def send_optimal_solution(self, client, callback: _StreamingCallback) -> None:
        """Resend the last incumbent with is_optimal=True after solver proves optimality.

        Call this inside the SolutionClient context immediately after
        solver.solve() returns cp_model.OPTIMAL::

            cb = adapter.make_streaming_callback(client)
            status = solver.solve(CP, cb)
            if status == cp_model.OPTIMAL:
                adapter.send_optimal_solution(client, cb)
        """
        signal = build_solution_proto(
            var_values=callback._last_var_values,
            objective_value=callback._last_obj,
            feasible=True,
            is_optimal=True,
        )
        client.send(signal)
