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
    "spear_goblins": {"princess_hp": 2728},
    "goblins": {"princess_hp": 2692},
    "bomber": {"princess_hp": 2827},
    "skeletons": {"princess_hp": 3052},
    "barbarians": {"princess_hp": 0, "king_hp": 2712},
    "witch": {"princess_hp": 1351},
    "fire_spirit": {"princess_hp": 2845},
    "bats": {"princess_hp": 3052},
    "mega_minion": {"princess_hp": 2740},
    "tombstone": {"princess_hp": 3052},
    "goblin_hut": {"princess_hp": 3052},
    "valkyrie": {"princess_hp": 1190},
    "mini_pekka": {"princess_hp": 32, "princess_hits": 4},
    "wizard": {"princess_hp": 2209, "princess_hits": 3},
    "giant": {"princess_hp": 0, "king_hp": 4571},
    "musketeer": {"princess_hp": 1967, "princess_hits": 5},
    "fireball": {"princess_hp": 2880},
    "royal_giant": {"princess_hp": 0, "king_hp": 4517},
    "minion_horde": {"princess_hp": 163},
    "goblin_gang": {"princess_hp": 1960},
    "elite_barbarians": {"princess_hp": 0, "king_hp": 2520},
    "hog_rider": {"princess_hp": 833},
    "dart_goblin": {"princess_hp": 2599},
    "pekka": {"princess_hp": 0, "king_hp": 1560},
    "rocket": {"princess_hp": 2710},
    "bomb_tower": {"princess_hp": 3052},
}


def run_naked_bridge(card_id, max_ticks=2400):
    engine = GameEngine(options=GameOptions(placement_delay_ticks=0))
    card = CARD_SPECS[card_id]

    if card.kind == "spell":
        engine._cast_spell(SIDE_BLUE, card, RED_LEFT_PRINCESS)
    else:
        engine._deploy_card_units(SIDE_BLUE, card, BRIDGE_LEFT)

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

    def test_added_retro_cards_naked_bridge_harness_smoke(self):
        for card_id in (
            "royal_giant",
            "minion_horde",
            "goblin_gang",
            "elite_barbarians",
            "hog_rider",
            "dart_goblin",
            "pekka",
            "rocket",
            "bomb_tower",
        ):
            with self.subTest(card_id=card_id):
                actual = run_naked_bridge(card_id)
                self.assertGreaterEqual(actual["princess_hp"], 0)
                self.assertLessEqual(actual["princess_hp"], 3052)
                self.assertGreater(actual["ticks"], 0)


if __name__ == "__main__":
    unittest.main()
