import unittest

from clashbot.engine.constants import (
    MATCH_END_TICKS,
    SIDE_BLUE,
    SIDE_RED,
    SUDDEN_DEATH_START_TICKS,
    TICKS_PER_SECOND,
    TROOP_MOVEMENT_SPEED_FACTOR,
)
from clashbot.engine.cards import CARD_SPECS
from clashbot.engine.geometry import Vec2
from clashbot.engine.geometry import tiles_per_minute_to_tiles_per_tick
from clashbot.engine.replay import CompactReplay
from clashbot.engine.simulation import GameEngine, GameOptions


class SimulationTests(unittest.TestCase):
    def make_engine(self):
        return GameEngine(options=GameOptions(placement_delay_ticks=0))

    def first_troop_slot(self, engine, side=SIDE_BLUE):
        for index, card_id in enumerate(engine.players[side].hand()):
            if CARD_SPECS[card_id].kind != "spell":
                return index, card_id
        raise AssertionError("expected at least one troop/building in hand")

    def test_play_card_spends_elixir_and_cycles_hand(self):
        engine = self.make_engine()
        initial_hand = list(engine.players[SIDE_BLUE].hand())
        hand_slot, card_id = self.first_troop_slot(engine)
        engine.submit_play(SIDE_BLUE, hand_slot, 9.0, 24.0)
        engine.step()
        cycled_hand = list(engine.players[SIDE_BLUE].hand())
        self.assertNotEqual(cycled_hand, initial_hand)
        self.assertEqual(cycled_hand[hand_slot], engine.players[SIDE_BLUE].deck[4])
        self.assertLess(engine.players[SIDE_BLUE].elixir_milli, 5000)
        self.assertTrue(any(entity.card_id == card_id for entity in engine.entities.values()))

    def test_placement_snaps_to_clicked_tile_center(self):
        engine = self.make_engine()
        hand_slot, card_id = self.first_troop_slot(engine)
        engine.submit_play(SIDE_BLUE, hand_slot, 9.1, 24.9)
        engine.step()
        entity = next(entity for entity in engine.entities.values() if entity.card_id == card_id)
        self.assertEqual((entity.pos.x, entity.pos.y), (9.5, 24.5))

    def test_invalid_deep_placement_is_rejected(self):
        engine = self.make_engine()
        initial_hand = list(engine.players[SIDE_BLUE].hand())
        hand_slot, card_id = self.first_troop_slot(engine)
        self.assertFalse(engine.can_place(SIDE_BLUE, card_id, Vec2(9.0, 8.0)))
        engine.submit_play(SIDE_BLUE, hand_slot, 9.0, 8.0)
        engine.step()
        self.assertEqual(engine.players[SIDE_BLUE].hand(), initial_hand)

    def test_building_placement_requires_three_by_three_clearance(self):
        engine = self.make_engine()
        self.assertFalse(engine.can_place(SIDE_BLUE, "cannon", Vec2(3.5, 17.5)))
        self.assertTrue(engine.can_place(SIDE_BLUE, "cannon", Vec2(3.5, 18.5)))
        self.assertFalse(engine.can_place(SIDE_BLUE, "cannon", Vec2(0.5, 24.5)))

    def test_fireball_damages_units_after_travel(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(9.5, 10.5))
        target = next(entity for entity in engine.entities.values() if entity.side == SIDE_RED and entity.card_id == "knight")
        target_id = target.entity_id

        engine._cast_spell(SIDE_BLUE, CARD_SPECS["fireball"], Vec2(9.5, 10.5))
        engine.step(70)
        target = engine.entities[target_id]
        self.assertEqual(target.hp, 1766 - 688)

    def test_fireball_does_not_knock_back_heavy_units(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_RED, CARD_SPECS["giant"], Vec2(9.5, 10.5))
        giant = next(entity for entity in engine.entities.values() if entity.card_id == "giant")
        old_pos = giant.pos
        engine._apply_knockback(giant, Vec2(8.0, 10.5), 1.8)
        self.assertEqual(giant.pos, old_pos)

    def test_cannon_lifetime_decays_hp_over_time(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["cannon"], Vec2(9.5, 24.5))
        cannon = next(entity for entity in engine.entities.values() if entity.card_id == "cannon")
        start_hp = cannon.hp
        engine.step(30)
        self.assertLess(cannon.hp, start_hp)
        self.assertGreater(cannon.lifetime_ticks_remaining, 0)

    def test_cannon_uses_inner_collision_circle_and_cosmetic_footprint(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["cannon"], Vec2(9.5, 24.5))
        cannon = next(entity for entity in engine.entities.values() if entity.card_id == "cannon")
        self.assertAlmostEqual(cannon.radius, 0.6)
        self.assertAlmostEqual(cannon.footprint, 2.4)

    def test_hidden_card_stats_are_exposed(self):
        engine = self.make_engine()
        snapshot = engine.snapshot()
        archer = snapshot["cards"]["archers"]["units"][0]
        minion = snapshot["cards"]["minions"]["units"][0]
        archer_spec = CARD_SPECS["archers"].units[0]
        minion_spec = CARD_SPECS["minions"].units[0]
        fireball = snapshot["cards"]["fireball"]
        fireball_spec = CARD_SPECS["fireball"].spell
        assert fireball_spec is not None

        self.assertEqual(archer["firstAttackTicks"], archer_spec.first_attack_ticks)
        self.assertEqual(archer["loadTimeTicks"], archer_spec.load_time_ticks)
        self.assertEqual(minion["hitSpeedTicks"], minion_spec.hit_speed_ticks)
        self.assertEqual(fireball["spellCrownTowerDamage"], fireball_spec.crown_tower_damage)
        self.assertEqual(fireball["spellKnockbackTiles"], fireball_spec.knockback_tiles)

    def test_new_target_waits_first_attack_delay_before_hit(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(9.5, 10.0))
        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(9.5, 9.2))
        blue_knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_BLUE and entity.card_id == "knight")
        red_knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_RED and entity.card_id == "knight")
        blue_knight.deploy_ticks_remaining = 0
        red_knight.deploy_ticks_remaining = 0
        assert blue_knight.first_attack_ticks is not None

        engine.step()
        self.assertEqual(red_knight.hp, red_knight.max_hp)
        self.assertEqual(blue_knight.attack_windup_target_id, red_knight.entity_id)
        self.assertFalse(blue_knight.target_locked)

        engine.step(blue_knight.first_attack_ticks - 1)
        self.assertEqual(red_knight.hp, red_knight.max_hp)

        engine.step()
        self.assertEqual(red_knight.hp, red_knight.max_hp - blue_knight.damage)
        self.assertTrue(blue_knight.target_locked)

    def test_center_troop_moves_diagonally_toward_bridge(self):
        engine = self.make_engine()
        hand_slot, card_id = self.first_troop_slot(engine)
        engine.submit_play(SIDE_BLUE, hand_slot, 9.0, 24.0)
        engine.step(35)
        entity = next(entity for entity in engine.entities.values() if entity.card_id == card_id)
        self.assertGreater(entity.pos.x, 9.5)
        self.assertLess(entity.pos.y, 24.5)

    def test_clear_bridge_path_to_princess_tower_stays_straight(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(3.5, 24.5))
        knight = next(entity for entity in engine.entities.values() if entity.card_id == "knight")
        knight.deploy_ticks_remaining = 0

        bridge_xs = []
        bridge_facing_xs = []
        for _ in range(290):
            if 13.5 < knight.pos.y < 18.5:
                bridge_xs.append(knight.pos.x)
                bridge_facing_xs.append(knight.facing_x)
            engine.step()

        self.assertTrue(bridge_xs)
        self.assertLess(max(abs(x - 3.5) for x in bridge_xs), 1e-6)
        self.assertLess(max(abs(x) for x in bridge_facing_xs), 1e-6)
        self.assertLess(knight.pos.y, 14.0)

    def test_cannon_in_front_of_bridge_does_not_block_crossing(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["cannon"], Vec2(3.5, 18.5))
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(3.5, 24.5))
        cannon = next(entity for entity in engine.entities.values() if entity.card_id == "cannon")
        knight = next(entity for entity in engine.entities.values() if entity.card_id == "knight")
        cannon.deploy_ticks_remaining = 0
        knight.deploy_ticks_remaining = 0

        crossed = None
        for _ in range(700):
            engine.step()
            if knight.pos.y < 15.0:
                crossed = knight.pos
                break

        self.assertIsNotNone(crossed)
        assert crossed is not None
        self.assertGreaterEqual(crossed.x, 2.5)
        self.assertLessEqual(crossed.x, 4.5)

    def test_troop_movement_uses_global_speed_factor(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(9.5, 24.5))
        knight = next(entity for entity in engine.entities.values() if entity.card_id == "knight")
        knight.deploy_ticks_remaining = 0
        old_pos = knight.pos
        engine._move_entity(knight, None)
        expected = (
            tiles_per_minute_to_tiles_per_tick(knight.speed_tiles_per_minute, TICKS_PER_SECOND)
            * TROOP_MOVEMENT_SPEED_FACTOR
        )
        self.assertAlmostEqual(old_pos.distance_to(knight.pos), expected)

    def test_air_units_resolve_collision_with_each_other(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["minions"], Vec2(9.5, 24.5))
        minions = [entity for entity in engine.entities.values() if entity.card_id == "minions"]
        for minion in minions:
            minion.pos = Vec2(9.5, 24.5)
        engine._resolve_collisions()
        distances = [
            left.pos.distance_to(right.pos)
            for index, left in enumerate(minions)
            for right in minions[index + 1 :]
        ]
        self.assertTrue(any(distance > 0 for distance in distances))

    def test_unlocked_tower_target_can_be_pulled_by_nearer_troop(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(3.5, 10.0))
        blue_knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_BLUE and entity.card_id == "knight")
        blue_knight.deploy_ticks_remaining = 0
        engine._validate_or_acquire_target(blue_knight)
        tower_target_id = blue_knight.target_id
        self.assertIsNotNone(tower_target_id)
        self.assertEqual(engine.entities[tower_target_id].kind, "tower")

        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(3.5, 9.2))
        red_knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_RED and entity.card_id == "knight")
        red_knight.deploy_ticks_remaining = 0
        engine._validate_or_acquire_target(blue_knight)
        self.assertEqual(blue_knight.target_id, red_knight.entity_id)

    def test_locked_tower_target_does_not_retarget_to_nearer_troop(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(3.5, 10.0))
        blue_knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_BLUE and entity.card_id == "knight")
        blue_knight.deploy_ticks_remaining = 0
        engine._validate_or_acquire_target(blue_knight)
        tower_target_id = blue_knight.target_id
        blue_knight.target_locked = True

        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(3.5, 9.2))
        red_knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_RED and entity.card_id == "knight")
        red_knight.deploy_ticks_remaining = 0
        engine._validate_or_acquire_target(blue_knight)
        self.assertEqual(blue_knight.target_id, tower_target_id)

    def test_king_tower_requires_activation_before_targeting(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["giant"], Vec2(9.0, 7.5))
        giant = next(entity for entity in engine.entities.values() if entity.card_id == "giant")
        giant.deploy_ticks_remaining = 0
        red_king = engine._king_entity(SIDE_RED)
        engine.step()
        self.assertIsNone(red_king.target_id)

        engine._damage_entity(SIDE_BLUE, red_king, 1)
        engine.step()
        self.assertIsNone(red_king.target_id)
        engine.step(120)
        self.assertEqual(red_king.target_id, giant.entity_id)

    def test_double_elixir_starts_at_sudden_death(self):
        engine = self.make_engine()
        blue = engine.players[SIDE_BLUE]
        blue.elixir_milli = 0
        engine.tick = SUDDEN_DEATH_START_TICKS - 1
        engine.step()
        before = blue.elixir_milli
        engine.step()
        self.assertGreater(blue.elixir_milli - before, before)

    def test_sudden_death_any_tower_destroyed_wins(self):
        engine = self.make_engine()
        engine.tick = SUDDEN_DEATH_START_TICKS
        tower = engine._tower_entity(SIDE_RED, "left_princess")
        engine._damage_entity(SIDE_BLUE, tower, tower.hp)
        engine._cleanup_dead()
        self.assertTrue(engine.game_over)
        self.assertEqual(engine.winner, SIDE_BLUE)
        self.assertEqual(engine.end_reason, "tower destroyed")

    def test_tiebreaker_lowest_tower_hp_loses(self):
        engine = self.make_engine()
        red_tower = engine._tower_entity(SIDE_RED, "left_princess")
        red_tower.hp = 100
        engine.tick = MATCH_END_TICKS - 1
        engine.step()
        self.assertTrue(engine.game_over)
        self.assertEqual(engine.winner, SIDE_BLUE)
        self.assertEqual(engine.end_reason, "tiebreaker")

    def test_tiebreaker_draw_when_lowest_tower_hp_equal(self):
        engine = self.make_engine()
        engine.tick = MATCH_END_TICKS - 1
        engine.step()
        self.assertTrue(engine.game_over)
        self.assertIsNone(engine.winner)
        self.assertEqual(engine.end_reason, "tiebreaker draw")

    def test_king_tower_death_ends_game_and_removes_all_side_towers(self):
        engine = self.make_engine()
        red_king = engine._king_entity(SIDE_RED)
        engine._damage_entity(SIDE_BLUE, red_king, red_king.hp)
        engine._cleanup_dead()
        self.assertTrue(engine.game_over)
        self.assertEqual(engine.winner, SIDE_BLUE)
        self.assertFalse(
            any(entity.side == SIDE_RED and entity.kind == "tower" for entity in engine.entities.values())
        )

    def test_game_over_stops_timer(self):
        engine = self.make_engine()
        red_king = engine._king_entity(SIDE_RED)
        engine._damage_entity(SIDE_BLUE, red_king, red_king.hp)
        engine._cleanup_dead()
        ended_tick = engine.tick
        engine.step(10)
        self.assertEqual(engine.tick, ended_tick)

    def test_replay_command_stream_is_deterministic(self):
        first = self.make_engine()
        first.submit_play(SIDE_BLUE, 0, 9.0, 24.0)
        first.step(8)
        first.submit_play(SIDE_RED, 0, 9.0, 8.0)
        first.step(120)
        replay_dict = first.export_replay()

        replay = CompactReplay.from_dict(replay_dict)
        second = GameEngine.from_replay(replay, options=GameOptions(placement_delay_ticks=0))
        second.run_replay_commands(replay, until_tick=first.tick)
        self.assertEqual(first.state_hash(), second.state_hash())


if __name__ == "__main__":
    unittest.main()
