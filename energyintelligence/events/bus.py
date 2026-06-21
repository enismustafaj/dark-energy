"""In-process, per-tenant pub/sub for SSE.

Each household has its own set of subscriber queues; publishing an event fans it
out only to that tenant's subscribers, so a household's SSE stream can never see
another tenant's events. This is the live-update backbone: the ingest and
anomaly paths publish here, and the SSE endpoint drains a per-connection queue.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from dataclasses import dataclass


@dataclass
class Event:
    type: str          # telemetry | insight | action
    data: dict


class EventBus:
    def __init__(self) -> None:
        # household_id -> set of subscriber queues
        self._subscribers: dict[str, set[asyncio.Queue]] = defaultdict(set)

    def subscribe(self, household_id: str) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        self._subscribers[household_id].add(q)
        return q

    def unsubscribe(self, household_id: str, q: asyncio.Queue) -> None:
        self._subscribers[household_id].discard(q)

    async def publish(self, household_id: str, event: Event) -> None:
        for q in list(self._subscribers.get(household_id, ())):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # Drop for a slow consumer rather than blocking the writer.
                pass


# Single process-wide bus instance.
bus = EventBus()
