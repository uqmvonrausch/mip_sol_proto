import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))
import solution_pb2 as _pb2  # noqa: E402  # type: ignore[import-not-found]


def parse_var_name(name: str) -> tuple[str, str]:
    """Split 'X_0,1,2,3' into ('X', '0,1,2,3') on the first underscore."""
    if "_" in name:
        family, index = name.split("_", 1)
        return family, index
    return name, ""


def build_variable_set(var_values: dict[str, float]) -> _pb2.VariableSet:
    """Convert flat {canonical_name: value} dict into a VariableSet proto."""
    grouped: dict[str, dict[str, float]] = {}
    for name, value in var_values.items():
        family, index = parse_var_name(name)
        grouped.setdefault(family, {})[index] = value

    var_set = _pb2.VariableSet()
    for family, index_map in grouped.items():
        var_map = _pb2.VariableMap()
        var_map.values.update(index_map)
        var_set.vars[family].CopyFrom(var_map)
    return var_set


def build_solution_proto(
    var_values: dict[str, float],
    objective_value: float,
    feasible: bool,
) -> _pb2.Solution:
    """Build a Solution proto from a flat variable dict and scalar metadata."""
    return _pb2.Solution(
        variable_map=build_variable_set(var_values),
        objective_value=objective_value,
        feasible=feasible,
        timestamp=int(time.time()),
    )


def read_variable_set(variable_set: _pb2.VariableSet) -> dict[str, float]:
    """Invert a VariableSet proto into a flat {canonical_name: value} dict.

    'X' family with index '0,1,2,3' → key 'X_0,1,2,3'.
    Family with empty index '' → key is the family name alone.
    """
    result: dict[str, float] = {}
    for family, var_map in variable_set.vars.items():
        for index, value in var_map.values.items():
            name = f"{family}_{index}" if index else family
            result[name] = value
    return result
