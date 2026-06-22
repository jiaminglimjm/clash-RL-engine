import unittest

from clashbot.engine.constants import SIDE_BLUE, SIDE_RED
from clashbot.engine.cards import CARD_SPECS
from clashbot.engine.geometry import Vec2
from clashbot.engine.replay import CompactReplay
from clashbot.engine.simulation import GameEngine, GameOptions


class SimulationTests(unittest.TestCase):
    def make_engine(self):
        return GameEngine(options=GameOptions(placement_delay_ticks=0))

    def test_play_card_spends_elixir_and_cycles_hand(self):
        engine = self.make_engine()
        self.assertEqual(engine.players[SIDE_BLUE].hand(), ["knight", "archers", "minions", "fireball"])
        engine.submit_play(SIDE_BLUE, 0, 9.0, 24.0)
        engine.step()
        self.assertEqual(engine.players[SIDE_BLUE].hand(), ["archers", "minions", "fireball", "cannon"])
        self.assertLess(engine.players[SIDE_BLUE].elixir_milli, 5000)
        self.assertTrue(any(entity.card_id == "knight" for entity in engine.entities.values()))

    def test_placement_snaps_to_clicked_tile_center(self):
        engine = self.make_engine()
        engine.submit_play(SIDE_BLUE, 0, 9.1, 24.9)
        engine.step()
        knight = next(entity for entity in engine.entities.values() if entity.card_id == "knight")
        self.assertEqual((knight.pos.x, knight.pos.y), (9.5, 24.5))

    def test_invalid_deep_placement_is_rejected(self):
        engine = self.make_engine()
        self.assertFalse(engine.can_place(SIDE_BLUE, "knight", Vec2(9.0, 8.0)))
        engine.submit_play(SIDE_BLUE, 0, 9.0, 8.0)
        engine.step()
        self.assertEqual(engine.players[SIDE_BLUE].hand(), ["knight", "archers", "minions", "fireball"])

    def test_fireball_damages_units_after_travel(self):
        engine = self.make_engine()
        engine.submit_play(SIDE_RED, 0, 9.0, 10.0)
        engine.step()
        red_knights = [entity for entity in engine.entities.values() if entity.side == SIDE_RED and entity.card_id == "knight"]
        self.assertEqual(len(red_knights), 1)
        target_id = red_knights[0].entity_id

        engine.submit_play(SIDE_BLUE, 3, 9.0, 10.0)
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

    def test_center_troop_moves_diagonally_toward_bridge(self):
        engine = self.make_engine()
        engine.submit_play(SIDE_BLUE, 0, 9.0, 24.0)
        engine.step(35)
        knight = next(entity for entity in engine.entities.values() if entity.card_id == "knight")
        self.assertGreater(knight.pos.x, 9.5)
        self.assertLess(knight.pos.y, 24.5)

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
        self.assertEqual(red_king.target_id, giant.entity_id)

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
