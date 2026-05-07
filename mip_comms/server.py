import concurrent.futures
import os
import sys

import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))
import solution_pb2 as _pb2  # noqa: E402  # type: ignore[import-not-found]
import solution_pb2_grpc as _grpc_pb2  # noqa: E402  # type: ignore[import-not-found]


class _SolutionServiceServicer(_grpc_pb2.SolutionServiceServicer):
    def __init__(self, callback) -> None:
        self._callback = callback

    def SendSolution(self, request, context):
        self._callback(request)
        return _pb2.Ack(success=True)


class SolutionServer:
    """gRPC server that receives MIP solutions and invokes a callback per solution.

    The callback is called on a gRPC worker thread — use a threading.Lock or
    queue.Queue inside it if you need to pass data to the main thread safely.

    Usage::

        def on_solution(solution):
            vals = solution.variable_map.vars["X"].values  # dict[str, float]

        with SolutionServer("localhost:50051", on_solution) as srv:
            srv.wait_for_termination()
    """

    def __init__(self, address: str, callback, max_workers=1) -> None:
        self._server = grpc.server(
            concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        )
        _grpc_pb2.add_SolutionServiceServicer_to_server(
            _SolutionServiceServicer(callback), self._server
        )
        self._server.add_insecure_port(address)

    def start(self) -> None:
        self._server.start()

    def stop(self, grace: float = 5.0) -> None:
        self._server.stop(grace)

    def wait_for_termination(self, timeout: float | None = None) -> bool:
        return self._server.wait_for_termination(timeout=timeout)

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()
        return False
