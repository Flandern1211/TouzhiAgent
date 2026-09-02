from __future__ import annotations

from datetime import datetime
from typing import Protocol

from fund_agent.domain.models import Evidence


class SourceAdapter(Protocol):
    """Boundary for any configured public or crawler-backed evidence source."""

    def fetch(self, subject: str, since: datetime | None = None) -> list[Evidence]: ...
