from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class Vec2:
    x: float
    y: float

    def distance_to(self, other: "Vec2") -> float:
        return math.hypot(self.x - other.x, self.y - other.y)

    def direction_to(self, other: "Vec2") -> "Vec2":
        dx = other.x - self.x
        dy = other.y - self.y
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            return Vec2(0.0, 0.0)
        return Vec2(dx / length, dy / length)

    def moved_toward(self, other: "Vec2", distance: float) -> "Vec2":
        total = self.distance_to(other)
        if total <= 1e-9 or distance >= total:
            return other
        unit = self.direction_to(other)
        return Vec2(self.x + unit.x * distance, self.y + unit.y * distance)

    def add(self, dx: float, dy: float) -> "Vec2":
        return Vec2(self.x + dx, self.y + dy)

    def clamp(self, min_x: float, min_y: float, max_x: float, max_y: float) -> "Vec2":
        return Vec2(
            min(max(self.x, min_x), max_x),
            min(max(self.y, min_y), max_y),
        )


def seconds_to_ticks(seconds: float, ticks_per_second: int) -> int:
    return max(1, int(round(seconds * ticks_per_second)))


def tiles_per_minute_to_tiles_per_tick(speed: float, ticks_per_second: int) -> float:
    return speed / 60.0 / float(ticks_per_second)

