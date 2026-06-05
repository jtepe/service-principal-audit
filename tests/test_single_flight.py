"""Tests for the async single-flight cache primitive."""

from __future__ import annotations

import asyncio

from sp_audit.single_flight import SingleFlight


def test_concurrent_missers_trigger_exactly_one_fetch() -> None:
    calls = 0
    started = asyncio.Event()

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        started.set()
        await asyncio.sleep(0)  # yield so concurrent missers pile up
        return "value"

    async def scenario() -> list[str]:
        flight: SingleFlight[str, str] = SingleFlight()
        return list(
            await asyncio.gather(
                flight.do("k", fetch),
                flight.do("k", fetch),
                flight.do("k", fetch),
            )
        )

    results = asyncio.run(scenario())

    assert results == ["value", "value", "value"]
    assert calls == 1


def test_distinct_keys_fetch_independently() -> None:
    seen: list[str] = []

    def make_fetch(value: str):
        async def fetch() -> str:
            seen.append(value)
            return value

        return fetch

    async def scenario() -> tuple[str, str]:
        flight: SingleFlight[str, str] = SingleFlight()
        a = await flight.do("a", make_fetch("a"))
        b = await flight.do("b", make_fetch("b"))
        return a, b

    a, b = asyncio.run(scenario())

    assert (a, b) == ("a", "b")
    assert sorted(seen) == ["a", "b"]


def test_repeat_lookup_does_not_refetch() -> None:
    calls = 0

    async def fetch() -> str:
        nonlocal calls
        calls += 1
        return "value"

    async def scenario() -> None:
        flight: SingleFlight[str, str] = SingleFlight()
        assert await flight.do("k", fetch) == "value"
        assert await flight.do("k", fetch) == "value"

    asyncio.run(scenario())

    assert calls == 1


def test_failed_fetch_does_not_poison_retry() -> None:
    attempts = 0

    async def fetch() -> str:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("boom")
        return "value"

    async def scenario() -> str:
        flight: SingleFlight[str, str] = SingleFlight()
        try:
            await flight.do("k", fetch)
        except RuntimeError:
            pass
        return await flight.do("k", fetch)

    result = asyncio.run(scenario())

    assert result == "value"
    assert attempts == 2
