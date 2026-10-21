"""Content-free milestones for one Telegram reply, shared across its lifecycle."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ReplyTiming:
    request_id: str
    received_at: float = field(default_factory=time.monotonic)
    _seen: set[str] = field(default_factory=set, init=False, repr=False)

    def mark(self, stage: str) -> None:
        """Record the first occurrence, so token volume cannot amplify logging."""
        if stage in self._seen:
            return
        self._seen.add(stage)
        log.info(
            "Telegram reply timing request_id=%s stage=%s elapsed_ms=%.1f",
            self.request_id,
            stage,
            (time.monotonic() - self.received_at) * 1000,
        )
