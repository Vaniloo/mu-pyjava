"""Cooperative turn cancellation shared by the agent, model and tools."""

import threading


class TurnCancelled(RuntimeError):
    pass


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self._event.is_set():
            raise TurnCancelled("Turn cancelled")

    def wait(self, timeout: float) -> bool:
        return self._event.wait(timeout)
