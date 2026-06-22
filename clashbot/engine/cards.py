from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .constants import TICKS_PER_SECOND
from .geometry import Vec2, seconds_to_ticks


@dataclass(frozen=True)
class UnitSpec:
    unit_id: str
    label: str
    kind: str
    movement_type: str
    target_mode: str
    hp: int
    damage: int
    speed_tiles_per_minute: float
    attack_range: float
    sight_range: float
    hit_speed_ticks: int
    deploy_ticks: int
    mass: float
    radius: float
    projectile_speed_tiles_per_minute: float = 0.0
    splash_radius: float = 0.0
    footprint_tiles: float = 0.0
    lifetime_ticks: Optional[int] = None
    mechanics: Tuple[str, ...] = ()


@dataclass(frozen=True)
class SpellSpec:
    spell_id: str
    damage: int
    crown_tower_damage: int
    radius: float
    projectile_speed_tiles_per_minute: float
    knockback_tiles: float = 0.0
    mechanics: Tuple[str, ...] = ()


@dataclass(frozen=True)
class CardSpec:
    card_id: str
    display_name: str
    source_name: str
    kind: str
    elixir: int
    secondary_color: str
    units: Tuple[UnitSpec, ...] = ()
    formation: Tuple[Vec2, ...] = (Vec2(0.0, 0.0),)
    spell: Optional[SpellSpec] = None
    source_note: str = "Retro Royale workbook, Level 11 public stats plus hidden/community-derived fields."


def _ticks(seconds: float) -> int:
    return seconds_to_ticks(seconds, TICKS_PER_SECOND)


CARD_SPECS: Dict[str, CardSpec] = {
    "knight": CardSpec(
        card_id="knight",
        display_name="Knight",
        source_name="Knight",
        kind="troop",
        elixir=3,
        secondary_color="#8f9399",
        units=(
            UnitSpec(
                unit_id="knight",
                label="Knight",
                kind="troop",
                movement_type="ground",
                target_mode="ground",
                hp=1766,
                damage=202,
                speed_tiles_per_minute=60,
                attack_range=1.2,
                sight_range=5.5,
                hit_speed_ticks=_ticks(1.2),
                deploy_ticks=_ticks(1.0),
                mass=6,
                radius=0.5,
            ),
        ),
    ),
    "archers": CardSpec(
        card_id="archers",
        display_name="Archers",
        source_name="Archers",
        kind="troop",
        elixir=3,
        secondary_color="#f2a5c8",
        units=(
            UnitSpec(
                unit_id="archer",
                label="Archer",
                kind="troop",
                movement_type="ground",
                target_mode="all",
                hp=304,
                damage=112,
                speed_tiles_per_minute=60,
                attack_range=5.0,
                sight_range=5.5,
                hit_speed_ticks=_ticks(0.9),
                deploy_ticks=_ticks(1.0),
                mass=3,
                radius=0.5,
                projectile_speed_tiles_per_minute=600,
            ),
        ),
        formation=(Vec2(-0.45, 0.0), Vec2(0.45, 0.0)),
    ),
    "minions": CardSpec(
        card_id="minions",
        display_name="Minions",
        source_name="Minions",
        kind="troop",
        elixir=3,
        secondary_color="#2d8bea",
        units=(
            UnitSpec(
                unit_id="minion",
                label="Minion",
                kind="troop",
                movement_type="air",
                target_mode="all",
                hp=230,
                damage=107,
                speed_tiles_per_minute=90,
                attack_range=2.5,
                sight_range=5.5,
                hit_speed_ticks=_ticks(1.0),
                deploy_ticks=_ticks(1.0),
                mass=2,
                radius=0.5,
                projectile_speed_tiles_per_minute=1000,
            ),
        ),
        formation=(Vec2(0.0, -0.35), Vec2(-0.55, 0.45), Vec2(0.55, 0.45)),
    ),
    "fireball": CardSpec(
        card_id="fireball",
        display_name="Fireball",
        source_name="Fireball",
        kind="spell",
        elixir=4,
        secondary_color="#f47a23",
        spell=SpellSpec(
            spell_id="fireball",
            damage=688,
            crown_tower_damage=207,
            radius=2.5,
            projectile_speed_tiles_per_minute=600,
            knockback_tiles=1.8,
            mechanics=("traveling_spell", "area_damage", "knockback"),
        ),
    ),
    "cannon": CardSpec(
        card_id="cannon",
        display_name="Cannon",
        source_name="Cannon",
        kind="building",
        elixir=3,
        secondary_color="#4a4d50",
        units=(
            UnitSpec(
                unit_id="cannon",
                label="Cannon",
                kind="building",
                movement_type="building",
                target_mode="ground",
                hp=824,
                damage=212,
                speed_tiles_per_minute=0,
                attack_range=5.5,
                sight_range=5.5,
                hit_speed_ticks=_ticks(1.0),
                deploy_ticks=_ticks(1.0),
                mass=0,
                radius=1.5,
                projectile_speed_tiles_per_minute=1000,
                footprint_tiles=3.0,
                lifetime_ticks=_ticks(30.0),
                mechanics=("lifetime",),
            ),
        ),
    ),
    "wizard": CardSpec(
        card_id="wizard",
        display_name="Wizard",
        source_name="Wizard",
        kind="troop",
        elixir=5,
        secondary_color="#d94d34",
        units=(
            UnitSpec(
                unit_id="wizard",
                label="Wizard",
                kind="troop",
                movement_type="ground",
                target_mode="all",
                hp=755,
                damage=281,
                speed_tiles_per_minute=60,
                attack_range=5.5,
                sight_range=5.5,
                hit_speed_ticks=_ticks(1.4),
                deploy_ticks=_ticks(1.0),
                mass=5,
                radius=0.5,
                projectile_speed_tiles_per_minute=600,
                splash_radius=1.5,
            ),
        ),
    ),
    "giant": CardSpec(
        card_id="giant",
        display_name="Giant",
        source_name="Giant",
        kind="troop",
        elixir=5,
        secondary_color="#8a5b35",
        units=(
            UnitSpec(
                unit_id="giant",
                label="Giant",
                kind="troop",
                movement_type="ground",
                target_mode="buildings",
                hp=4090,
                damage=253,
                speed_tiles_per_minute=45,
                attack_range=1.2,
                sight_range=7.5,
                hit_speed_ticks=_ticks(1.5),
                deploy_ticks=_ticks(1.0),
                mass=18,
                radius=0.75,
            ),
        ),
    ),
    "musketeer": CardSpec(
        card_id="musketeer",
        display_name="Musketeer",
        source_name="Musketeer",
        kind="troop",
        elixir=4,
        secondary_color="#8a50cc",
        units=(
            UnitSpec(
                unit_id="musketeer",
                label="Musk",
                kind="troop",
                movement_type="ground",
                target_mode="all",
                hp=721,
                damage=217,
                speed_tiles_per_minute=60,
                attack_range=6.0,
                sight_range=6.0,
                hit_speed_ticks=_ticks(1.0),
                deploy_ticks=_ticks(1.0),
                mass=5,
                radius=0.5,
                projectile_speed_tiles_per_minute=1000,
            ),
        ),
    ),
    "mini_pekka": CardSpec(
        card_id="mini_pekka",
        display_name="Mini P.E.K.K.A.",
        source_name="Mini P.E.K.K.A",
        kind="troop",
        elixir=4,
        secondary_color="#404348",
        units=(
            UnitSpec(
                unit_id="mini_pekka",
                label="Mini P",
                kind="troop",
                movement_type="ground",
                target_mode="ground",
                hp=1433,
                damage=755,
                speed_tiles_per_minute=90,
                attack_range=0.8,
                sight_range=5.5,
                hit_speed_ticks=_ticks(1.6),
                deploy_ticks=_ticks(1.0),
                mass=4,
                radius=0.45,
            ),
        ),
    ),
}

DEFAULT_DECK = (
    "knight",
    "archers",
    "minions",
    "fireball",
    "cannon",
    "giant",
    "musketeer",
    "mini_pekka",
)

TOWER_SPECS: Dict[str, UnitSpec] = {
    "king": UnitSpec(
        unit_id="king_tower",
        label="King",
        kind="tower",
        movement_type="building",
        target_mode="all",
        hp=4824,
        damage=109,
        speed_tiles_per_minute=0,
        attack_range=9.0,
        sight_range=9.0,
        hit_speed_ticks=_ticks(1.0),
        deploy_ticks=0,
        mass=0,
        radius=1.4,
        projectile_speed_tiles_per_minute=1000,
    ),
    "princess": UnitSpec(
        unit_id="princess_tower",
        label="Tower",
        kind="tower",
        movement_type="building",
        target_mode="all",
        hp=3052,
        damage=109,
        speed_tiles_per_minute=0,
        attack_range=7.5,
        sight_range=7.5,
        hit_speed_ticks=_ticks(0.8),
        deploy_ticks=0,
        mass=0,
        radius=1.0,
        projectile_speed_tiles_per_minute=1000,
    ),
}
