# mip_comms

A library for streaming feasible MIP solutions from an **OR-Tools CP-SAT** solver to a **Gurobi** solver running in a separate process on the same machine. Gurobi uses each received solution as a warm start, injecting it into its branch-and-bound whenever it improves on the current incumbent.

```
OR-Tools process                     Gurobi process
─────────────────                    ──────────────
Solve CP-SAT model      ──solution→  Inject into B&B via cbSetSolution
  (finds incumbents)    ──solution→  (only if better than Gurobi's best)
  (finds incumbents)    ──solution→
```

---

## Repository layout

```
mip_sol_proto/
├── mip_comms/              # importable Python library
│   ├── __init__.py
│   ├── client.py           # SolutionClient  — OR-Tools side
│   ├── server.py           # SolutionServer  — Gurobi side
│   ├── ortools_adapter.py  # ORToolsAdapter
│   ├── gurobi_adapter.py   # GurobiAdapter
│   └── serializer.py       # internal proto helpers
├── proto/                  # generated protobuf stubs (do not edit by hand)
│   ├── solution.proto
│   ├── solution_pb2.py
│   ├── solution_pb2_grpc.py
│   └── build_proto.sh      # regenerate stubs after editing solution.proto
├── examples/
│   ├── tsp_instance.py     # shared problem instance
│   ├── tsp_ortools.py      # OR-Tools sender example
│   └── tsp_gurobi.py       # Gurobi receiver example
├── pyrightconfig.json       # tells Pylance to leave proto/ alone
└── README.md
```

---

## Setup

Example resolve the library by inserting `mip_sol_proto/` into `sys.path` at the top of the file, so they can be run from any working directory:

```python
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))  # → mip_sol_proto/
from mip_comms import SolutionClient, ORToolsAdapter
```

For other scripts using this library,  `sys.path.insert(<path_to_this_repo>)` can be used to make the `mip_comms` package available. 

If the proto stubs are ever corrupted by the IDE (Pylance can rewrite generated imports), regenerate them:

```bash
cd mip_sol_proto/proto && bash build_proto.sh
```

---

## Variable naming convention

Mathematical programming models write decision variables with a **family name** and a tuple of **indices**:

> x_{i,j,k,t} ∈ {0, 1}

Variable names in this library follow the same pattern as a string:

```
family_index1,index2,...
```

| Mathematical notation | String name      |
|-----------------------|------------------|
| x_{i,j,k,t}          | `"X_i,j,k,t"`   |
| y_{j,t}               | `"Y_j,t"`        |
| z_{0,3,1,2}           | `"Z_0,3,1,2"`   |

**The OR-Tools name and the Gurobi name for the same variable must be identical.** The library matches variables between solvers by this string. If they differ, the warm start is silently skipped for that variable.

---

## OR-Tools process

### 1. Declare variables

Name variables using the `family_i,j,...` convention with an f-string:

```python
from ortools.sat.python import cp_model

CP = cp_model.CpModel()

# x_{i,j,k,t} ∈ {0,1}  for all valid (i,j,k,t)
X = {
    (i, j, k, t): CP.new_bool_var(f"X_{i},{j},{k},{t}")
    for i in Tasks
    for j in J
    for k in K
    for t in T
    if Tasks[i][t] > 0
}

# y_{j,t} ∈ {0,1}
Y = {
    (j, t): CP.new_bool_var(f"Y_{j},{t}")
    for j in J
    for t in T
}
```

Then build your constraints and objective as usual.

### 2. Stream solutions

Pass all variable families to `ORToolsAdapter` as a `dict`. The adapter validates that every variable's `.name` matches its key — it raises `ValueError` immediately on any mismatch, catching naming mistakes before the solver runs.

```python
from mip_comms import SolutionClient, ORToolsAdapter

adapter = ORToolsAdapter({"X": X, "Y": Y})  # add more families as needed

solver = cp_model.CpSolver()

with SolutionClient("localhost:50051") as client:
    cb = adapter.make_streaming_callback(client)
    solver.solve(CP, cb)
```

Every time the CP-SAT solver finds a new (improving) incumbent, it is automatically serialised and sent to the Gurobi process. The library prints a line for each solution streamed:

```
[mip_comms] OR-Tools incumbent: obj=1234.5678 — streaming to Gurobi
```

---

## Gurobi process

### 1. Declare variables

Declare the **same** decision variables in Gurobi using **identical names**:

```python
import gurobipy as gp

m = gp.Model()

# x_{i,j,k,t} ∈ {0,1} — same index sets and conditions as OR-Tools
X = {
    (i, j, k, t): m.addVar(vtype=gp.GRB.BINARY, name=f"X_{i},{j},{k},{t}")
    for i in Tasks
    for j in J
    for k in K
    for t in T
    if Tasks[i][t] > 0
}
Y = {
    (j, t): m.addVar(vtype=gp.GRB.BINARY, name=f"Y_{j},{t}")
    for j in J
    for t in T
}

m.update()
# ... add constraints and objective ...
```

### 2. Receive and inject solutions

`GurobiAdapter` holds a thread-safe queue of solutions received from OR-Tools. At each branch-and-bound node (`MIPNODE` event), it checks whether the queued solution is **strictly better** than Gurobi's current incumbent before injecting — Gurobi is never directed backwards.

```python
from mip_comms import SolutionServer, GurobiAdapter

adapter = GurobiAdapter(m)

server = SolutionServer("localhost:50051", adapter.on_solution_received)
server.start()

m.optimize(adapter.make_injection_callback())

server.stop()
```

The library prints a line for each receive, injection attempt, and outcome:

```
[mip_comms] Received from OR-Tools: obj=1234.5678 (queued)
[mip_comms] Attempting warm-start: OR-Tools obj=1234.5678 vs Gurobi best=1350.0000
[mip_comms] Warm-start accepted by Gurobi: obj=1234.5678
```

The two solvers should be run **in parallel**. Gurobi does not wait for OR-Tools to finish.

### 3. Combining with your own Gurobi callback

If your formulation already uses a Gurobi callback (e.g. for lazy subtour-elimination constraints), obtain the injection callback separately and call it from inside your own:

```python
injection_cb = adapter.make_injection_callback()

def my_callback(model, where):
    if where == gp.GRB.Callback.MIPSOL:
        # ... your lazy constraint logic ...
        pass
    injection_cb(model, where)  # handles MIPNODE warm-start injection

m.optimize(my_callback)
```

---

## Starting order

Start the **Gurobi process first** so the server is listening before OR-Tools tries to connect.

---

## What happens if variable names do not match?

- **OR-Tools side**: `ORToolsAdapter` raises `ValueError` at construction if a variable's `.name` does not match the key you gave it. Fix the f-string in your `new_bool_var(...)` call.

- **Gurobi side**: `GurobiAdapter` silently skips any variable it cannot find in the received solution (passes `GRB.UNDEFINED` for that slot, letting Gurobi determine the value itself). No error is raised, but the warm start will be less effective.

The safest practice is to use identical f-strings in both files.

---

## Examples

The `examples/` directory contains a Travelling Salesman Problem solved with both solvers communicating over `mip_comms`. Run from `mip_sol_proto/examples/` with the venv active:

```bash
# Terminal 1 — start Gurobi receiver first
python3 tsp_gurobi.py

# Terminal 2 — start OR-Tools sender
python3 tsp_ortools.py
```

`tsp_instance.py` defines the shared city coordinates and distance matrix used by both scripts. Both scripts import it directly, so they always solve the same problem.

The Gurobi example also demonstrates the combined callback pattern: subtour-elimination lazy constraints are added in `MIPSOL` events while OR-Tools warm starts are injected in `MIPNODE` events, both handled by a single callback function passed to `m.optimize()`.
