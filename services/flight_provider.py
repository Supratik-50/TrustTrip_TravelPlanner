"""Provider abstraction for flight shopping and booking."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class FlightProviderError(RuntimeError):
    """Expected provider/API failure that can be shown to the client."""


class FlightProviderTimeout(FlightProviderError):
    """Provider request exceeded its configured timeout."""


class FlightProvider(ABC):
    name = "base"

    @abstractmethod
    def search(self, request: dict[str, Any]) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def get_offer(self, offer_id: str) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def book(
        self,
        offer_id: str,
        passengers: list[dict[str, Any]],
        payment: dict[str, Any] | None = None,
        payment_mode: str = "auto",
    ) -> dict[str, Any]:
        raise NotImplementedError
