"""Main simulator loop.

Concurrency model:
  - N async "meeting" tasks run in parallel.
  - Each meeting emits events at a Poisson rate (`events_per_second / N`).
  - When a meeting ends, it emits `meeting_end` and a fresh one spins up.

Every event is POSTed to Mock GAP's /api/gap/ingest — the whole point is
to exercise the real HTTP→Kafka→Avro path, not bypass it.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import signal
import time

import httpx

from .config import Config
from .entities import Meeting, pick_zone
from .events import meeting_end, meeting_start, random_mid_stream

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger("simulator")


class Stats:
    __slots__ = ("sent", "failed", "started_at")

    def __init__(self) -> None:
        self.sent = 0
        self.failed = 0
        self.started_at = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.started_at


async def post_event(
    client: httpx.AsyncClient,
    gap_url: str,
    event: dict,
    stats: Stats,
) -> None:
    try:
        resp = await client.post(
            f"{gap_url}/api/gap/ingest",
            json=event,
            timeout=5.0,
        )
        if resp.status_code >= 300:
            stats.failed += 1
            log.warning("ingest failed status=%d body=%s", resp.status_code, resp.text[:200])
            return
        stats.sent += 1
    except Exception as exc:
        stats.failed += 1
        log.warning("ingest error: %s", exc)


async def run_meeting(
    cfg: Config,
    client: httpx.AsyncClient,
    stats: Stats,
    stop: asyncio.Event,
) -> None:
    """Run a single meeting end-to-end, then loop with a fresh meeting."""
    per_meeting_eps = cfg.events_per_second / max(cfg.concurrent_meetings, 1)
    mean_gap = 1.0 / per_meeting_eps if per_meeting_eps > 0 else 1.0

    while not stop.is_set():
        now_ms = int(time.time() * 1000)
        meeting = Meeting.new(
            zone_id=pick_zone(cfg.zone_weights),
            now_ms=now_ms,
            duration_ms=cfg.meeting_duration_seconds * 1000,
            student_count=cfg.students_per_meeting,
        )
        log.info(
            "meeting started id=%s zone=%d students=%d",
            meeting.meeting_id, meeting.zone_id, len(meeting.students),
        )
        await post_event(client, cfg.gap_url, meeting_start(meeting), stats)

        while not stop.is_set() and time.time() * 1000 < meeting.ends_at_ms:
            # Poisson arrivals: inter-arrival times are exponential.
            await asyncio.sleep(random.expovariate(1.0 / mean_gap))
            if stop.is_set():
                break
            await post_event(client, cfg.gap_url, random_mid_stream(meeting), stats)

        await post_event(client, cfg.gap_url, meeting_end(meeting), stats)
        log.info("meeting ended id=%s", meeting.meeting_id)


async def report_stats(stats: Stats, stop: asyncio.Event) -> None:
    prev_sent = 0
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), timeout=10.0)
        except asyncio.TimeoutError:
            pass
        rate = (stats.sent - prev_sent) / 10.0
        log.info(
            "stats sent=%d failed=%d rate_eps=%.1f elapsed=%.1fs",
            stats.sent, stats.failed, rate, stats.elapsed(),
        )
        prev_sent = stats.sent


async def main() -> None:
    cfg = Config.from_env()
    log.info("starting simulator cfg=%s", cfg)

    stop = asyncio.Event()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    stats = Stats()

    async with httpx.AsyncClient() as client:
        tasks = [
            asyncio.create_task(run_meeting(cfg, client, stats, stop))
            for _ in range(cfg.concurrent_meetings)
        ]
        tasks.append(asyncio.create_task(report_stats(stats, stop)))

        if cfg.duration_seconds > 0:
            async def _timer() -> None:
                await asyncio.sleep(cfg.duration_seconds)
                stop.set()
            tasks.append(asyncio.create_task(_timer()))

        await stop.wait()
        log.info("shutdown requested, draining…")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    log.info("final stats sent=%d failed=%d", stats.sent, stats.failed)


if __name__ == "__main__":
    asyncio.run(main())
