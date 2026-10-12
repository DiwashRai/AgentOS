"""In-process pub/sub for SSE notifications (SPEC §10 "Broker").

A fan-out of asyncio queues, one per `GET /api/stream` subscriber, on the server's event loop
(`bind` at startup, `close` at shutdown). Services publish from sync code in any thread (FastAPI's
threadpool, the dispatcher and reaper loops) through `loop.call_soon_threadsafe`: publishing
never blocks, never raises, and is a no-op while nobody listens.

Events are notifications only (the client refetches on receipt), so dropping is safe: a subscriber
that falls MAX_QUEUED events behind is dropped (its stream ends; the browser's EventSource
reconnects and refetches everything).

Publish **after** the transaction commits, so a client's refetch sees the change. Every event
names the one kind of data that changed; a change that also shows on the ticket or the board
(runs, chat counts) publishes a `ticket` event for the same key too:
- `ticket {key}`: the ticket, its pipeline, its files or the board row changed.
- `run {key, id}` (+ `ticket {key}`): a run of the ticket changed state or stage.
- `chat {key, id}` (+ `ticket {key}`): a chat message was posted.

Scaling escape hatch: Postgres LISTEN/NOTIFY behind this module (SPEC "Scaling escape hatch").
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from agentos.models import StreamEvent

log = logging.getLogger(__name__)

MAX_QUEUED = 1000  # per subscriber (SPEC §10)


class Subscription:
    """One subscriber's queue. Lives on the broker's loop: only the loop touches it."""

    def __init__(self, max_queued: int) -> None:
        self._max = max_queued
        # Unbounded: the limit is checked in `_offer`, so the end marker always fits.
        self._queue: asyncio.Queue[StreamEvent | None] = asyncio.Queue()
        self.ended = False

    async def get(self) -> StreamEvent | None:
        """The next event, or None once the subscription has ended (dropped for falling behind,
        or the broker closed)."""
        event = await self._queue.get()
        if event is None:
            self._queue.put_nowait(None)  # stay ended for every later get
        return event

    def _offer(self, event: StreamEvent) -> bool:
        """Queue the event; False (and end the subscription) if it is MAX_QUEUED behind."""
        if self.ended:
            return False
        if self._queue.qsize() >= self._max:
            self._end()
            return False
        self._queue.put_nowait(event)
        return True

    def _end(self) -> None:
        """Discard what is queued (the client refetches everything on reconnect) and wake the
        reader with the end marker."""
        if self.ended:
            return
        self.ended = True
        while not self._queue.empty():
            self._queue.get_nowait()
        self._queue.put_nowait(None)


class Broker:
    def __init__(self, max_queued: int = MAX_QUEUED) -> None:
        self._max = max_queued
        self._loop: asyncio.AbstractEventLoop | None = None
        self._subs: set[Subscription] = set()

    # ------------------------------------------------------------------ lifecycle (on the loop)

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Deliver on `loop` (the server's; called at lifespan start)."""
        self._loop = loop

    def close(self) -> None:
        """Shutdown, on the loop: end every subscription (their streams finish) and unbind, so
        later publishes are no-ops and later subscriptions end at once."""
        self._loop = None
        subs, self._subs = self._subs, set()
        for sub in subs:
            sub._end()

    @property
    def subscribers(self) -> int:
        return len(self._subs)

    def subscribe(self) -> Subscription:
        """A new subscription (call on the bound loop); pair it with `unsubscribe` in a `finally`.
        On an unbound or closed broker it has already ended.

        Not a context manager on purpose: the SSE body is an async generator, and an async
        context manager inside one is finalised separately by the GC when the client goes away
        mid-`yield`, which breaks its `__aexit__`."""
        sub = Subscription(self._max)
        if self._loop is None:
            sub._end()
        else:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        """Stop delivering to `sub` (on the loop). Idempotent."""
        self._subs.discard(sub)

    # ------------------------------------------------------------------ publishing (any thread)

    def publish(self, *events: StreamEvent) -> None:
        """Hand the events to every subscriber. Thread-safe; never raises into the caller."""
        loop = self._loop
        if loop is None or not self._subs or not events:
            return
        try:
            loop.call_soon_threadsafe(self._deliver, events)
        except RuntimeError:  # the loop is closed: shutting down, nobody is listening
            log.debug("broker: loop closed, dropped %s event(s)", len(events))

    def ticket(self, key: str) -> None:
        """The ticket, its pipeline, its files or its board row changed."""
        if self._subs:
            self.publish(StreamEvent(type="ticket", key=key))

    def run(self, key: str, run_id: int) -> None:
        """A run changed state or stage (which also shows on the ticket and the board)."""
        if self._subs:
            self.publish(
                StreamEvent(type="run", key=key, id=run_id), StreamEvent(type="ticket", key=key)
            )

    def chat(self, key: str, message_id: int) -> None:
        """A chat message was posted (the board shows chat counts)."""
        if self._subs:
            self.publish(
                StreamEvent(type="chat", key=key, id=message_id),
                StreamEvent(type="ticket", key=key),
            )

    def _deliver(self, events: Sequence[StreamEvent]) -> None:
        for sub in list(self._subs):
            for event in events:
                if not sub._offer(event):
                    self._subs.discard(sub)
                    log.warning("broker: dropped a subscriber %s events behind", self._max)
                    break
