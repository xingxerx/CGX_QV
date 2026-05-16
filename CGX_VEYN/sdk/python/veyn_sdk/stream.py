"""Convenience wrappers that keep a persistent connection to the event stream."""

from __future__ import annotations

import asyncio
import json
import threading
from queue import Queue
from typing import Any, Callable, Iterator, Optional

from .client import ContextSnapshot, VeynClient, VeynEvent


class EventStream:
    """Subscribe to the VEYN SSE stream in a background thread.

    Usage::

        stream = EventStream(VeynClient.from_token_file(), metrics="heart_rate,hrv")
        stream.start()

        for event in stream:
            print(event)

        stream.stop()

    Or as a context manager::

        with EventStream(client, sources="healthkit") as stream:
            for event in stream:
                ...
    """

    def __init__(
        self,
        client: VeynClient,
        metrics: Optional[str] = None,
        sources: Optional[str] = None,
        on_event: Optional[Callable[[VeynEvent], None]] = None,
        queue_size: int = 256,
    ) -> None:
        self._client = client
        self._metrics = metrics
        self._sources = sources
        self._on_event = on_event
        self._queue: Queue[Optional[VeynEvent]] = Queue(maxsize=queue_size)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> "EventStream":
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put(None)  # unblock __iter__
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "EventStream":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()

    def __iter__(self) -> Iterator[VeynEvent]:
        while True:
            item = self._queue.get()
            if item is None:
                break
            yield item

    def _run(self) -> None:
        for event in self._client.stream_sse(
            metrics=self._metrics, sources=self._sources
        ):
            if self._stop_event.is_set():
                break
            if self._on_event:
                self._on_event(event)
            else:
                if not self._queue.full():
                    self._queue.put(event)
        self._queue.put(None)


class ContextStream:
    """Subscribe to context snapshots filtered by the declarative DSL.

    Usage::

        with ContextStream(client, intents="calm,idle", min_confidence=0.7) as stream:
            for ctx in stream:
                print(ctx.intent, ctx.confidence)
    """

    def __init__(
        self,
        client: VeynClient,
        intents: Optional[str] = None,
        min_confidence: float = 0.0,
        tier: str = "filtered",
        on_snapshot: Optional[Callable[[ContextSnapshot], None]] = None,
        queue_size: int = 64,
    ) -> None:
        self._client = client
        self._intents = intents
        self._min_confidence = min_confidence
        self._tier = tier
        self._on_snapshot = on_snapshot
        self._queue: Queue[Optional[ContextSnapshot]] = Queue(maxsize=queue_size)
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()

    def start(self) -> "ContextStream":
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop_event.set()
        self._queue.put(None)
        if self._thread:
            self._thread.join(timeout=5)

    def __enter__(self) -> "ContextStream":
        return self.start()

    def __exit__(self, *_: Any) -> None:
        self.stop()

    def __iter__(self) -> Iterator[ContextSnapshot]:
        while True:
            item = self._queue.get()
            if item is None:
                break
            yield item

    def _run(self) -> None:
        for snap in self._client.subscribe_context(
            intents=self._intents,
            min_confidence=self._min_confidence,
            tier=self._tier,
        ):
            if self._stop_event.is_set():
                break
            if self._on_snapshot:
                self._on_snapshot(snap)
            else:
                if not self._queue.full():
                    self._queue.put(snap)
        self._queue.put(None)
