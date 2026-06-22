from __future__ import annotations

from dataclasses import dataclass
import hashlib
import heapq
import json
import math
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
from .cards import CARD_SPECS, DEFAULT_DECK, TOWER_SPECS, CardSpec, UnitSpec
from .constants import (
    ARENA_COLS,
    ARENA_ROWS,
    ENGINE_VERSION,
    KING_ACTIVATION_DELAY_TICKS,
    MATCH_END_TICKS,
    SUDDEN_DEATH_START_TICKS,
    SIDE_BLUE,
    SIDE_RED,
    SIDES,
    TICKS_PER_SECOND,
    TROOP_MOVEMENT_SPEED_FACTOR,
)
from .entities import Entity, PlayerState, Projectile, ScheduledCommand, players_snapshot
from .geometry import Vec2, tiles_per_minute_to_tiles_per_tick
from .replay import CompactReplay, build_replay


@dataclass(frozen=True)
class GameOptions:
    simulated_network_latency_ticks: int = 0
    placement_delay_ticks: int = 2
    lockstep_delay_ticks: int = 0
    elixir_regen_multiplier: int = 1
    max_event_log: int = 80


class GameEngine:
    def __init__(
        self,
        blue_deck: Iterable[str] = DEFAULT_DECK,
        red_deck: Iterable[str] = DEFAULT_DECK,
        options: Optional[GameOptions] = None,
        seed: int = 0,
    ) -> None:
        self.options = options or GameOptions()
        self.seed = seed
        self.tick = 0
        self._next_entity_id = 1
        self._next_projectile_id = 1
        self.players: Dict[str, PlayerState] = {
            SIDE_BLUE: PlayerState(SIDE_BLUE, tuple(blue_deck)),
            SIDE_RED: PlayerState(SIDE_RED, tuple(red_deck)),
        }
        self.entities: Dict[int, Entity] = {}
        self.projectiles: Dict[int, Projectile] = {}
        self.pending_commands: List[ScheduledCommand] = []
        self.accepted_commands: List[ScheduledCommand] = []
        self.event_log: List[str] = []
        self.king_activated: Dict[str, bool] = {SIDE_BLUE: False, SIDE_RED: False}
        self.king_activation_started_tick: Dict[str, Optional[int]] = {SIDE_BLUE: None, SIDE_RED: None}
        self.game_over = False
        self.winner: Optional[str] = None
        self.ended_tick: Optional[int] = None
        self.end_reason: Optional[str] = None
        self._spawn_initial_towers()

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
        self._regen_elixir()
        self._update_timers()
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
        return placement_allowed(
            side,
            spec.kind,
            pos,
            blue_princess_alive=self._princess_alive(SIDE_BLUE),
            red_princess_alive=self._princess_alive(SIDE_RED),
        )

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
                    "cooldown": entity.attack_cooldown_ticks,
                    "deploy": entity.deploy_ticks_remaining,
                    "life": entity.lifetime_ticks_remaining,
                }
                for entity in self._entities_sorted()
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
            "logs": list(self.event_log),
            "cards": {
                card_id: {
                    "name": spec.display_name,
                    "elixir": spec.elixir,
                    "kind": spec.kind,
                    "secondaryColor": spec.secondary_color,
                    "spellRadius": spec.spell.radius if spec.spell is not None else 0,
                    "formation": [{"x": point.x, "y": point.y} for point in spec.formation],
                    "units": [
                        {
                            "radius": unit.radius,
                            "footprint": unit.footprint_tiles if unit.footprint_tiles > 0 else unit.radius * 2.0,
                            "kind": unit.kind,
                            "movementType": unit.movement_type,
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
            "secondaryColor": entity.secondary_color,
            "deployTicks": entity.deploy_ticks_remaining,
            "targetId": entity.target_id,
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
            attack_range=spec.attack_range,
            sight_range=spec.sight_range,
            hit_speed_ticks=spec.hit_speed_ticks,
            deploy_ticks_remaining=spec.deploy_ticks if deploy_ticks is None else deploy_ticks,
            mass=spec.mass,
            radius=spec.radius,
            secondary_color=secondary_color,
            projectile_speed_tiles_per_minute=spec.projectile_speed_tiles_per_minute,
            splash_radius=spec.splash_radius,
            footprint_tiles=spec.footprint_tiles,
            lifetime_ticks_remaining=spec.lifetime_ticks,
            lifetime_ticks_total=spec.lifetime_ticks,
            created_tick=self.tick,
        )
        return entity

    def _allocate_entity_id(self) -> int:
        entity_id = self._next_entity_id
        self._next_entity_id += 1
        return entity_id

    def _allocate_projectile_id(self) -> int:
        projectile_id = self._next_projectile_id
        self._next_projectile_id += 1
        return projectile_id

    def _apply_due_commands(self) -> None:
        while self.pending_commands and self.pending_commands[0].execute_tick <= self.tick:
            command = heapq.heappop(self.pending_commands)
            self._execute_command(command, record=True)

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
        pos = self._snap_placement(Vec2(command.x, command.y))
        if not self.can_place(command.side, card_id, pos):
            self._log("%s %s rejected: invalid placement" % (command.side, spec.display_name))
            return False
        if not player.can_pay(spec.elixir):
            self._log("%s %s rejected: not enough elixir" % (command.side, spec.display_name))
            return False

        player.spend(spec.elixir)
        player.cycle_slot(command.hand_slot)
        if spec.kind == "spell":
            self._cast_spell(command.side, spec, pos)
        else:
            self._spawn_card_units(command.side, spec, pos)
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
        self._log("%s played %s at %.1f,%.1f" % (command.side, spec.display_name, pos.x, pos.y))
        return True

    def _snap_placement(self, pos: Vec2) -> Vec2:
        row, col = tile_for_world(pos)
        row = min(max(row, 0), ARENA_ROWS - 1)
        col = min(max(col, 0), ARENA_COLS - 1)
        return Vec2(col + 0.5, row + 0.5)

    def _spawn_card_units(self, side: str, card: CardSpec, pos: Vec2) -> None:
        unit_specs = list(card.units)
        formation = list(card.formation)
        if len(unit_specs) == 1 and len(formation) > 1:
            unit_specs = [unit_specs[0] for _ in formation]
        if len(formation) == 1 and len(unit_specs) > 1:
            formation = [formation[0] for _ in unit_specs]

        mirror_y = -1.0 if side == SIDE_RED else 1.0
        for unit_spec, offset in zip(unit_specs, formation):
            spawn_pos = pos.add(offset.x, offset.y * mirror_y).clamp(0.05, 0.05, ARENA_COLS - 0.05, ARENA_ROWS - 0.05)
            entity = self._entity_from_spec(side, card.card_id, unit_spec, spawn_pos, card.secondary_color)
            self.entities[entity.entity_id] = entity

    def _cast_spell(self, side: str, card: CardSpec, target: Vec2) -> None:
        if card.spell is None:
            return
        king = self._king_entity(side)
        start = king.pos if king is not None else Vec2(9.0, 29.0 if side == SIDE_BLUE else 3.0)
        spell = card.spell
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
        )
        self.projectiles[projectile.projectile_id] = projectile

    def _regen_elixir(self) -> None:
        for player in self.players.values():
            player.regen_tick(self._current_elixir_multiplier())

    def _current_elixir_multiplier(self) -> int:
        multiplier = self.options.elixir_regen_multiplier
        if self.tick >= SUDDEN_DEATH_START_TICKS:
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
            entity.target_id = None
            entity.target_locked = False
            return
        if entity.target_id is not None:
            target = self.entities.get(entity.target_id)
            if target is not None and target.alive and self._can_target(entity, target):
                distance = entity.effective_distance_to(target)
                if entity.target_locked and distance > max(entity.sight_range, entity.attack_range + 2.5):
                    entity.target_id = None
                    entity.target_locked = False
                elif target.is_building_like and not entity.target_locked:
                    challenger = self._best_target(entity)
                    if challenger is not None and challenger.entity_id != target.entity_id:
                        challenger_distance = entity.effective_distance_to(challenger)
                        if challenger_distance + 0.05 < distance:
                            entity.target_id = challenger.entity_id
                            entity.target_locked = False
                    return
                else:
                    return
            else:
                entity.target_id = None
                entity.target_locked = False
        target = self._best_target(entity)
        entity.target_id = target.entity_id if target is not None else None
        entity.target_locked = False

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
            return
        self._damage_entity(attacker.side, target, attacker.damage, splash_radius=attacker.splash_radius)
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

        bridge_x = self._bridge_lane_x(self._preferred_bridge_x(entity.pos, destination), entity.side)
        if self._behind_own_king(entity):
            return Vec2(bridge_x, entity.pos.y)
        if entity.side == SIDE_BLUE:
            if entity.pos.y > 16.6:
                return Vec2(bridge_x, 16.5)
            if entity.pos.y > 14.4:
                return Vec2(bridge_x, 14.5)
        else:
            if entity.pos.y < 15.4:
                return Vec2(bridge_x, 15.5)
            if entity.pos.y < 17.6:
                return Vec2(bridge_x, 17.5)
        return destination

    def _nearest_bridge_x(self, x: float) -> float:
        return 3.5 if abs(x - 3.5) <= abs(x - 14.5) else 14.5

    def _bridge_lane_x(self, bridge_x: float, side: str) -> float:
        return bridge_x + (0.35 if side == SIDE_BLUE else -0.35)

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
        clearance = obstacle.radius + entity.radius + 0.55
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
            inflated = obstacle.radius + entity.radius + 0.2
            progress, distance = self._segment_projection(entity.pos, destination, obstacle.pos)
            if 0.0 <= progress <= 1.0 and distance <= inflated:
                blockers.append((entity.pos.distance_to(obstacle.pos), obstacle))
        if not blockers:
            return None
        blockers.sort(key=lambda item: (item[0], item[1].entity_id))
        return blockers[0][1]

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
            if projectile.is_spell:
                self._update_spell_projectile(projectile)
            else:
                self._update_targeted_projectile(projectile)

    def _update_spell_projectile(self, projectile: Projectile) -> None:
        assert projectile.target_pos is not None
        step_distance = tiles_per_minute_to_tiles_per_tick(projectile.speed_tiles_per_minute, TICKS_PER_SECOND)
        projectile.pos = projectile.pos.moved_toward(projectile.target_pos, step_distance)
        if projectile.pos.distance_to(projectile.target_pos) <= 0.05:
            self._resolve_area_damage(projectile)
            self.projectiles.pop(projectile.projectile_id, None)

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
        for entity in self._entities_sorted():
            if not entity.alive or entity.side == projectile.side:
                continue
            if entity.pos.distance_to(projectile.pos) <= projectile.splash_radius + entity.radius:
                damage = projectile.damage
                if entity.kind == "tower" and projectile.crown_tower_damage is not None:
                    damage = projectile.crown_tower_damage
                self._damage_entity(projectile.side, entity, damage)
                self._apply_knockback(entity, projectile.pos, projectile.knockback_tiles)
        self._log("%s resolved at %.1f,%.1f" % (projectile.label, projectile.pos.x, projectile.pos.y))

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
                left_weight = 0.0 if left.mass == 0 else 1.0 / left.mass
                right_weight = 0.0 if right.mass == 0 else 1.0 / right.mass
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
            if entity.kind == "tower":
                self._log("%s %s destroyed" % (entity.side, entity.tower_role))
                if entity.tower_role == "king" or self.tick >= SUDDEN_DEATH_START_TICKS:
                    self._end_game(defeated_side=entity.side, reason="tower destroyed")
                elif entity.tower_role in ("left_princess", "right_princess"):
                    self._trigger_king_activation(entity.side)
            for other in self.entities.values():
                if other.target_id == entity_id:
                    other.target_id = None

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
