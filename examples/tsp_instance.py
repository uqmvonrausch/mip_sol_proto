"""
Shared TSP instance: 40 cities placed randomly in a 500x500 grid.

Both tsp_ortools.py and tsp_gurobi.py import from here so they solve
exactly the same problem.
"""

import math
import random

N = 400

random.seed(42)
CITIES = [(random.randint(0, 500), random.randint(0, 500)) for _ in range(N)]

def dist(i: int, j: int) -> int:
    """Rounded Euclidean distance between city i and city j."""
    dx = CITIES[i][0] - CITIES[j][0]
    dy = CITIES[i][1] - CITIES[j][1]
    return round(math.sqrt(dx * dx + dy * dy))

# Pre-computed distance matrix so both solvers use identical values.
DIST = [[dist(i, j) for j in range(N)] for i in range(N)]
