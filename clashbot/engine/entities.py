from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .constants import (
    ELIXIR_MILLI,
    MAX_ELIXIR_MILLI,
    NORMAL_ELIXIR_TICKS,
    SIDE_BLUE,
    SIDE_RED,
    START_ELIXIR_MILLI,
)
from .geometry import Vec2


@dataclass
class Entity:
    entity_id: int
    side: str
    card_id: str
    unit_id: str
    label: str
    kind: str
    movement_type: str
    target_mode: str
    pos: Vec2
    hp: int
    max_hp: int
    damage: int
    speed_tiles_per_minute: float
    attack_range: float
    sight_range: float
    hit_speed_ticks: int
    deploy_ticks_remaining: int
    mass: float
    radius: float
    secondary_color: str
    projectile_speed_tiles_per_minute: float = 0.0
    splash_radius: float = 0.0
    footprint_tiles: float = 0.0
    lifetime_ticks_remaining: Optional[int] = None
    lifetime_ticks_total: Optional[int] = None
    lifetime_decay_remainder: int = 0
    target_id: Optional[int] = None
    target_locked: bool = False
    attack_cooldown_ticks: int = 0
    created_tick: int = 0
    tower_role: Optional[str] = None
    facing_x: float = 0.0
    facing_y: float = -1.0
    last_hit_tick: Optional[int] = None

    @property
    def alive(self) -> bool:
        return self.hp > 0

    @property
    def deployed(self) -> bool:
        return self.deploy_ticks_remaining <= 0

    @property
    def is_air(self) -> bool:
        return self.movement_type == "air"

    @property
    def is_building_like(self) -> bool:
        return self.kind in ("building", "tower")

    @property
    def footprint(self) -> float:
        return self.footprint_tiles if self.footprint_tiles > 0 else self.radius * 2.0

    @property
    def can_move(self) -> bool:
        return self.speed_tiles_per_minute > 0 and self.movement_type in ("ground", "air")

    def effective_distance_to(self, other: "Entity") -> float:
        return max(0.0, self.pos.distance_to(other.pos) - other.radius)


@dataclass
class Projectile:
    projectile_id: int
    side: str
    source_card_id: str
    label: str
    pos: Vec2
    damage: int
    speed_tiles_per_minute: float
    target_id: Optional[int] = None
    target_pos: Optional[Vec2] = None
    splash_radius: float = 0.0
    crown_tower_damage: Optional[int] = None
    knockback_tiles: float = 0.0
    radius: float = 0.12
    ttl_ticks: int = 180

    @property
    def is_spell(self) -> bool:
        return self.target_pos is not None and self.target_id is None


@dataclass
class PlayerState:
    side: str
    deck: Tuple[str, ...]
    order: List[int] = field(default_factory=list)
    elixir_milli: int = START_ELIXIR_MILLI
    elixir_remainder: int = 0
    next_sequence: int = 1

    def __post_init__(self) -> None:
        if not self.order:
            self.order = list(range(len(self.deck)))

    def hand(self) -> List[str]:
        return [self.deck[index] for index in self.order[:4]]

    def can_pay(self, elixir: int) -> bool:
        return self.elixir_milli >= elixir * ELIXIR_MILLI

    def spend(self, elixir: int) -> None:
        self.elixir_milli -= elixir * ELIXIR_MILLI

    def cycle_slot(self, hand_slot: int) -> None:
        played_index = self.order[hand_slot]
        if len(self.order) <= 4:
            return
        replacement_index = self.order.pop(4)
        self.order[hand_slot] = replacement_index
        self.order.append(played_index)

    def regen_tick(self, multiplier: int = 1) -> None:
        if self.elixir_milli >= MAX_ELIXIR_MILLI:
            self.elixir_milli = MAX_ELIXIR_MILLI
            self.elixir_remainder = 0
            return
        self.elixir_remainder += ELIXIR_MILLI * multiplier
        gained, self.elixir_remainder = divmod(self.elixir_remainder, NORMAL_ELIXIR_TICKS)
        self.elixir_milli = min(MAX_ELIXIR_MILLI, self.elixir_milli + gained)

    def allocate_sequence(self) -> int:
        sequence = self.next_sequence
        self.next_sequence += 1
        return sequence


@dataclass(order=True)
class ScheduledCommand:
    execute_tick: int
    sequence: int
    side: str = field(compare=False)
    hand_slot: int = field(compare=False)
    x: float = field(compare=False)
    y: float = field(compare=False)
    card_id: Optional[str] = field(default=None, compare=False)
    client_tick: Optional[int] = field(default=None, compare=False)
    receive_tick: int = field(default=0, compare=False)

    def compact_tuple(self):
        side_code = 0 if self.side == SIDE_BLUE else 1
        return [self.execute_tick, side_code, self.hand_slot, int(round(self.x * 100)), int(round(self.y * 100)), self.sequence]


def side_from_code(code: int) -> str:
    return SIDE_BLUE if code == 0 else SIDE_RED


def players_snapshot(players: Dict[str, PlayerState], card_names: Dict[str, str]):
    result = {}
    for side in (SIDE_BLUE, SIDE_RED):
        player = players[side]
        result[side] = {
            "deck": list(player.deck),
            "hand": [
                {"slot": i, "cardId": card_id, "name": card_names[card_id]}
                for i, card_id in enumerate(player.hand())
            ],
            "elixir": round(player.elixir_milli / float(ELIXIR_MILLI), 3),
            "elixirMilli": player.elixir_milli,
        }
    return result
