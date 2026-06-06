"""In-process pub/sub broker for Server-Sent Events (real-time updates).

Thread-based analogue of the original asyncio broker. Each poll has a set of
subscriber queue.Queue objects. When a vote lands (or the poll is closed) we
publish the fresh results JSON to every subscriber of that poll. Good enough
for a single-process self-hosted deployment (run Flask with threaded=True).
"""
from __future__ import annotations

import queue
import threading
from collections import defaultdict
from typing import Dict, Set


class EventBroker:
    def __init__(self) -> None:
        self._subscribers: Dict[str, Set[queue.Queue]] = defaultdict(set)
        self._lock = threading.Lock()

    def subscribe(self, poll_id: str) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            self._subscribers[poll_id].add(q)
        return q

    def unsubscribe(self, poll_id: str, q: queue.Queue) -> None:
        with self._lock:
            self._subscribers[poll_id].discard(q)
            if not self._subscribers[poll_id]:
                self._subscribers.pop(poll_id, None)

    def publish(self, poll_id: str, data: str) -> None:
        with self._lock:
            queues = list(self._subscribers.get(poll_id, ()))
        for q in queues:
            # Best effort; never block the publisher.
            try:
                q.put_nowait(data)
            except queue.Full:
                pass


broker = EventBroker()
