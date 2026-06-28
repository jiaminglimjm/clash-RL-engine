from __future__ import annotations

from dataclasses import dataclass, field, replace
import hashlib
import heapq
import json
import math
import random
import secrets
from typing import Dict, Iterable, List, Optional, Tuple

from .arena import (
    arena_tiles_snapshot,
    bridge_rects_snapshot,
    default_tower_positions,
    is_bridge_world,
    lane_for_x,
    placement_allowed,
    tile_for_world,
    tile_type,
    RIVER,
)
from .cards import CARD_SPECS, DEFAULT_DECK, TOWER_SPECS, CardSpec, SpawnSpec, SpellSpec, UnitSpec
from .constants import (
    ARENA_COLS,
    ARENA_ROWS,
    DOUBLE_ELIXIR_START_TICKS,
    ENGINE_VERSION,
    KING_ACTIVATION_DELAY_TICKS,
    MATCH_END_TICKS,
    MELEE_ATTACK_RANGE_FACTOR,
    MELEE_ATTACK_RANGE_MAX_TILES,
    MULTI_UNIT_SPAWN_STAGGER_TICKS,
    SUDDEN_DEATH_START_TICKS,
    SIDE_BLUE,
    SIDE_RED,
    SIDES,
    TICKS_PER_SECOND,
    TRIPLE_ELIXIR_START_TICKS,
    TROOP_MOVEMENT_SPEED_FACTOR,
)
from .entities import Entity, PlayerState, Projectile, ScheduledCommand, players_snapshot
from .geometry import Vec2, tiles_per_minute_to_tiles_per_tick
from .replay import CompactReplay, build_replay


BUILDING_PLACEMENT_TILES = 3.0
GROUND_SEGMENT_SAMPLE_TILES = 0.2
BUILDING_AVOIDANCE_MARGIN = 0.15


@dataclass(order=True)
class ScheduledSpawn:
    execute_tick: int
    sequence: int
    side: str = field(compare=False)
    card_id: str = field(compare=False)
    unit_spec: UnitSpec = field(compare=False)
    pos: Vec2 = field(compare=False)
    secondary_color: str = field(compare=False)


@dataclass(frozen=True)
class GameOptions:
    simulated_network_latency_ticks: int = 0
    placement_delay_ticks: int = 2
    lockstep_delay_ticks: int = 0
    elixir_regen_multiplier: int = 1
    max_event_log: int = 80
    shuffle_initial_hands: bool = True


class GameEngine:
    def __init__(
        self,
        blue_deck: Iterable[str] = DEFAULT_DECK,
        red_deck: Iterable[str] = DEFAULT_DECK,
        options: Optional[GameOptions] = None,
        seed: Optional[int] = None,
    ) -> None:
        self.options = options or GameOptions()
        self.seed = secrets.randbits(63) if seed is None else seed
        self.tick = 0
        self._next_entity_id = 1
        self._next_projectile_id = 1
        self._next_spawn_sequence = 1
        blue_deck_tuple = tuple(blue_deck)
        red_deck_tuple = tuple(red_deck)
        self.players: Dict[str, PlayerState] = {
            SIDE_BLUE: PlayerState(
                SIDE_BLUE,
                blue_deck_tuple,
                order=self._initial_deck_order(SIDE_BLUE, blue_deck_tuple),
            ),
            SIDE_RED: PlayerState(
                SIDE_RED,
                red_deck_tuple,
                order=self._initial_deck_order(SIDE_RED, red_deck_tuple),
            ),
        }
        self.entities: Dict[int, Entity] = {}
        self.projectiles: Dict[int, Projectile] = {}
        self.pending_commands: List[ScheduledCommand] = []
        self.pending_spawns: List[ScheduledSpawn] = []
        self.accepted_commands: List[ScheduledCommand] = []
        self.event_log: List[str] = []
        self.king_activated: Dict[str, bool] = {SIDE_BLUE: False, SIDE_RED: False}
        self.king_activation_started_tick: Dict[str, Optional[int]] = {SIDE_BLUE: None, SIDE_RED: None}
        self.game_over = False
        self.winner: Optional[str] = None
        self.ended_tick: Optional[int] = None
        self.end_reason: Optional[str] = None
        self._spawn_initial_towers()

    def _initial_deck_order(self, side: str, deck: Tuple[str, ...]) -> List[int]:
        order = list(range(len(deck)))
        if not self.options.shuffle_initial_hands or len(order) <= 1:
            return order
        seed_material = "%s:%s:%s" % (self.seed, side, ",".join(deck))
        seed_bytes = hashlib.sha256(seed_material.encode("utf-8")).digest()
        rng = random.Random(int.from_bytes(seed_bytes[:16], "big"))
        rng.shuffle(order)
        return order

    @classmethod
    def from_replay(cls, replay: CompactReplay, options: Optional[GameOptions] = None) -> "GameEngine":
        return cls(
            blue_deck=replay.blue_deck or DEFAULT_DECK,
            red_deck=replay.red_deck or DEFAULT_DECK,
            options=options or GameOptions(placement_delay_ticks=0),
            seed=replay.seed,
        )

    def export_replay(self) -> Dict:
        return build_replay(
            self.players[SIDE_BLUE].deck,
            self.players[SIDE_RED].deck,
            self.accepted_commands,
            seed=self.seed,
            version=ENGINE_VERSION,
        ).to_dict()

    def run_replay_commands(self, replay: CompactReplay, until_tick: Optional[int] = None) -> None:
        commands = sorted(replay.commands)
        index = 0
        final_tick = until_tick
        if final_tick is None:
            final_tick = commands[-1].execute_tick + 1 if commands else self.tick
        while self.tick <= final_tick:
            while index < len(commands) and commands[index].execute_tick <= self.tick:
                self._execute_command(commands[index], record=False)
                index += 1
            if self.tick == final_tick:
                break
            self.step()

    def submit_play(
        self,
        side: str,
        hand_slot: int,
        x: float,
        y: float,
        client_tick: Optional[int] = None,
    ) -> ScheduledCommand:
        if side not in SIDES:
            raise ValueError("unknown side: %s" % side)
        if hand_slot < 0 or hand_slot > 3:
            raise ValueError("hand_slot must be 0..3")
        player = self.players[side]
        hand = player.hand()
        if hand_slot >= len(hand):
            raise ValueError("hand slot is not available")

        receive_tick = self.tick + max(0, self.options.simulated_network_latency_ticks)
        sync_tick = 0
        if client_tick is not None:
            sync_tick = client_tick + max(0, self.options.lockstep_delay_ticks)
        execute_tick = max(
            receive_tick + max(0, self.options.placement_delay_ticks),
            sync_tick,
        )
        command = ScheduledCommand(
            execute_tick=execute_tick,
            sequence=player.allocate_sequence(),
            side=side,
            hand_slot=hand_slot,
            x=x,
            y=y,
            card_id=hand[hand_slot],
            client_tick=client_tick,
            receive_tick=receive_tick,
        )
        heapq.heappush(self.pending_commands, command)
        self._log(
            "%s queued %s for tick %d"
            % (side, CARD_SPECS[command.card_id].display_name, command.execute_tick)
        )
        return command

    def step(self, ticks: int = 1) -> None:
        for _ in range(ticks):
            self._step_one()

    def _step_one(self) -> None:
        if self.game_over:
            return
        self._apply_due_commands()
        self._apply_due_spawns()
        self._regen_elixir()
        self._update_timers()
        self._update_spawners()
        self._update_king_activation()
        self._update_projectiles()
        self._cleanup_dead()
        self._update_entities()
        self._resolve_collisions()
        self._cleanup_dead()
        self.tick += 1
        self._check_match_clock()

    def can_place(self, side: str, card_id: str, pos: Vec2) -> bool:
        spec = CARD_SPECS[card_id]
        if not self._placement_tile_allowed(side, spec.kind, pos):
            return False
        if spec.kind == "building":
            return self._placement_area_allowed(side, spec.kind, pos, BUILDING_PLACEMENT_TILES)
        return True

    def _placement_tile_allowed(self, side: str, card_kind: str, pos: Vec2) -> bool:
        return placement_allowed(
            side,
            card_kind,
            pos,
            blue_princess_alive=self._princess_alive(SIDE_BLUE),
            red_princess_alive=self._princess_alive(SIDE_RED),
        )

    def _placement_area_allowed(self, side: str, card_kind: str, pos: Vec2, size_tiles: float) -> bool:
        half = size_tiles / 2.0
        min_row = int(math.floor(pos.y - half + 1e-9))
        max_row = int(math.ceil(pos.y + half - 1e-9)) - 1
        min_col = int(math.floor(pos.x - half + 1e-9))
        max_col = int(math.ceil(pos.x + half - 1e-9)) - 1
        for row in range(min_row, max_row + 1):
            for col in range(min_col, max_col + 1):
                if not self._placement_tile_allowed(side, card_kind, Vec2(col + 0.5, row + 0.5)):
                    return False
        return True

    def state_hash(self) -> str:
        material = {
            "tick": self.tick,
            "game": {
                "over": self.game_over,
                "winner": self.winner,
                "kingActivated": dict(self.king_activated),
                "kingActivationStartedTick": dict(self.king_activation_started_tick),
            },
            "players": {
                side: {
                    "order": list(self.players[side].order),
                    "elixir": self.players[side].elixir_milli,
                    "rem": self.players[side].elixir_remainder,
                    "lastPlayed": self.players[side].last_played_card_id,
                }
                for side in SIDES
            },
            "entities": [
                {
                    "id": entity.entity_id,
                    "side": entity.side,
                    "card": entity.card_id,
                    "unit": entity.unit_id,
                    "x": round(entity.pos.x, 5),
                    "y": round(entity.pos.y, 5),
                    "hp": entity.hp,
                    "target": entity.target_id,
                    "windupTarget": entity.attack_windup_target_id,
                    "cooldown": entity.attack_cooldown_ticks,
                    "deploy": entity.deploy_ticks_remaining,
                    "life": entity.lifetime_ticks_remaining,
                    "spawnCooldowns": list(entity.spawn_cooldowns),
                }
                for entity in self._entities_sorted()
            ],
            "pendingSpawns": [
                {
                    "tick": spawn.execute_tick,
                    "sequence": spawn.sequence,
                    "side": spawn.side,
                    "card": spawn.card_id,
                    "unit": spawn.unit_spec.unit_id,
                    "x": round(spawn.pos.x, 5),
                    "y": round(spawn.pos.y, 5),
                }
                for spawn in sorted(self.pending_spawns)
            ],
            "projectiles": [
                {
                    "id": projectile.projectile_id,
                    "side": projectile.side,
                    "card": projectile.source_card_id,
                    "x": round(projectile.pos.x, 5),
                    "y": round(projectile.pos.y, 5),
                    "target": projectile.target_id,
                    "targetPos": None
                    if projectile.target_pos is None
                    else [round(projectile.target_pos.x, 5), round(projectile.target_pos.y, 5)],
                    "ttl": projectile.ttl_ticks,
                    "delay": projectile.delay_ticks,
                    "done": projectile.effect_done,
                    "mechanics": list(projectile.mechanics),
                    "visualTargets": [
                        [round(point.x, 5), round(point.y, 5)]
                        for point in projectile.visual_targets
                    ],
                }
                for projectile in sorted(self.projectiles.values(), key=lambda item: item.projectile_id)
            ],
        }
        encoded = json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def snapshot(self) -> Dict:
        card_names = {card_id: spec.display_name for card_id, spec in CARD_SPECS.items()}
        return {
            "version": ENGINE_VERSION,
            "tick": self.tick,
            "seconds": round(self.tick / float(TICKS_PER_SECOND), 3),
            "arena": {
                "rows": ARENA_ROWS,
                "cols": ARENA_COLS,
                "tiles": arena_tiles_snapshot(),
                "bridges": bridge_rects_snapshot(),
            },
            "players": players_snapshot(self.players, card_names),
            "entities": [self._entity_snapshot(entity) for entity in self._entities_sorted()],
            "projectiles": [self._projectile_snapshot(projectile) for projectile in self.projectiles.values()],
            "pendingCommands": [
                {
                    "tick": command.execute_tick,
                    "side": command.side,
                    "handSlot": command.hand_slot,
                    "cardId": command.card_id,
                    "x": round(command.x, 3),
                    "y": round(command.y, 3),
                    "sequence": command.sequence,
                }
                for command in sorted(self.pending_commands)
            ],
            "pendingSpawns": [
                {
                    "tick": spawn.execute_tick,
                    "side": spawn.side,
                    "cardId": spawn.card_id,
                    "unitId": spawn.unit_spec.unit_id,
                    "x": round(spawn.pos.x, 3),
                    "y": round(spawn.pos.y, 3),
                    "sequence": spawn.sequence,
                }
                for spawn in sorted(self.pending_spawns)
            ],
            "logs": list(self.event_log),
            "cards": {
                card_id: {
                    "name": spec.display_name,
                    "elixir": spec.elixir,
                    "kind": spec.kind,
                    "secondaryColor": spec.secondary_color,
                    "spellRadius": spec.spell.radius if spec.spell is not None else 0,
                    "spellDamage": spec.spell.damage if spec.spell is not None else 0,
                    "spellCrownTowerDamage": spec.spell.crown_tower_damage if spec.spell is not None else 0,
                    "spellProjectileSpeed": (
                        spec.spell.projectile_speed_tiles_per_minute if spec.spell is not None else 0
                    ),
                    "spellKnockbackTiles": spec.spell.knockback_tiles if spec.spell is not None else 0,
                    "formation": [{"x": point.x, "y": point.y} for point in spec.formation],
                    "units": [
                        {
                            "radius": unit.radius,
                            "footprint": unit.footprint_tiles if unit.footprint_tiles > 0 else unit.radius * 2.0,
                            "kind": unit.kind,
                            "movementType": unit.movement_type,
                            "hitSpeedTicks": unit.hit_speed_ticks,
                            "firstAttackTicks": unit.first_attack_ticks,
                            "loadTimeTicks": unit.load_time_ticks,
                            "projectileSpeed": unit.projectile_speed_tiles_per_minute,
                            "splashRadius": unit.splash_radius,
                            "sightRange": unit.sight_range,
                            "mechanics": list(unit.mechanics),
                            "periodicSpawns": [self._spawn_spec_snapshot(spawn) for spawn in unit.periodic_spawns],
                            "deathSpawns": [self._spawn_spec_snapshot(spawn) for spawn in unit.death_spawns],
                        }
                        for unit in spec.units
                    ],
                }
                for card_id, spec in CARD_SPECS.items()
            },
            "net": {
                "simulatedLatencyTicks": self.options.simulated_network_latency_ticks,
                "placementDelayTicks": self.options.placement_delay_ticks,
                "lockstepDelayTicks": self.options.lockstep_delay_ticks,
            },
            "game": {
                "over": self.game_over,
                "winner": self.winner,
                "endedTick": self.ended_tick,
                "endReason": self.end_reason,
                "kingActivated": dict(self.king_activated),
                "kingActivationStartedTick": dict(self.king_activation_started_tick),
                "phase": self._phase_name(),
                "suddenDeathTick": SUDDEN_DEATH_START_TICKS,
                "matchEndTick": MATCH_END_TICKS,
                "elixirMultiplier": self._current_elixir_multiplier(),
            },
        }

    def _entity_snapshot(self, entity: Entity) -> Dict:
        return {
            "id": entity.entity_id,
            "side": entity.side,
            "cardId": entity.card_id,
            "unitId": entity.unit_id,
            "label": entity.label,
            "kind": entity.kind,
            "movementType": entity.movement_type,
            "x": round(entity.pos.x, 4),
            "y": round(entity.pos.y, 4),
            "hp": entity.hp,
            "maxHp": entity.max_hp,
            "radius": entity.radius,
            "footprint": entity.footprint,
            "attackRange": entity.attack_range,
            "hitSpeedTicks": entity.hit_speed_ticks,
            "firstAttackTicks": entity.first_attack_ticks,
            "loadTimeTicks": entity.load_time_ticks,
            "projectileSpeed": entity.projectile_speed_tiles_per_minute,
            "splashRadius": entity.splash_radius,
            "sightRange": entity.sight_range,
            "mechanics": list(entity.mechanics),
            "spawnCooldowns": list(entity.spawn_cooldowns),
            "periodicSpawns": [self._spawn_spec_snapshot(spawn) for spawn in entity.periodic_spawns],
            "deathSpawns": [self._spawn_spec_snapshot(spawn) for spawn in entity.death_spawns],
            "deathDamage": entity.death_damage,
            "deathSplashRadius": entity.death_splash_radius,
            "secondaryColor": entity.secondary_color,
            "deployTicks": entity.deploy_ticks_remaining,
            "targetId": entity.target_id,
            "attackWindupTargetId": entity.attack_windup_target_id,
            "towerRole": entity.tower_role,
            "facing": {"x": round(entity.facing_x, 4), "y": round(entity.facing_y, 4)},
            "lastHitTick": entity.last_hit_tick,
            "active": not (
                entity.kind == "tower"
                and entity.tower_role == "king"
                and not self.king_activated.get(entity.side, False)
            ),
        }

    def _projectile_snapshot(self, projectile: Projectile) -> Dict:
        return {
            "id": projectile.projectile_id,
            "side": projectile.side,
            "cardId": projectile.source_card_id,
            "label": projectile.label,
            "x": round(projectile.pos.x, 4),
            "y": round(projectile.pos.y, 4),
            "radius": projectile.radius,
            "targetId": projectile.target_id,
            "targetPos": None
            if projectile.target_pos is None
            else {"x": projectile.target_pos.x, "y": projectile.target_pos.y},
            "mechanics": list(projectile.mechanics),
            "delayTicks": projectile.delay_ticks,
            "effectDone": projectile.effect_done,
            "visualTargets": [{"x": point.x, "y": point.y} for point in projectile.visual_targets],
        }

    def _spawn_spec_snapshot(self, spawn: SpawnSpec) -> Dict:
        return {
            "initialDelayTicks": spawn.initial_delay_ticks,
            "periodTicks": spawn.period_ticks,
            "requiresEnemyInRange": spawn.requires_enemy_in_range,
            "triggerRange": spawn.trigger_range,
            "formation": [{"x": point.x, "y": point.y} for point in spawn.formation],
            "units": [
                {
                    "unitId": unit.unit_id,
                    "label": unit.label,
                    "kind": unit.kind,
                    "movementType": unit.movement_type,
                    "targetMode": unit.target_mode,
                    "hp": unit.hp,
                    "damage": unit.damage,
                    "radius": unit.radius,
                }
                for unit in spawn.units
            ],
        }

    def _spawn_initial_towers(self) -> None:
        positions = default_tower_positions()
        for side in (SIDE_BLUE, SIDE_RED):
            self._spawn_tower(side, "king", positions[side]["king"])
            self._spawn_tower(side, "left_princess", positions[side]["left_princess"])
            self._spawn_tower(side, "right_princess", positions[side]["right_princess"])

    def _spawn_tower(self, side: str, tower_role: str, pos: Vec2) -> None:
        spec = TOWER_SPECS["king" if tower_role == "king" else "princess"]
        secondary = "#f2d17b" if tower_role == "king" else "#d9d4c5"
        entity = self._entity_from_spec(
            side=side,
            card_id=tower_role,
            spec=spec,
            pos=pos,
            secondary_color=secondary,
            deploy_ticks=0,
        )
        entity.tower_role = tower_role
        self.entities[entity.entity_id] = entity

    def _entity_from_spec(
        self,
        side: str,
        card_id: str,
        spec: UnitSpec,
        pos: Vec2,
        secondary_color: str,
        deploy_ticks: Optional[int] = None,
    ) -> Entity:
        entity = Entity(
            entity_id=self._allocate_entity_id(),
            side=side,
            card_id=card_id,
            unit_id=spec.unit_id,
            label=spec.label,
            kind=spec.kind,
            movement_type=spec.movement_type,
            target_mode=spec.target_mode,
            pos=pos,
            hp=spec.hp,
            max_hp=spec.hp,
            damage=spec.damage,
            speed_tiles_per_minute=spec.speed_tiles_per_minute,
            attack_range=self._entity_attack_range(spec),
            sight_range=spec.sight_range,
            hit_speed_ticks=spec.hit_speed_ticks,
            deploy_ticks_remaining=spec.deploy_ticks if deploy_ticks is None else deploy_ticks,
            mass=spec.mass,
            radius=spec.radius,
            secondary_color=secondary_color,
            projectile_speed_tiles_per_minute=spec.projectile_speed_tiles_per_minute,
            splash_radius=spec.splash_radius,
            first_attack_ticks=spec.first_attack_ticks,
            load_time_ticks=spec.load_time_ticks,
            mechanics=spec.mechanics,
            periodic_spawns=spec.periodic_spawns,
            death_spawns=spec.death_spawns,
            death_damage=spec.death_damage,
            death_splash_radius=spec.death_splash_radius,
            spawn_cooldowns=tuple(spawn.initial_delay_ticks for spawn in spec.periodic_spawns),
            footprint_tiles=spec.footprint_tiles,
            lifetime_ticks_remaining=spec.lifetime_ticks,
            lifetime_ticks_total=spec.lifetime_ticks,
            created_tick=self.tick,
        )
        return entity

    def _entity_attack_range(self, spec: UnitSpec) -> float:
        if (
            spec.kind == "troop"
            and spec.attack_range > 0
            and spec.attack_range <= MELEE_ATTACK_RANGE_MAX_TILES
        ):
            return spec.attack_range * MELEE_ATTACK_RANGE_FACTOR
        return spec.attack_range

    def _allocate_entity_id(self) -> int:
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        return entity_id

    def _allocate_projectile_id(self) -> int:
        projectile_id = self._next_projectile_id
        self._next_projectile_id += 1
        return projectile_id

    def _allocate_spawn_sequence(self) -> int:
        sequence = self._next_spawn_sequence
        self._next_spawn_sequence += 1
        return sequence

    def _apply_due_commands(self) -> None:
        while self.pending_commands and self.pending_commands[0].execute_tick <= self.tick:
            command = heapq.heappop(self.pending_commands)
            self._execute_command(command, record=True)

    def _apply_due_spawns(self) -> None:
        while self.pending_spawns and self.pending_spawns[0].execute_tick <= self.tick:
            spawn = heapq.heappop(self.pending_spawns)
            entity = self._entity_from_spec(
                spawn.side,
                spawn.card_id,
                spawn.unit_spec,
                spawn.pos,
                spawn.secondary_color,
            )
            self.entities[entity.entity_id] = entity

    def _execute_command(self, command: ScheduledCommand, record: bool) -> bool:
        if self.game_over:
            self._log("%s command rejected: game over" % command.side)
            return False
        player = self.players[command.side]
        hand = player.hand()
        if command.hand_slot >= len(hand):
            self._log("%s command rejected: empty hand slot" % command.side)
            return False

        card_id = command.card_id or hand[command.hand_slot]
        if hand[command.hand_slot] != card_id:
            self._log("%s command rejected: hand desync on slot %d" % (command.side, command.hand_slot))
            return False

        spec = CARD_SPECS[card_id]
        effective_spec = self._effective_card_for_play(player, spec)
        if effective_spec is None:
            self._log("%s Mirror rejected: no previous card" % command.side)
            return False
        pos = self._snap_placement(Vec2(command.x, command.y))
        if not self._can_place_card(command.side, effective_spec, pos):
            self._log("%s %s rejected: invalid placement" % (command.side, spec.display_name))
            return False
        if not player.can_pay(spec.elixir):
            self._log("%s %s rejected: not enough elixir" % (command.side, spec.display_name))
            return False

        player.spend(spec.elixir)
        player.cycle_slot(command.hand_slot)
        if effective_spec.kind == "spell":
            self._cast_spell(command.side, effective_spec, pos)
        else:
            self._deploy_card_units(command.side, effective_spec, pos)
        if spec.card_id != "mirror":
            player.last_played_card_id = spec.card_id
        accepted = ScheduledCommand(
            execute_tick=self.tick,
            sequence=command.sequence,
            side=command.side,
            hand_slot=command.hand_slot,
            x=pos.x,
            y=pos.y,
            card_id=card_id,
            client_tick=command.client_tick,
            receive_tick=command.receive_tick,
        )
        if record:
            self.accepted_commands.append(accepted)
        if spec.card_id == "mirror":
            self._log(
                "%s played Mirror as %s at %.1f,%.1f"
                % (command.side, effective_spec.display_name, pos.x, pos.y)
            )
        else:
            self._log("%s played %s at %.1f,%.1f" % (command.side, spec.display_name, pos.x, pos.y))
        return True

    def _can_place_card(self, side: str, card: CardSpec, pos: Vec2) -> bool:
        if not self._placement_tile_allowed(side, card.kind, pos):
            return False
        if card.kind == "building":
            return self._placement_area_allowed(side, card.kind, pos, BUILDING_PLACEMENT_TILES)
        return True

    def _effective_card_for_play(self, player: PlayerState, spec: CardSpec) -> Optional[CardSpec]:
        if spec.card_id != "mirror":
            return spec
        source_id = player.last_played_card_id
        if not source_id or source_id == "mirror" or source_id not in CARD_SPECS:
            return None
        return self._mirrored_card(CARD_SPECS[source_id])

    def _mirrored_card(self, source: CardSpec) -> CardSpec:
        units = tuple(self._buff_unit_spec(unit) for unit in source.units)
        spell = self._buff_spell_spec(source.spell) if source.spell is not None else None
        return replace(
            source,
            display_name="Mirrored %s" % source.display_name,
            units=units,
            spell=spell,
        )

    def _buff_unit_spec(self, unit: UnitSpec) -> UnitSpec:
        return replace(
            unit,
            hp=self._buff_stat(unit.hp),
            damage=self._buff_stat(unit.damage),
            death_damage=self._buff_stat(unit.death_damage),
            periodic_spawns=tuple(self._buff_spawn_spec(spawn) for spawn in unit.periodic_spawns),
            death_spawns=tuple(self._buff_spawn_spec(spawn) for spawn in unit.death_spawns),
        )

    def _buff_spawn_spec(self, spawn: SpawnSpec) -> SpawnSpec:
        return replace(spawn, units=tuple(self._buff_unit_spec(unit) for unit in spawn.units))

    def _buff_spell_spec(self, spell: SpellSpec) -> SpellSpec:
        return replace(
            spell,
            damage=self._buff_stat(spell.damage),
            crown_tower_damage=self._buff_stat(spell.crown_tower_damage),
        )

    def _buff_stat(self, value: int) -> int:
        if value <= 0:
            return value
        return int(round(value * 1.1))

    def _snap_placement(self, pos: Vec2) -> Vec2:
        row, col = tile_for_world(pos)
        row = min(max(row, 0), ARENA_ROWS - 1)
        col = min(max(col, 0), ARENA_COLS - 1)
        return Vec2(col + 0.5, row + 0.5)

    def _spawn_card_units(self, side: str, card: CardSpec, pos: Vec2) -> None:
        for unit_spec, spawn_pos in self._expanded_card_unit_spawns(side, card, pos):
            entity = self._entity_from_spec(side, card.card_id, unit_spec, spawn_pos, card.secondary_color)
            self.entities[entity.entity_id] = entity

    def _deploy_card_units(self, side: str, card: CardSpec, pos: Vec2) -> None:
        spawns = self._expanded_card_unit_spawns(side, card, pos)
        if len(spawns) <= 1:
            self._spawn_card_units(side, card, pos)
            return

        for spawn_order, (unit_spec, spawn_pos) in enumerate(spawns):
            execute_tick = self.tick + spawn_order * MULTI_UNIT_SPAWN_STAGGER_TICKS
            if execute_tick <= self.tick:
                entity = self._entity_from_spec(side, card.card_id, unit_spec, spawn_pos, card.secondary_color)
                self.entities[entity.entity_id] = entity
                continue
            heapq.heappush(
                self.pending_spawns,
                ScheduledSpawn(
                    execute_tick=execute_tick,
                    sequence=self._allocate_spawn_sequence(),
                    side=side,
                    card_id=card.card_id,
                    unit_spec=unit_spec,
                    pos=spawn_pos,
                    secondary_color=card.secondary_color,
                ),
            )

    def _expanded_card_unit_spawns(self, side: str, card: CardSpec, pos: Vec2) -> List[Tuple[UnitSpec, Vec2]]:
        return self._expanded_unit_spawns(side, card.units, card.formation, pos)

    def _expanded_unit_spawns(
        self,
        side: str,
        units: Tuple[UnitSpec, ...],
        formation_offsets: Tuple[Vec2, ...],
        pos: Vec2,
    ) -> List[Tuple[UnitSpec, Vec2]]:
        unit_specs = list(units)
        formation = list(formation_offsets)
        if len(unit_specs) == 1 and len(formation) > 1:
            unit_specs = [unit_specs[0] for _ in formation]
        if len(formation) == 1 and len(unit_specs) > 1:
            formation = [formation[0] for _ in unit_specs]

        mirror_y = -1.0 if side == SIDE_RED else 1.0
        spawns: List[Tuple[int, UnitSpec, Vec2]] = []
        for original_index, (unit_spec, offset) in enumerate(zip(unit_specs, formation)):
            spawn_pos = pos.add(offset.x, offset.y * mirror_y).clamp(0.05, 0.05, ARENA_COLS - 0.05, ARENA_ROWS - 0.05)
            spawns.append((original_index, unit_spec, spawn_pos))
        spawns.sort(key=lambda item: (item[2].x, item[0]))
        return [(unit_spec, spawn_pos) for _, unit_spec, spawn_pos in spawns]

    def _spawn_spawn_spec(self, source: Entity, spawn: SpawnSpec) -> None:
        for unit_spec, spawn_pos in self._expanded_unit_spawns(
            source.side,
            spawn.units,
            spawn.formation,
            source.pos,
        ):
            entity = self._entity_from_spec(
                source.side,
                source.card_id,
                unit_spec,
                spawn_pos,
                source.secondary_color,
                deploy_ticks=unit_spec.deploy_ticks,
            )
            self.entities[entity.entity_id] = entity

    def _cast_spell(self, side: str, card: CardSpec, target: Vec2) -> None:
        if card.spell is None:
            return
        spell = card.spell
        if "instant_spell" in spell.mechanics:
            self._resolve_area_spell(side, card, target)
            return
        if "delayed_lightning" in spell.mechanics:
            self._queue_lightning(side, card, target)
            return

        king = self._king_entity(side)
        start = king.pos if king is not None else Vec2(9.0, 29.0 if side == SIDE_BLUE else 3.0)
        projectile = Projectile(
            projectile_id=self._allocate_projectile_id(),
            side=side,
            source_card_id=card.card_id,
            label=card.display_name,
            pos=start,
            damage=spell.damage,
            crown_tower_damage=spell.crown_tower_damage,
            speed_tiles_per_minute=spell.projectile_speed_tiles_per_minute,
            target_pos=target,
            splash_radius=spell.radius,
            knockback_tiles=spell.knockback_tiles,
            radius=0.22,
            ttl_ticks=180,
            mechanics=spell.mechanics,
            spawn_units=card.units,
            spawn_formation=card.formation,
            secondary_color=card.secondary_color,
        )
        self.projectiles[projectile.projectile_id] = projectile

    def _queue_lightning(self, side: str, card: CardSpec, target: Vec2) -> None:
        assert card.spell is not None
        delay_ticks = max(1, int(round(0.5 * TICKS_PER_SECOND)))
        projectile = Projectile(
            projectile_id=self._allocate_projectile_id(),
            side=side,
            source_card_id=card.card_id,
            label=card.display_name,
            pos=target,
            damage=card.spell.damage,
            crown_tower_damage=card.spell.crown_tower_damage,
            speed_tiles_per_minute=0,
            target_pos=target,
            splash_radius=card.spell.radius,
            radius=card.spell.radius,
            ttl_ticks=delay_ticks + max(1, int(round(0.22 * TICKS_PER_SECOND))),
            mechanics=card.spell.mechanics,
            delay_ticks=delay_ticks,
            secondary_color=card.secondary_color,
        )
        self.projectiles[projectile.projectile_id] = projectile

    def _regen_elixir(self) -> None:
        for player in self.players.values():
            player.regen_tick(self._current_elixir_multiplier())

    def _current_elixir_multiplier(self) -> int:
        multiplier = self.options.elixir_regen_multiplier
        if self.tick >= TRIPLE_ELIXIR_START_TICKS:
            multiplier *= 3
        elif self.tick >= DOUBLE_ELIXIR_START_TICKS:
            multiplier *= 2
        return multiplier

    def _phase_name(self) -> str:
        if self.game_over:
            return "ended"
        if self.tick >= SUDDEN_DEATH_START_TICKS:
            return "sudden_death"
        return "regulation"

    def _check_match_clock(self) -> None:
        if not self.game_over and self.tick >= MATCH_END_TICKS:
            self._end_by_tiebreaker()

    def _update_timers(self) -> None:
        for entity in self.entities.values():
            if entity.deploy_ticks_remaining > 0:
                entity.deploy_ticks_remaining -= 1
            if entity.attack_cooldown_ticks > 0:
                entity.attack_cooldown_ticks -= 1
            if entity.lifetime_ticks_remaining is not None:
                entity.lifetime_ticks_remaining -= 1
                if entity.lifetime_ticks_total:
                    entity.lifetime_decay_remainder += entity.max_hp
                    decay, entity.lifetime_decay_remainder = divmod(
                        entity.lifetime_decay_remainder, entity.lifetime_ticks_total
                    )
                    if decay > 0:
                        entity.hp = max(0, entity.hp - decay)
                if entity.lifetime_ticks_remaining <= 0:
                    entity.hp = 0

    def _update_spawners(self) -> None:
        for entity in self._entities_sorted():
            if not entity.alive or not entity.deployed or not entity.periodic_spawns:
                continue
            cooldowns = list(entity.spawn_cooldowns)
            if len(cooldowns) != len(entity.periodic_spawns):
                cooldowns = [spawn.initial_delay_ticks for spawn in entity.periodic_spawns]

            for index, spawn in enumerate(entity.periodic_spawns):
                if spawn.requires_enemy_in_range and not self._enemy_in_spawn_range(entity, spawn):
                    continue
                cooldowns[index] -= 1
                if cooldowns[index] > 0:
                    continue
                if "elixir_collector" in entity.mechanics and not spawn.units:
                    self.players[entity.side].grant_elixir(1)
                    self._log("%s generated 1 elixir" % entity.label)
                else:
                    self._spawn_spawn_spec(entity, spawn)
                if spawn.period_ticks is None:
                    cooldowns[index] = 10**9
                    continue
                period = max(1, spawn.period_ticks)
                while cooldowns[index] <= 0:
                    cooldowns[index] += period

            entity.spawn_cooldowns = tuple(cooldowns)

    def _enemy_in_spawn_range(self, entity: Entity, spawn: SpawnSpec) -> bool:
        trigger_range = spawn.trigger_range if spawn.trigger_range is not None else entity.sight_range
        for candidate in self._entities_sorted():
            if not candidate.alive or candidate.side == entity.side:
                continue
            if entity.effective_distance_to(candidate) <= trigger_range:
                return True
        return False

    def _update_king_activation(self) -> None:
        for side, started_tick in list(self.king_activation_started_tick.items()):
            if started_tick is None or self.king_activated[side]:
                continue
            if self.tick - started_tick >= KING_ACTIVATION_DELAY_TICKS:
                self.king_activated[side] = True
                king = self._king_entity(side)
                if king is not None:
                    king.attack_cooldown_ticks = 0
                self._log("%s king tower activated" % side)

    def _update_entities(self) -> None:
        for entity in self._entities_sorted():
            if not entity.alive or not entity.deployed:
                continue
            self._validate_or_acquire_target(entity)
            target = self.entities.get(entity.target_id) if entity.target_id is not None else None
            if target is not None and target.alive and self._in_attack_range(entity, target):
                self._try_attack(entity, target)
            else:
                self._move_entity(entity, target)

    def _validate_or_acquire_target(self, entity: Entity) -> None:
        if entity.kind == "tower" and entity.tower_role == "king" and not self.king_activated[entity.side]:
            self._set_target(entity, None)
            return
        if entity.target_id is not None:
            target = self.entities.get(entity.target_id)
            if target is not None and target.alive and self._can_target(entity, target):
                distance = entity.effective_distance_to(target)
                if entity.target_locked and distance > max(entity.sight_range, entity.attack_range + 2.5):
                    self._set_target(entity, None)
                elif target.is_building_like and not entity.target_locked:
                    challenger = self._best_target(entity)
                    if challenger is not None and challenger.entity_id != target.entity_id:
                        challenger_distance = entity.effective_distance_to(challenger)
                        if challenger_distance + 0.05 < distance:
                            self._set_target(entity, challenger)
                    return
                else:
                    return
            else:
                self._set_target(entity, None)
        target = self._best_target(entity)
        self._set_target(entity, target)

    def _set_target(self, entity: Entity, target: Optional[Entity]) -> None:
        target_id = target.entity_id if target is not None else None
        if entity.target_id == target_id:
            return
        if entity.attack_windup_target_id is not None and not entity.target_locked:
            entity.attack_cooldown_ticks = 0
        entity.target_id = target_id
        entity.target_locked = False
        entity.attack_windup_target_id = None

    def _best_target(self, entity: Entity) -> Optional[Entity]:
        best = None
        best_distance = 999999.0
        for candidate in self._entities_sorted():
            if not candidate.alive or candidate.side == entity.side:
                continue
            if not self._can_target(entity, candidate):
                continue
            distance = entity.effective_distance_to(candidate)
            if distance <= entity.sight_range and (distance, candidate.entity_id) < (
                best_distance,
                best.entity_id if best is not None else 999999,
            ):
                best = candidate
                best_distance = distance
        return best

    def _can_target(self, attacker: Entity, target: Entity) -> bool:
        if attacker.target_mode == "none":
            return False
        if target.kind == "projectile":
            return False
        if attacker.target_mode == "all":
            return True
        if attacker.target_mode == "ground":
            return not target.is_air
        if attacker.target_mode == "buildings":
            return target.is_building_like
        return False

    def _in_attack_range(self, attacker: Entity, target: Entity) -> bool:
        return attacker.effective_distance_to(target) <= attacker.attack_range

    def _try_attack(self, attacker: Entity, target: Entity) -> None:
        if attacker.attack_cooldown_ticks > 0:
            return
        if not attacker.target_locked and attacker.attack_windup_target_id != target.entity_id:
            first_attack_ticks = attacker.first_attack_ticks or 0
            if first_attack_ticks > 0:
                attacker.attack_windup_target_id = target.entity_id
                attacker.attack_cooldown_ticks = first_attack_ticks
                self._face_toward(attacker, target.pos)
                return
            attacker.attack_windup_target_id = target.entity_id
        self._face_toward(attacker, target.pos)
        attacker.attack_cooldown_ticks = attacker.hit_speed_ticks
        attacker.target_locked = True
        if attacker.projectile_speed_tiles_per_minute > 0 and attacker.attack_range > 1.8:
            projectile = Projectile(
                projectile_id=self._allocate_projectile_id(),
                side=attacker.side,
                source_card_id=attacker.card_id,
                label=attacker.label,
                pos=attacker.pos,
                damage=attacker.damage,
                speed_tiles_per_minute=attacker.projectile_speed_tiles_per_minute,
                target_id=target.entity_id,
                splash_radius=attacker.splash_radius,
            )
            self.projectiles[projectile.projectile_id] = projectile
            if "kamikaze" in attacker.mechanics:
                attacker.hp = 0
            return
        self._damage_entity(attacker.side, target, attacker.damage, splash_radius=attacker.splash_radius)
        if "kamikaze" in attacker.mechanics:
            attacker.hp = 0
        attacker.last_hit_tick = self.tick

    def _move_entity(self, entity: Entity, target: Optional[Entity]) -> None:
        if not entity.can_move:
            return
        destination = target.pos if target is not None else self._lane_destination(entity)
        if destination is None:
            return
        if entity.movement_type == "ground":
            destination = self._ground_steering_destination(entity, destination)
            if not self._near_bridge(entity):
                destination = self._avoid_blocking_buildings(entity, destination, target)
        step_distance = (
            tiles_per_minute_to_tiles_per_tick(entity.speed_tiles_per_minute, TICKS_PER_SECOND)
            * TROOP_MOVEMENT_SPEED_FACTOR
        )
        new_pos = entity.pos.moved_toward(destination, step_distance)
        if entity.movement_type == "ground" and not self._ground_position_allowed(new_pos):
            bridge_x = self._bridge_lane_x(self._preferred_bridge_x(entity.pos, destination), entity.side)
            fallback = Vec2(bridge_x, 15.5 if entity.pos.y > 16.0 else 16.5)
            new_pos = entity.pos.moved_toward(fallback, step_distance)
        if new_pos.distance_to(entity.pos) > 1e-9:
            self._face_toward(entity, new_pos)
        entity.pos = new_pos.clamp(entity.radius, entity.radius, ARENA_COLS - entity.radius, ARENA_ROWS - entity.radius)

    def _face_toward(self, entity: Entity, destination: Vec2) -> None:
        direction = entity.pos.direction_to(destination)
        if abs(direction.x) + abs(direction.y) <= 1e-9:
            return
        entity.facing_x = direction.x
        entity.facing_y = direction.y

    def _lane_destination(self, entity: Entity) -> Optional[Vec2]:
        enemy = SIDE_RED if entity.side == SIDE_BLUE else SIDE_BLUE
        lane = lane_for_x(entity.pos.x)
        princess_role = "%s_princess" % lane
        tower = self._tower_entity(enemy, princess_role)
        if tower is not None and tower.alive:
            return tower.pos
        king = self._king_entity(enemy)
        return king.pos if king is not None else None

    def _ground_steering_destination(self, entity: Entity, destination: Vec2) -> Vec2:
        current_above = entity.pos.y < 15.0
        current_below = entity.pos.y > 17.0
        dest_above = destination.y < 15.0
        dest_below = destination.y > 17.0
        crossing = (current_below and dest_above) or (current_above and dest_below)
        if not crossing:
            return destination

        if self._ground_segment_allowed(entity.pos, destination):
            return destination

        bridge_x = self._bridge_lane_x(self._preferred_bridge_x(entity.pos, destination), entity.side)
        if self._behind_own_king(entity):
            return Vec2(bridge_x, entity.pos.y)
        if current_below:
            return Vec2(bridge_x, 16.5)
        if current_above:
            return Vec2(bridge_x, 15.5)
        return destination

    def _nearest_bridge_x(self, x: float) -> float:
        return 3.5 if abs(x - 3.5) <= abs(x - 14.5) else 14.5

    def _bridge_lane_x(self, bridge_x: float, side: str) -> float:
        return bridge_x

    def _preferred_bridge_x(self, start: Vec2, destination: Vec2) -> float:
        reference_x = destination.x
        if abs(destination.x - 9.0) < 0.8:
            reference_x = (start.x + destination.x) * 0.5
        return 3.5 if reference_x < 9.0 else 14.5

    def _behind_own_king(self, entity: Entity) -> bool:
        if entity.side == SIDE_BLUE:
            return entity.pos.y > 28.0 and 6.8 < entity.pos.x < 11.2
        return entity.pos.y < 4.0 and 6.8 < entity.pos.x < 11.2

    def _ground_position_allowed(self, pos: Vec2) -> bool:
        row, col = tile_for_world(pos)
        kind = tile_type(row, col)
        if kind != RIVER:
            return True
        return is_bridge_world(pos)

    def _ground_segment_allowed(self, start: Vec2, end: Vec2) -> bool:
        distance = start.distance_to(end)
        if distance <= 1e-9:
            return self._ground_position_allowed(end)
        samples = max(1, int(math.ceil(distance / GROUND_SEGMENT_SAMPLE_TILES)))
        for index in range(1, samples + 1):
            progress = index / samples
            point = Vec2(
                start.x + (end.x - start.x) * progress,
                start.y + (end.y - start.y) * progress,
            )
            if not self._ground_position_allowed(point):
                return False
        return True

    def _on_bridge_corridor(self, entity: Entity) -> bool:
        return is_bridge_world(entity.pos)

    def _near_bridge(self, entity: Entity) -> bool:
        return 14.0 <= entity.pos.y <= 18.0 and (
            abs(entity.pos.x - 3.5) <= 2.2 or abs(entity.pos.x - 14.5) <= 2.2
        )

    def _avoid_blocking_buildings(self, entity: Entity, destination: Vec2, target: Optional[Entity]) -> Vec2:
        obstacle = self._first_blocking_building(entity, destination, target)
        if obstacle is None:
            return destination

        move_dir = entity.pos.direction_to(destination)
        away = obstacle.pos.direction_to(entity.pos)
        if abs(away.x) + abs(away.y) <= 1e-9:
            away = Vec2(-move_dir.y, move_dir.x)
        tangent_a = Vec2(-away.y, away.x)
        tangent_b = Vec2(away.y, -away.x)
        clearance = self._pathing_collision_radius(obstacle) + entity.radius + BUILDING_AVOIDANCE_MARGIN
        base = obstacle.pos.add(away.x * clearance, away.y * clearance)
        candidates = [
            base.add(tangent_a.x * clearance, tangent_a.y * clearance),
            base.add(tangent_b.x * clearance, tangent_b.y * clearance),
        ]
        allowed = [point for point in candidates if self._ground_position_allowed(point)]
        if not allowed:
            allowed = candidates
        return min(allowed, key=lambda point: point.distance_to(destination))

    def _first_blocking_building(
        self, entity: Entity, destination: Vec2, target: Optional[Entity]
    ) -> Optional[Entity]:
        ignored_target_id = target.entity_id if target is not None and target.is_building_like else None
        blockers: List[Tuple[float, Entity]] = []
        for obstacle in self._entities_sorted():
            if obstacle.entity_id == entity.entity_id or not obstacle.alive or not obstacle.is_building_like:
                continue
            if obstacle.entity_id == ignored_target_id:
                continue
            if obstacle.pos.distance_to(destination) <= 1e-6:
                continue
            inflated = self._pathing_collision_radius(obstacle) + entity.radius + 0.2
            progress, distance = self._segment_projection(entity.pos, destination, obstacle.pos)
            if 0.0 <= progress <= 1.0 and distance <= inflated:
                blockers.append((entity.pos.distance_to(obstacle.pos), obstacle))
        if not blockers:
            return None
        blockers.sort(key=lambda item: (item[0], item[1].entity_id))
        return blockers[0][1]

    def _pathing_collision_radius(self, obstacle: Entity) -> float:
        return obstacle.radius

    def _segment_projection(self, start: Vec2, end: Vec2, point: Vec2) -> Tuple[float, float]:
        dx = end.x - start.x
        dy = end.y - start.y
        length_sq = dx * dx + dy * dy
        if length_sq <= 1e-9:
            return 0.0, start.distance_to(point)
        progress = ((point.x - start.x) * dx + (point.y - start.y) * dy) / length_sq
        clamped = min(1.0, max(0.0, progress))
        closest = Vec2(start.x + dx * clamped, start.y + dy * clamped)
        return progress, closest.distance_to(point)

    def _update_projectiles(self) -> None:
        for projectile in list(self.projectiles.values()):
            projectile.ttl_ticks -= 1
            if projectile.ttl_ticks <= 0:
                self.projectiles.pop(projectile.projectile_id, None)
                continue
            if "delayed_lightning" in projectile.mechanics:
                self._update_delayed_lightning(projectile)
            elif projectile.is_spell:
                self._update_spell_projectile(projectile)
            else:
                self._update_targeted_projectile(projectile)

    def _update_spell_projectile(self, projectile: Projectile) -> None:
        assert projectile.target_pos is not None
        step_distance = tiles_per_minute_to_tiles_per_tick(projectile.speed_tiles_per_minute, TICKS_PER_SECOND)
        projectile.pos = projectile.pos.moved_toward(projectile.target_pos, step_distance)
        if projectile.pos.distance_to(projectile.target_pos) <= 0.05:
            if "spawn_on_arrival" in projectile.mechanics:
                self._spawn_spell_units(projectile)
            else:
                self._resolve_area_damage(projectile)
            self.projectiles.pop(projectile.projectile_id, None)

    def _update_delayed_lightning(self, projectile: Projectile) -> None:
        if projectile.effect_done:
            return
        projectile.delay_ticks -= 1
        if projectile.delay_ticks > 0:
            return
        hits = self._resolve_lightning(projectile)
        projectile.visual_targets = tuple(entity.pos for entity in hits)
        projectile.effect_done = True
        projectile.ttl_ticks = max(projectile.ttl_ticks, max(1, int(round(0.22 * TICKS_PER_SECOND))))

    def _spawn_spell_units(self, projectile: Projectile) -> None:
        if projectile.target_pos is None:
            return
        for unit_spec, spawn_pos in self._expanded_unit_spawns(
            projectile.side,
            projectile.spawn_units,
            projectile.spawn_formation,
            projectile.target_pos,
        ):
            entity = self._entity_from_spec(
                projectile.side,
                projectile.source_card_id,
                unit_spec,
                spawn_pos,
                projectile.secondary_color,
                deploy_ticks=unit_spec.deploy_ticks,
            )
            self.entities[entity.entity_id] = entity
        self._log("%s resolved at %.1f,%.1f" % (projectile.label, projectile.target_pos.x, projectile.target_pos.y))

    def _update_targeted_projectile(self, projectile: Projectile) -> None:
        target = self.entities.get(projectile.target_id) if projectile.target_id is not None else None
        if target is None or not target.alive:
            self.projectiles.pop(projectile.projectile_id, None)
            return
        step_distance = tiles_per_minute_to_tiles_per_tick(projectile.speed_tiles_per_minute, TICKS_PER_SECOND)
        projectile.pos = projectile.pos.moved_toward(target.pos, step_distance)
        if projectile.pos.distance_to(target.pos) <= target.radius + projectile.radius:
            self._damage_entity(projectile.side, target, projectile.damage, splash_radius=projectile.splash_radius)
            self.projectiles.pop(projectile.projectile_id, None)

    def _resolve_area_damage(self, projectile: Projectile) -> None:
        for entity in self._targets_in_radius(projectile.side, projectile.pos, projectile.splash_radius):
            self._damage_spell_target(projectile, entity)
            self._apply_knockback(entity, projectile.pos, projectile.knockback_tiles)
        self._log("%s resolved at %.1f,%.1f" % (projectile.label, projectile.pos.x, projectile.pos.y))

    def _resolve_area_spell(self, side: str, card: CardSpec, target: Vec2) -> None:
        if card.spell is None:
            return
        projectile = Projectile(
            projectile_id=self._allocate_projectile_id(),
            side=side,
            source_card_id=card.card_id,
            label=card.display_name,
            pos=target,
            damage=card.spell.damage,
            crown_tower_damage=card.spell.crown_tower_damage,
            speed_tiles_per_minute=0,
            target_pos=target,
            splash_radius=card.spell.radius,
            knockback_tiles=card.spell.knockback_tiles,
            mechanics=card.spell.mechanics,
        )
        self._resolve_area_damage(projectile)

    def _resolve_lightning(self, projectile: Projectile) -> List[Entity]:
        candidates = self._targets_in_radius(projectile.side, projectile.pos, projectile.splash_radius)
        hits = sorted(candidates, key=lambda entity: (-entity.hp, entity.entity_id))[:3]
        for entity in hits:
            self._damage_spell_target(projectile, entity)
            entity.attack_cooldown_ticks = 0
            entity.attack_windup_target_id = None
            entity.target_locked = False
        self._log("%s struck %d targets at %.1f,%.1f" % (projectile.label, len(hits), projectile.pos.x, projectile.pos.y))
        return hits

    def _targets_in_radius(self, source_side: str, center: Vec2, radius: float) -> List[Entity]:
        return [
            entity
            for entity in self._entities_sorted()
            if entity.alive
            and entity.side != source_side
            and entity.pos.distance_to(center) <= radius + entity.radius
        ]

    def _damage_spell_target(self, projectile: Projectile, entity: Entity) -> None:
        damage = projectile.damage
        if entity.kind == "tower" and projectile.crown_tower_damage is not None:
            damage = projectile.crown_tower_damage
        self._damage_entity(projectile.side, entity, damage)
        if "reset" in projectile.mechanics:
            entity.attack_cooldown_ticks = 0
            entity.attack_windup_target_id = None
            entity.target_locked = False

    def _damage_entity(self, source_side: str, target: Entity, amount: int, splash_radius: float = 0.0) -> None:
        if amount <= 0 or not target.alive:
            return
        if target.kind == "tower" and target.tower_role == "king":
            self._trigger_king_activation(target.side)
        target.hp = max(0, target.hp - int(amount))
        if splash_radius > 0:
            for entity in self._entities_sorted():
                if entity.entity_id == target.entity_id or entity.side == source_side or not entity.alive:
                    continue
                if entity.pos.distance_to(target.pos) <= splash_radius + entity.radius:
                    entity.hp = max(0, entity.hp - int(amount))

    def _apply_knockback(self, entity: Entity, origin: Vec2, distance: float) -> None:
        if distance <= 0 or entity.is_building_like:
            return
        if entity.mass >= 18:
            return
        direction = origin.direction_to(entity.pos)
        new_pos = entity.pos.add(direction.x * distance, direction.y * distance)
        entity.pos = new_pos.clamp(entity.radius, entity.radius, ARENA_COLS - entity.radius, ARENA_ROWS - entity.radius)

    def _resolve_collisions(self) -> None:
        units = [entity for entity in self._entities_sorted() if entity.alive]
        for index, left in enumerate(units):
            for right in units[index + 1 :]:
                if left.is_air != right.is_air:
                    continue
                overlap = left.radius + right.radius - left.pos.distance_to(right.pos)
                if overlap <= 0:
                    continue
                if left.mass == 0 and right.mass == 0:
                    continue
                dx = right.pos.x - left.pos.x
                dy = right.pos.y - left.pos.y
                length = math.hypot(dx, dy)
                if length <= 1e-9:
                    dx, dy, length = 1.0, 0.0, 1.0
                if self._bridge_passing_pair(left, right):
                    nx, ny = (1.0, 0.0) if left.side == SIDE_RED else (-1.0, 0.0)
                else:
                    nx, ny = dx / length, dy / length
                
                MASS_WEIGHT_POWER = 0.5

                left_weight = 0.0 if left.mass == 0 else 1.0 / left.mass ** MASS_WEIGHT_POWER
                right_weight = 0.0 if right.mass == 0 else 1.0 / right.mass ** MASS_WEIGHT_POWER
                total = left_weight + right_weight
                if total <= 0:
                    continue
                correction = overlap + 0.001
                if left_weight > 0:
                    fraction = left_weight / total
                    left.pos = left.pos.add(-nx * correction * fraction, -ny * correction * fraction)
                if right_weight > 0:
                    fraction = right_weight / total
                    right.pos = right.pos.add(nx * correction * fraction, ny * correction * fraction)
                left.pos = left.pos.clamp(left.radius, left.radius, ARENA_COLS - left.radius, ARENA_ROWS - left.radius)
                right.pos = right.pos.clamp(right.radius, right.radius, ARENA_COLS - right.radius, ARENA_ROWS - right.radius)
                if left.movement_type == "ground":
                    left.pos = self._repair_ground_position(left)
                if right.movement_type == "ground":
                    right.pos = self._repair_ground_position(right)

    def _repair_ground_position(self, entity: Entity) -> Vec2:
        if self._ground_position_allowed(entity.pos):
            return entity.pos
        row, _ = tile_for_world(entity.pos)
        bridge_x = self._bridge_lane_x(self._nearest_bridge_x(entity.pos.x), entity.side)
        if row in (15, 16):
            return Vec2(bridge_x, entity.pos.y).clamp(
                entity.radius, entity.radius, ARENA_COLS - entity.radius, ARENA_ROWS - entity.radius
            )
        repaired_y = 14.95 if entity.pos.y < 16.0 else 17.05
        return Vec2(entity.pos.x, repaired_y).clamp(
            entity.radius, entity.radius, ARENA_COLS - entity.radius, ARENA_ROWS - entity.radius
        )

    def _bridge_passing_pair(self, left: Entity, right: Entity) -> bool:
        if left.side == right.side:
            return False
        if not left.can_move or not right.can_move:
            return False
        if not self._on_bridge_corridor(left) and not self._on_bridge_corridor(right):
            return False
        return left.target_mode == "buildings" or right.target_mode == "buildings"

    def _cleanup_dead(self) -> None:
        dead_ids = [entity_id for entity_id, entity in self.entities.items() if entity.hp <= 0]
        for entity_id in dead_ids:
            if entity_id not in self.entities:
                continue
            entity = self.entities.pop(entity_id)
            self._resolve_death_damage(entity)
            for spawn in entity.death_spawns:
                if spawn.requires_enemy_in_range and not self._enemy_in_spawn_range(entity, spawn):
                    continue
                self._spawn_spawn_spec(entity, spawn)
            if entity.kind == "tower":
                self._log("%s %s destroyed" % (entity.side, entity.tower_role))
                if entity.tower_role == "king" or self.tick >= SUDDEN_DEATH_START_TICKS:
                    self._end_game(defeated_side=entity.side, reason="tower destroyed")
                elif entity.tower_role in ("left_princess", "right_princess"):
                    self._trigger_king_activation(entity.side)
            for other in self.entities.values():
                if other.target_id == entity_id:
                    other.target_id = None

    def _resolve_death_damage(self, source: Entity) -> None:
        if source.death_damage <= 0 or source.death_splash_radius <= 0:
            return
        hit_any = False
        for entity in self._entities_sorted():
            if not entity.alive or entity.side == source.side:
                continue
            if entity.pos.distance_to(source.pos) <= source.death_splash_radius + entity.radius:
                self._damage_entity(source.side, entity, source.death_damage)
                hit_any = True
        if hit_any:
            self._log(
                "%s death damage resolved at %.1f,%.1f"
                % (source.label, source.pos.x, source.pos.y)
            )

    def _trigger_king_activation(self, side: str) -> None:
        if self.king_activated[side] or self.king_activation_started_tick[side] is not None:
            return
        self.king_activation_started_tick[side] = self.tick
        self._log("%s king tower activation started" % side)

    def _end_game(
        self,
        defeated_side: Optional[str] = None,
        winner: Optional[str] = None,
        reason: str = "game over",
    ) -> None:
        if self.game_over:
            return
        self.game_over = True
        if winner is not None:
            self.winner = winner
        elif defeated_side is not None:
            self.winner = SIDE_RED if defeated_side == SIDE_BLUE else SIDE_BLUE
        else:
            self.winner = None
        self.ended_tick = self.tick
        self.end_reason = reason
        if defeated_side is not None:
            for entity in list(self.entities.values()):
                if entity.side == defeated_side and entity.kind == "tower":
                    self.entities.pop(entity.entity_id, None)
                    for other in self.entities.values():
                        if other.target_id == entity.entity_id:
                            other.target_id = None
        self.pending_commands.clear()
        if self.winner is None:
            self._log("match ends in a draw by %s" % reason)
        else:
            self._log("%s wins by %s" % (self.winner, reason))

    def _end_by_tiebreaker(self) -> None:
        blue_low = min(self._tower_healths(SIDE_BLUE))
        red_low = min(self._tower_healths(SIDE_RED))
        if blue_low == red_low:
            self._end_game(winner=None, reason="tiebreaker draw")
        elif blue_low < red_low:
            self._end_game(defeated_side=SIDE_BLUE, reason="tiebreaker")
        else:
            self._end_game(defeated_side=SIDE_RED, reason="tiebreaker")

    def _tower_healths(self, side: str) -> List[int]:
        values = []
        for role in ("king", "left_princess", "right_princess"):
            tower = self._tower_entity(side, role)
            values.append(tower.hp if tower is not None else 0)
        return values

    def _princess_alive(self, side: str) -> Tuple[bool, bool]:
        return (
            self._tower_entity(side, "left_princess") is not None,
            self._tower_entity(side, "right_princess") is not None,
        )

    def _king_entity(self, side: str) -> Optional[Entity]:
        return self._tower_entity(side, "king")

    def _tower_entity(self, side: str, role: str) -> Optional[Entity]:
        for entity in self.entities.values():
            if entity.side == side and entity.kind == "tower" and entity.tower_role == role:
                return entity
        return None

    def _entities_sorted(self) -> List[Entity]:
        return sorted(self.entities.values(), key=lambda entity: entity.entity_id)

    def _log(self, message: str) -> None:
        self.event_log.append("[t=%d] %s" % (self.tick, message))
        if len(self.event_log) > self.options.max_event_log:
            self.event_log = self.event_log[-self.options.max_event_log :]
