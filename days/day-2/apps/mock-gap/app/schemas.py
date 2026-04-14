"""Avro schemas and eventType → topic routing.

Subject naming follows Confluent's TopicNameStrategy: `<topic>-value`.
Field casing (zoneId vs zone_id) is intentional and mirrors production.
"""
from __future__ import annotations

ANALYTICS_SCHEMA = {
    "type": "record",
    "name": "AnalyticsEvent",
    "namespace": "com.connect.analytics",
    "fields": [
        {"name": "meetingId", "type": "string"},
        {"name": "zoneId", "type": "int"},
        {"name": "eventType", "type": "string"},
        {"name": "timestamp", "type": "long"},
        {"name": "userId", "type": "string"},
        {"name": "payload", "type": {"type": "map", "values": "string"}},
    ],
}

ENGAGEMENT_SCHEMA = {
    "type": "record",
    "name": "EngagementEvent",
    "namespace": "com.connect.analytics",
    "fields": [
        {"name": "meetingId", "type": "string"},
        {"name": "zone_id", "type": "int"},
        {"name": "eventType", "type": "string"},
        {"name": "timestamp", "type": "long"},
        {"name": "value", "type": "double"},
    ],
}

WEBRTC_SCHEMA = {
    "type": "record",
    "name": "WebRTCAnalyticsEvent",
    "namespace": "com.connect.analytics",
    "fields": [
        {"name": "meetingId", "type": "string"},
        {"name": "zoneId", "type": "int"},
        {"name": "eventType", "type": "string"},
        {"name": "timestamp", "type": "long"},
        {"name": "userId", "type": "string"},
        {"name": "payload", "type": {"type": "map", "values": "string"}},
    ],
}

# eventType → (topic, schema). Engagement uses snake_case field `zone_id`.
# Everything else routes to analytics/webrtc which use `zoneId`.
EVENT_ROUTING: dict[str, tuple[str, dict]] = {
    # analytics topic
    "meeting_start": ("analytics", ANALYTICS_SCHEMA),
    "meeting_end": ("analytics", ANALYTICS_SCHEMA),
    "user_join": ("analytics", ANALYTICS_SCHEMA),
    "user_leave": ("analytics", ANALYTICS_SCHEMA),
    # webrtc-analytics topic
    "mic_toggle": ("webrtc-analytics", WEBRTC_SCHEMA),
    "cam_toggle": ("webrtc-analytics", WEBRTC_SCHEMA),
    "network_quality": ("webrtc-analytics", WEBRTC_SCHEMA),
    # engagement-topic
    "speech_time": ("engagement-topic", ENGAGEMENT_SCHEMA),
    "attention_score": ("engagement-topic", ENGAGEMENT_SCHEMA),
}

TOPICS = ("analytics", "engagement-topic", "webrtc-analytics")


def route(event_type: str) -> tuple[str, dict]:
    if event_type not in EVENT_ROUTING:
        raise ValueError(f"unknown eventType: {event_type}")
    return EVENT_ROUTING[event_type]


def subject_for(topic: str) -> str:
    return f"{topic}-value"
