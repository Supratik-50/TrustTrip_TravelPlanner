from __future__ import annotations
from typing import Any, Protocol

class HotelProviderError(RuntimeError):
    pass

class HotelProvider(Protocol):
    name: str
    def search(self, request: dict[str, Any]) -> list[dict[str, Any]]: ...
    def prebook(self, offer: dict[str, Any]) -> dict[str, Any]: ...
    def book(self, prebook: dict[str, Any], holder: dict[str, Any], guests: list[dict[str, Any]], client_reference: str) -> dict[str, Any]: ...
