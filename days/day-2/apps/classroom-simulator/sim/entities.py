"""Domain entities for the classroom simulator."""
from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class User:
    user_id: str
    role: str  # "teacher" | "student"


@dataclass
class Meeting:
    meeting_id: str
    zone_id: int
    teacher: User
    students: list[User]
    started_at_ms: int
    ends_at_ms: int
    ended: bool = False

    @classmethod
    def new(
        cls,
        zone_id: int,
        now_ms: int,
        duration_ms: int,
        student_count: int,
    ) -> "Meeting":
        meeting_id = f"m-{uuid.uuid4().hex[:10]}"
        teacher = User(user_id=f"t-{uuid.uuid4().hex[:8]}", role="teacher")
        students = [
            User(user_id=f"s-{uuid.uuid4().hex[:8]}", role="student")
            for _ in range(student_count)
        ]
        return cls(
            meeting_id=meeting_id,
            zone_id=zone_id,
            teacher=teacher,
            students=students,
            started_at_ms=now_ms,
            ends_at_ms=now_ms + duration_ms,
        )

    @property
    def participants(self) -> list[User]:
        return [self.teacher, *self.students]

    def random_participant(self) -> User:
        return random.choice(self.participants)


def pick_zone(weights: tuple[float, ...]) -> int:
    """Weighted pick — returns 1-indexed zoneId."""
    return random.choices(range(1, 6), weights=weights, k=1)[0]
