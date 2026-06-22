import os
import unittest

from clashbot.engine.cards import CARD_SPECS
from clashbot.engine.constants import SIDE_BLUE, SIDE_RED
from clashbot.engine.geometry import Vec2
from clashbot.engine.simulation import GameEngine, GameOptions


RUN_BALANCE_BENCHMARKS = os.environ.get("CLASHBOT_RUN_BALANCE_BENCHMARKS") == "1"

BRIDGE_LEFT = Vec2(3.5, 17.5)
RED_LEFT_PRINCESS = Vec2(3.5, 6.5)


EXPECTED_NAKED_BRIDGE = {
    "minions": {"princess_hp": 2517},
    "knight": {"princess_hp": 1638, "princess_hits": 7},
    "archers": {"princess_hp": 2604},
    "mini_pekka": {"princess_hp": 32, "princess_hits": 4},
    "wizard": {"princess_hp": 2209, "princess_hits": 3},
    "giant": {"princess_hp": 0, "king_hits": 1},
    "musketeer": {"princess_hp": 1967, "princess_hits": 5},
    "fireball": {"princess_hp": 2880},
}


def run_naked_bridge(card_id, max_ticks=2400):
    engine = GameEngine(options=GameOptions(placement_delay_ticks=0))
    card = CARD_SPECS[card_id]

    if card.kind == "spell":
        engine._cast_spell(SIDE_BLUE, card, RED_LEFT_PRINCESS)
    else:
        engine._spawn_card_units(SIDE_BLUE, card, BRIDGE_LEFT)
        for entity in engine.entities.values():
            if entity.side == SIDE_BLUE and entity.card_id == card_id:
                entity.deploy_ticks_remaining = 0

    princess_hits = 0
    king_hits = 0
    last_princess_hp = tower_hp(engine, "left_princess")
    last_king_hp = tower_hp(engine, "king")

    for _ in range(max_ticks):
        engine.step()
        princess_hp = tower_hp(engine, "left_princess")
        king_hp = tower_hp(engine, "king")
        if princess_hp < last_princess_hp:
            princess_hits += 1
        if king_hp < last_king_hp:
            king_hits += 1
        last_princess_hp = princess_hp
        last_king_hp = king_hp

        if not any(entity.side == SIDE_BLUE for entity in engine.entities.values()) and not engine.projectiles:
            break

    return {
        "princess_hp": tower_hp(engine, "left_princess"),
        "king_hp": tower_hp(engine, "king"),
        "princess_hits": princess_hits,
        "king_hits": king_hits,
        "ticks": engine.tick,
    }


def tower_hp(engine, role):
    tower = engine._tower_entity(SIDE_RED, role)
    return tower.hp if tower is not None else 0


class NakedBridgeBenchmarkTests(unittest.TestCase):
    @unittest.skipUnless(
        RUN_BALANCE_BENCHMARKS,
        "set CLASHBOT_RUN_BALANCE_BENCHMARKS=1 to run balance calibration benchmarks",
    )
    def test_naked_at_bridge_expected_princess_tower_damage(self):
        for card_id, expected in EXPECTED_NAKED_BRIDGE.items():
            with self.subTest(card_id=card_id):
                actual = run_naked_bridge(card_id)
                for key, value in expected.items():
                    self.assertEqual(actual[key], value, actual)

    def test_naked_bridge_benchmark_harness_smoke(self):
        actual = run_naked_bridge("mini_pekka")
        self.assertGreaterEqual(actual["princess_hp"], 0)
        self.assertLessEqual(actual["princess_hp"], 3052)
        self.assertGreater(actual["princess_hits"], 0)


if __name__ == "__main__":
    unittest.main()
