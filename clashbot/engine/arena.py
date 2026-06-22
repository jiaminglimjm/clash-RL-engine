from __future__ import annotations

import math
from typing import Dict, Tuple

from .constants import ARENA_COLS, ARENA_ROWS, SIDE_BLUE, SIDE_RED
from .geometry import Vec2


NORMAL = "normal"
RIVER = "river"
BANNED = "banned"
PRINCESS = "princess"
CROWN = "crown"

BRIDGE_CENTERS = (3.5, 14.5)
BRIDGE_HALF_WIDTH = 1.0
BRIDGE_TOP = 15.0
BRIDGE_BOTTOM = 17.0


def tile_type(row: int, col: int) -> str:
    if row < 0 or row >= ARENA_ROWS or col < 0 or col >= ARENA_COLS:
        return BANNED

    kind = NORMAL
    true_i = abs(15.5 - row) - 0.5
    true_j = abs(8.5 - col) - 0.5

    if true_i == 15 and (col < 6 or col > 11):
        kind = BANNED

    if 10 < true_i < 15 and true_j < 2:
        kind = CROWN

    if 7 < true_i < 11 and 3 < true_j < 7:
        kind = PRINCESS

    if true_i < 1:
        if true_j < 5 or true_j > 5:
            kind = RIVER

    return kind


def is_bridge_tile(row: int, col: int) -> bool:
    return row in (15, 16) and col in (3, 14)


def is_bridge_world(pos: Vec2) -> bool:
    if pos.y < BRIDGE_TOP or pos.y >= BRIDGE_BOTTOM:
        return False
    return any(abs(pos.x - center) <= BRIDGE_HALF_WIDTH for center in BRIDGE_CENTERS)


def bridge_rects_snapshot():
    return [
        {
            "x": center - BRIDGE_HALF_WIDTH,
            "y": BRIDGE_TOP,
            "w": BRIDGE_HALF_WIDTH * 2.0,
            "h": BRIDGE_BOTTOM - BRIDGE_TOP,
        }
        for center in BRIDGE_CENTERS
    ]


def tile_for_world(pos: Vec2) -> Tuple[int, int]:
    return int(math.floor(pos.y)), int(math.floor(pos.x))


def lane_for_x(x: float) -> str:
    return "left" if x < 9.0 else "right"


def default_tower_positions() -> Dict[str, Dict[str, Vec2]]:
    return {
        SIDE_BLUE: {
            "king": Vec2(9.0, 29.0),
            "left_princess": Vec2(3.5, 25.5),
            "right_princess": Vec2(14.5, 25.5),
        },
        SIDE_RED: {
            "king": Vec2(9.0, 3.0),
            "left_princess": Vec2(3.5, 6.5),
            "right_princess": Vec2(14.5, 6.5),
        },
    }


def placement_allowed(
    side: str,
    card_kind: str,
    pos: Vec2,
    blue_princess_alive: Tuple[bool, bool],
    red_princess_alive: Tuple[bool, bool],
) -> bool:
    row, col = tile_for_world(pos)
    kind = tile_type(row, col)
    if kind == BANNED:
        return False

    if card_kind == "spell":
        return True

    placeable = kind not in (RIVER, PRINCESS, CROWN)

    if side == SIDE_BLUE:
        own_left_alive, own_right_alive = blue_princess_alive
        enemy_left_alive, enemy_right_alive = red_princess_alive
        if kind == PRINCESS and col < 9 and not own_left_alive:
            placeable = True
        if kind == PRINCESS and col > 8 and not own_right_alive:
            placeable = True
        if row < 11:
            placeable = False
        if enemy_left_alive and row < 17 and col < 9:
            placeable = False
        if enemy_right_alive and row < 17 and col > 8:
            placeable = False
        return placeable

    if side == SIDE_RED:
        own_left_alive, own_right_alive = red_princess_alive
        enemy_left_alive, enemy_right_alive = blue_princess_alive
        if kind == PRINCESS and col < 9 and not own_left_alive:
            placeable = True
        if kind == PRINCESS and col > 8 and not own_right_alive:
            placeable = True
        if row > 20:
            placeable = False
        if enemy_left_alive and row > 14 and col < 9:
            placeable = False
        if enemy_right_alive and row > 14 and col > 8:
            placeable = False
        return placeable

    return False


def arena_tiles_snapshot():
    return [
        {"row": row, "col": col, "type": tile_type(row, col), "bridge": is_bridge_tile(row, col)}
        for row in range(ARENA_ROWS)
        for col in range(ARENA_COLS)
    ]
