"""Event-shape generators.

Every event matches the Avro schema registered at Mock GAP:
  - analytics / webrtc-analytics: meetingId, zoneId, eventType, timestamp, userId, payload
  - engagement-topic:             meetingId, zone_id, eventType, timestamp, value
"""
from __future__ import annotations

import random
import time
from typing import Any

from .entities import Meeting, User

ANALYTICS_TYPES = ("user_join", "user_leave")
WEBRTC_TYPES = ("mic_toggle", "cam_toggle", "network_quality")
ENGAGEMENT_TYPES = ("speech_time", "attention_score")
MID_STREAM_TYPES = WEBRTC_TYPES + ANALYTICS_TYPES + ENGAGEMENT_TYPES


def _now_ms() -> int:
    return int(time.time() * 1000)


def meeting_start(meeting: Meeting) -> dict[str, Any]:
    return {
        "meetingId": meeting.meeting_id,
        "zoneId": meeting.zone_id,
        "eventType": "meeting_start",
        "timestamp": meeting.started_at_ms,
        "userId": meeting.teacher.user_id,
        "payload": {
            "teacherId": meeting.teacher.user_id,
            "studentCount": str(len(meeting.students)),
        },
    }


def meeting_end(meeting: Meeting) -> dict[str, Any]:
    return {
        "meetingId": meeting.meeting_id,
        "zoneId": meeting.zone_id,
        "eventType": "meeting_end",
        "timestamp": _now_ms(),
        "userId": meeting.teacher.user_id,
        "payload": {
            "durationMs": str(_now_ms() - meeting.started_at_ms),
            "participantCount": str(len(meeting.participants)),
        },
    }


def _analytics(meeting: Meeting, user: User, event_type: str) -> dict[str, Any]:
    return {
        "meetingId": meeting.meeting_id,
        "zoneId": meeting.zone_id,
        "eventType": event_type,
        "timestamp": _now_ms(),
        "userId": user.user_id,
        "payload": {"role": user.role},
    }


def _webrtc(meeting: Meeting, user: User, event_type: str) -> dict[str, Any]:
    payload: dict[str, str]
    if event_type == "network_quality":
        payload = {
            "rttMs": str(random.randint(20, 300)),
            "packetLoss": f"{random.uniform(0, 0.05):.4f}",
        }
    else:
        payload = {"state": random.choice(["on", "off"])}
    return {
        "meetingId": meeting.meeting_id,
        "zoneId": meeting.zone_id,
        "eventType": event_type,
        "timestamp": _now_ms(),
        "userId": user.user_id,
        "payload": payload,
    }


def _engagement(meeting: Meeting, user: User, event_type: str) -> dict[str, Any]:
    if event_type == "speech_time":
        value = random.uniform(0.1, 5.0)  # seconds of speech in this window
    else:
        value = random.uniform(0.0, 1.0)  # attention score
    return {
        "meetingId": meeting.meeting_id,
        "zone_id": meeting.zone_id,  # snake_case matches engagement schema
        "eventType": event_type,
        "timestamp": _now_ms(),
        "value": value,
    }


def random_mid_stream(meeting: Meeting) -> dict[str, Any]:
    event_type = random.choice(MID_STREAM_TYPES)
    user = meeting.random_participant()
    if event_type in ANALYTICS_TYPES:
        return _analytics(meeting, user, event_type)
    if event_type in WEBRTC_TYPES:
        return _webrtc(meeting, user, event_type)
    return _engagement(meeting, user, event_type)
