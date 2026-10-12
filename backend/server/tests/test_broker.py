"""Broker (SPEC §10): fan-out, thread-safe publish, overflow, shutdown; the SSE body generator."""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator

import pytest

from agentos import stream
from agentos.broker import Broker, Subscription
from agentos.models import StreamEvent

T1 = StreamEvent(type="ticket", key="AOS-1")
T2 = StreamEvent(type="ticket", key="AOS-2")
PING = ": ping\n\n"


async def next_event(sub: Subscription) -> StreamEvent | None:
    return await asyncio.wait_for(sub.get(), 2)


async def bound() -> Broker:
    broker = Broker()
    broker.bind(asyncio.get_running_loop())
    return broker


def test_publish_fans_out_to_every_subscriber() -> None:
    async def main() -> None:
        broker = await bound()
        a, b = broker.subscribe(), broker.subscribe()
        assert broker.subscribers == 2
        broker.publish(T1, T2)
        assert [await next_event(a), await next_event(a)] == [T1, T2]
        assert [await next_event(b), await next_event(b)] == [T1, T2]
        broker.unsubscribe(a)
        broker.unsubscribe(a)  # idempotent
        broker.unsubscribe(b)
        assert broker.subscribers == 0

    asyncio.run(main())


def test_publish_from_another_thread() -> None:
    async def main() -> None:
        broker = await bound()
        sub = broker.subscribe()
        thread = threading.Thread(target=broker.run, args=("AOS-3", 7))
        thread.start()
        thread.join()
        assert await next_event(sub) == StreamEvent(type="run", key="AOS-3", id=7)
        assert await next_event(sub) == StreamEvent(type="ticket", key="AOS-3")

    asyncio.run(main())


def test_chat_also_names_the_ticket() -> None:
    async def main() -> None:
        broker = await bound()
        sub = broker.subscribe()
        broker.chat("AOS-1", 5)
        broker.ticket("AOS-2")
        got = [await next_event(sub) for _ in range(3)]
        assert got == [StreamEvent(type="chat", key="AOS-1", id=5), T1, T2]

    asyncio.run(main())


def test_a_subscriber_that_falls_behind_is_dropped() -> None:
    async def main() -> None:
        broker = Broker(max_queued=3)
        broker.bind(asyncio.get_running_loop())
        keeps_up, slow = broker.subscribe(), broker.subscribe()
        broker.publish(T1, T1, T1)
        assert [await next_event(keeps_up) for _ in range(3)] == [T1, T1, T1]
        broker.publish(T2)  # `slow` already holds 3: dropped, its backlog discarded
        assert await next_event(keeps_up) == T2
        assert await next_event(slow) is None
        assert await next_event(slow) is None  # stays ended
        assert slow.ended
        assert broker.subscribers == 1
        broker.publish(T1)
        assert await next_event(keeps_up) == T1

    asyncio.run(main())


def test_publish_without_subscribers_or_loop_is_a_no_op() -> None:
    broker = Broker()
    broker.publish(T1)  # never bound
    broker.run("AOS-1", 1)
    loop = asyncio.new_event_loop()
    loop.close()
    broker.bind(loop)
    broker.ticket("AOS-1")  # bound, nobody listening: returns before touching the loop
    broker._subs.add(Subscription(10))  # a listener, but the loop is gone (shutdown)
    broker.publish(T1)  # must not raise
    broker.chat("AOS-1", 1)


def test_close_ends_every_subscription_and_later_ones() -> None:
    async def main() -> None:
        broker = await bound()
        sub = broker.subscribe()
        broker.publish(T1)
        await asyncio.sleep(0)
        broker.close()
        assert await next_event(sub) is None  # the backlog is discarded too
        broker.publish(T1)  # unbound now: a no-op
        late = broker.subscribe()
        assert await next_event(late) is None
        assert broker.subscribers == 0

    asyncio.run(main())


# ------------------------------------------------------------------ the SSE body


async def take(body: AsyncIterator[str], n: int, *, pings: bool = False) -> list[str]:
    """The next `n` chunks (skipping pings unless `pings`)."""
    out: list[str] = []
    while len(out) < n:
        chunk = await asyncio.wait_for(anext(body), 2)
        if pings or chunk != PING:
            out.append(chunk)
    return out


def test_events_stream_data_lines_pings_and_ends_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(stream, "PING_INTERVAL_S", 0.01)

    async def main() -> None:
        broker = await bound()
        body = stream.events(broker)
        assert await take(body, 1) == [": connected\n\n"]
        assert broker.subscribers == 1
        assert await take(body, 1, pings=True) == [PING]
        broker.run("AOS-4", 9)
        chunks = await take(body, 2)
        assert chunks == [
            'data: {"type":"run","key":"AOS-4","id":9}\n\n',
            'data: {"type":"ticket","key":"AOS-4","id":null}\n\n',
        ]
        assert [StreamEvent.model_validate_json(c.removeprefix("data: ")) for c in chunks] == [
            StreamEvent(type="run", key="AOS-4", id=9),
            StreamEvent(type="ticket", key="AOS-4"),
        ]
        broker.close()
        assert [chunk async for chunk in body if chunk != PING] == []
        assert broker.subscribers == 0

    asyncio.run(main())


def test_events_unsubscribe_when_the_client_goes_away() -> None:
    async def main() -> None:
        broker = await bound()
        # Starlette cancels the response when the client disconnects...
        body = stream.events(broker)
        await take(body, 1)
        assert broker.subscribers == 1
        waiting = asyncio.create_task(take(body, 1))
        await asyncio.sleep(0.01)
        waiting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiting
        assert broker.subscribers == 0
        # ...or abandons it suspended at a `yield` (closed by the GC).
        abandoned = stream.events(broker)
        await take(abandoned, 1)
        await abandoned.aclose()
        assert broker.subscribers == 0

    asyncio.run(main())
