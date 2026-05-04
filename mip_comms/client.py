import os
import sys

import grpc

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "proto"))
import solution_pb2_grpc as _grpc_pb2  # noqa: E402  # type: ignore[import-not-found]


class SolutionClient:
    """Sends MIP solutions to a SolutionServer over gRPC (unary RPC per solution).

    Usage::

        with SolutionClient("localhost:50051") as client:
            client.send(solution_proto)
    """

    def __init__(self, address: str) -> None:
        self._channel = grpc.insecure_channel(address)
        self._stub = _grpc_pb2.SolutionServiceStub(self._channel)

    def send(self, solution) -> None:
        """Send one Solution proto synchronously. Blocks until Ack is received."""
        self._stub.SendSolution(solution)

    def close(self) -> None:
        """Close the gRPC channel."""
        self._channel.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
        return False
