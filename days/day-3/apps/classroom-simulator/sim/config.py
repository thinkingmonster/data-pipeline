"""Simulator configuration — env-driven.

Defaults model a lab-scale workload: ~10 events/sec across 3 concurrent
meetings, 5 zones with realistic skew (zone 1 busiest, zone 5 quietest).
"""
from __future__ import annotations

import os
from dataclasses import dataclass


def _env_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


@dataclass(frozen=True)
class Config:
    gap_url: str
    events_per_second: float
    concurrent_meetings: int
    meeting_duration_seconds: int
    students_per_meeting: int
    duration_seconds: int  # 0 = run forever
    zone_weights: tuple[float, ...]  # len == 5

    @classmethod
    def from_env(cls) -> "Config":
        weights_raw = os.getenv("ZONE_WEIGHTS", "0.5,0.25,0.15,0.07,0.03")
        zone_weights = tuple(float(w) for w in weights_raw.split(","))
        if len(zone_weights) != 5:
            raise ValueError("ZONE_WEIGHTS must have exactly 5 values")

        return cls(
            gap_url=os.getenv("GAP_URL", "http://mock-gap:8000"),
            events_per_second=_env_float("EVENTS_PER_SECOND", 10.0),
            concurrent_meetings=_env_int("CONCURRENT_MEETINGS", 3),
            meeting_duration_seconds=_env_int("MEETING_DURATION_SECONDS", 120),
            students_per_meeting=_env_int("STUDENTS_PER_MEETING", 5),
            duration_seconds=_env_int("DURATION_SECONDS", 0),
            zone_weights=zone_weights,
        )
