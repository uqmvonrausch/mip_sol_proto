import itertools
import queue
import threading

import gurobipy as gp

from .serializer import read_variable_set


class GurobiAdapter:
    """Receives OR-Tools solutions and injects them into a live Gurobi solve.

    The server callback (on_solution_received) puts each received Solution on
    an internal priority queue ordered by objective quality (best first).
    A Gurobi MIPNODE callback drains the queue and injects solutions via
    cbSetSolution / cbUseSolution.

    When a solution arrives with is_optimal=True, a termination flag is set.
    The Gurobi callback injects the optimal solution first, then calls
    model.terminate() so the solution remains accessible in Gurobi.

    Usage::

        adapter = GurobiAdapter(model)

        server = SolutionServer("localhost:50051", adapter.on_solution_received)
        server.start()

        model.optimize(adapter.make_injection_callback())

        server.stop()
    """

    def __init__(self, model: gp.Model) -> None:
        self._model = model
        self._vars: list[gp.Var] = model.getVars()
        self._var_names: list[str] = [v.varName for v in self._vars]
        # ModelSense is 1 (minimise) or -1 (maximise); captured once so
        # on_solution_received can compute priority without holding the model lock.
        self._model_sense: int = model.ModelSense
        # PriorityQueue: items are (priority, counter, solution).
        # priority = objective_value * model_sense → smaller value is better for
        # both minimisation and maximisation, matching Python's min-heap ordering.
        # counter breaks ties so Solution objects are never compared directly.
        self._pending: queue.PriorityQueue = queue.PriorityQueue()
        self._counter = itertools.count()
        self._terminate_flag = threading.Event()

    def on_solution_received(self, solution) -> None:
        """gRPC server callback — thread-safe, returns immediately."""
        priority = solution.objective_value * self._model_sense
        self._pending.put((priority, next(self._counter), solution))
        print(f"[mip_comms] Received from OR-Tools: obj={solution.objective_value:.4f} (queued)")
        if solution.is_optimal:
            self._terminate_flag.set()

    def make_injection_callback(self):
        """Return a Gurobi callback that injects pending OR-Tools solutions.

        At each MIPNODE event the callback drains the priority queue in
        best-first order, injecting only solutions that improve on Gurobi's
        current incumbent.  After the queue is drained, if the termination
        flag is set (OR-Tools proved optimality) model.terminate() is called
        so the injected optimal solution remains accessible inside Gurobi.

        Variables absent from a received solution are passed as GRB.UNDEFINED,
        letting Gurobi determine those values itself.
        """
        vars_ = self._vars
        var_names = self._var_names
        pending = self._pending
        terminate_flag = self._terminate_flag
        model_sense = self._model_sense

        def _callback(model, where):
            if where != gp.GRB.Callback.MIPNODE:
                return

            # cbUseSolution is only valid when the LP at this node is optimal.
            if model.cbGet(gp.GRB.Callback.MIPNODE_STATUS) != gp.GRB.OPTIMAL:
                return

            # best_so_far starts at Gurobi's incumbent; updated after each
            # injection so multiple queued solutions are compared against
            # each other as well, not just the pre-callback incumbent.
            best_so_far = model.cbGet(gp.GRB.Callback.MIPNODE_OBJBST)

            while not pending.empty():
                try:
                    _, _, solution = pending.get_nowait()
                except queue.Empty:
                    break

                # Multiply by model_sense so "smaller is better" in both cases.
                if solution.objective_value * model_sense >= best_so_far * model_sense:
                    print(
                        f"[mip_comms] Skipping warm-start: OR-Tools obj={solution.objective_value:.4f}"
                        f" not better than Gurobi best={best_so_far:.4f}"
                    )
                    continue

                print(
                    f"[mip_comms] Attempting warm-start: OR-Tools obj={solution.objective_value:.4f}"
                    f" vs Gurobi best={best_so_far:.4f}"
                )
                flat = read_variable_set(solution.variable_map)
                vals = [flat.get(name, gp.GRB.UNDEFINED) for name in var_names]
                model.cbSetSolution(vars_, vals)
                accepted_obj = model.cbUseSolution()

                if accepted_obj < 1e30:
                    print(f"[mip_comms] Warm-start accepted by Gurobi: obj={accepted_obj:.4f}")
                    best_so_far = solution.objective_value
                else:
                    print(f"[mip_comms] Warm-start rejected (solution infeasible for Gurobi)")

            if terminate_flag.is_set():
                print("[mip_comms] OR-Tools proved optimality — terminating Gurobi.")
                model.terminate()

        return _callback
