"""
TSP receiver — Gurobi with subtour-elimination lazy constraints.

Solves the same 40-city TSP as tsp_ortools.py using Gurobi's
branch-and-bound. Every OR-Tools incumbent received over gRPC is
injected as a warm-start via cbSetSolution / cbUseSolution if it
is better than Gurobi's current incumbent.

Start this script FIRST — it opens the gRPC server before OR-Tools
connects.

Formulation
-----------
Variables:
    x_{i,j} in {0,1}  for all i,j with i != j
        1 iff the tour travels directly from city i to city j

Degree constraints (enforce that every city is entered and left once):
    sum_{i != j} x_{i,j} = 1   for all j   (enter j exactly once)
    sum_{j != i} x_{i,j} = 1   for all i   (leave i exactly once)

Subtour elimination (added lazily during branch-and-bound):
    sum_{i in S, j in S, i!=j} x_{i,j}  <=  |S| - 1
        for every strict subset S of cities with 2 <= |S| <= N-1

Objective:
    minimise  sum_{i,j} dist(i,j) * x_{i,j}

Run from mip_sol_proto/:
    python examples/tsp_gurobi.py
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

import gurobipy as gp
from mip_comms import SolutionServer, GurobiAdapter
from tsp_instance import N, DIST

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
m = gp.Model()
m.Params.LazyConstraints = 1   # required when adding cuts in a callback
m.Params.TimeLimit = 300       # 5-minute wall-clock limit
m.Params.Threads = 12

# x_{i,j} in {0,1}  for all i != j
X = {
    (i, j): m.addVar(vtype=gp.GRB.BINARY, name=f"X_{i},{j}")
    for i in range(N)
    for j in range(N)
    if i != j
}
m.update()

# Each city entered exactly once
for j in range(N):
    m.addConstr(gp.quicksum(X[i, j] for i in range(N) if i != j) == 1)

# Each city left exactly once
for i in range(N):
    m.addConstr(gp.quicksum(X[i, j] for j in range(N) if i != j) == 1)

m.setObjective(
    gp.quicksum(DIST[i][j] * X[i, j] for (i, j) in X),
    gp.GRB.MINIMIZE,
)

# ---------------------------------------------------------------------------
# Subtour detection
# ---------------------------------------------------------------------------
def _find_subtours(x_sol: dict) -> list[list[int]]:
    """
    Given {(i,j): value}, find all maximal tours in the support.
    Returns a list of city lists; a single list of length N means
    the solution is a valid Hamiltonian cycle.
    """
    succ = {i: j for (i, j), v in x_sol.items() if v > 0.5}
    visited: set[int] = set()
    tours: list[list[int]] = []
    for start in range(N):
        if start not in visited:
            tour = []
            node = start
            while node not in visited:
                visited.add(node)
                tour.append(node)
                node = succ.get(node, -1)
                if node == -1:
                    break
            tours.append(tour)
    return tours

# ---------------------------------------------------------------------------
# Gurobi adapter (warm-start injection from OR-Tools)
# ---------------------------------------------------------------------------
adapter = GurobiAdapter(m)
injection_cb = adapter.make_injection_callback()

def _combined_callback(model, where):
    # --- subtour elimination (MIPSOL: integer-feasible node) ---
    if where == gp.GRB.Callback.MIPSOL:
        x_sol = {(i, j): model.cbGetSolution(X[i, j]) for (i, j) in X}
        tours = _find_subtours(x_sol)
        if len(tours) > 1:
            for tour in tours:
                s = set(tour)
                # SEC: sum of arcs within S <= |S| - 1
                model.cbLazy(
                    gp.quicksum(X[i, j] for i in s for j in s if i != j)
                    <= len(s) - 1
                )

    # --- warm-start injection from OR-Tools (MIPNODE) ---
    injection_cb(model, where)

# ---------------------------------------------------------------------------
# Start gRPC server and solve
# ---------------------------------------------------------------------------
server = SolutionServer("localhost:50051", adapter.on_solution_received)
server.start()
print(f"[Gurobi] Server listening on localhost:50051")
print(f"[Gurobi] Solving {N}-city TSP ...")

m.optimize(_combined_callback)

server.stop()

print(f"[Gurobi] Finished")
if m.SolCount > 0:
    print(f"[Gurobi] Best tour length: {m.ObjVal:.1f}")
    print(f"[Gurobi] Optimality gap:   {m.MIPGap * 100:.2f}%")
