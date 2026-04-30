from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class BaseAdapter(ABC):
    """
    Abstract base class that all external system adapters must implement.

    Each adapter manages its own connection lifecycle and exposes a standard
    set of CRM-oriented operations. Adapters that do not support a particular
    operation should raise NotImplementedError with a descriptive message.
    """

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @abstractmethod
    async def connect(self) -> None:
        """Establish a connection / authenticate with the external system."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully close the connection to the external system."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return True if the adapter currently has a live connection."""

    # ------------------------------------------------------------------
    # CRM contract
    # ------------------------------------------------------------------

    @abstractmethod
    async def get_contact(self, contact_id: str) -> dict[str, Any]:
        """Retrieve a single contact / person record by its unique ID."""

    @abstractmethod
    async def create_note(self, client_id: str, note: str) -> dict[str, Any]:
        """Attach a freeform note to a client / contact record."""

    @abstractmethod
    async def search_leads(self, filters: dict[str, Any]) -> list[dict[str, Any]]:
        """
        Search for leads using the provided filter criteria.

        The shape of `filters` is adapter-specific, but common keys include
        ``query`` (free-text), ``stage``, ``owner``, and ``limit``.
        """
