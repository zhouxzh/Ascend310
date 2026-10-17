"""Stable backend contract for controlled Ascend Edge Chat extensions."""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Protocol, Sequence, Tuple


class BackendProvider(Protocol):
    """Minimum contract consumed by the loopback worker.

    Providers are created from a validated release profile before they receive
    requests. They never receive a model path or backend selector from an HTTP
    client.
    """

    profile: Any

    @property
    def healthy(self) -> bool:
        """Return whether the loaded provider can serve another request."""

    def start(self) -> None:
        """Load the model and initialize its hardware context."""

    def status(self) -> Mapping[str, Any]:
        """Return non-secret runtime status for the worker health endpoint."""

    def stream(
        self, messages: Sequence[Mapping[str, str]], max_tokens: int
    ) -> Iterable[Tuple[str, int]]:
        """Yield cumulative generated text and token count for greedy decoding."""

    def close(self) -> None:
        """Release provider-owned model and device resources."""


__all__ = ["BackendProvider"]
