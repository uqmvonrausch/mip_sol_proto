"""
TSP sender — OR-Tools CP-SAT.

Solves a 40-city Travelling Salesman Problem using the CP-SAT circuit
constraint and streams every improving incumbent to the Gurobi process
via mip_comms.

Start the Gurobi process (tsp_gurobi.py) FIRST so the server is ready
before this script tries to connect.

Run from mip_sol_proto/:
    python examples/tsp_ortools.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

from ortools.sat.python import cp_model
from mip_comms import SolutionClient, ORToolsAdapter
from tsp_instance import N, DIST

# ---------------------------------------------------------------------------
# Decision variables
#
# x_{i,j} in {0,1}  for all i,j with i != j
#   x_{i,j} = 1  iff the tour travels directly from city i to city j
# ---------------------------------------------------------------------------
CP = cp_model.CpModel()

X = {
    (i, j): CP.new_bool_var(f"X_{i},{j}")
    for i in range(N)
    for j in range(N)
    if i != j
}

# ---------------------------------------------------------------------------
# Constraints
#
# add_circuit enforces that the chosen arcs form a single Hamiltonian cycle
# covering all N cities exactly once.
# ---------------------------------------------------------------------------
CP.add_circuit([(i, j, X[(i, j)]) for (i, j) in X])

# ---------------------------------------------------------------------------
# Objective: minimise total tour length
# ---------------------------------------------------------------------------
CP.minimize(sum(DIST[i][j] * X[(i, j)] for (i, j) in X))

# ---------------------------------------------------------------------------
# Solve and stream
# ---------------------------------------------------------------------------
adapter = ORToolsAdapter({"X": X})
solver = cp_model.CpSolver()
solver.parameters.max_time_in_seconds = 180  # run for up to 3 minutes

print(f"[OR-Tools] Solving {N}-city TSP (streaming incumbents to localhost:50051) ...")

with SolutionClient("localhost:50051") as client:
    cb = adapter.make_streaming_callback(client)
    status = solver.solve(CP, cb)
    if status == cp_model.OPTIMAL:
        adapter.send_optimal_solution(client, cb)

print(f"[OR-Tools] Finished — status: {solver.status_name(status)}")
if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
    print(f"[OR-Tools] Best tour length: {solver.objective_value:.1f}")
