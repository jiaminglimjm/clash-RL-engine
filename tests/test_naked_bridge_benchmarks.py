import os
import unittest

from clashbot.engine.cards import CARD_SPECS
from clashbot.engine.constants import SIDE_BLUE, SIDE_RED, SUDDEN_DEATH_START_TICKS
from clashbot.engine.geometry import Vec2
from clashbot.engine.simulation import GameEngine, GameOptions


RUN_BALANCE_BENCHMARKS = os.environ.get("CLASHBOT_RUN_BALANCE_BENCHMARKS") == "1"
BENCHMARK_REPORT_MODE = os.environ.get("CLASHBOT_NAKED_BRIDGE_REPORT", "").lower()

BRIDGE_LEFT = Vec2(3.5, 17.5)
RED_LEFT_PRINCESS = Vec2(3.5, 6.5)
OLD_NAKED_BRIDGE_CUTOFF_TICKS = 2400
NAKED_BRIDGE_MAX_TICKS = OLD_NAKED_BRIDGE_CUTOFF_TICKS
PRINCESS_NAKED_BRIDGE_MAX_TICKS = SUDDEN_DEATH_START_TICKS


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
    "baby_dragon": {"princess_hp": 2247},
    "flying_machine": {"princess_hp": 2368},
    "princess": {"princess_hp": 0, "king_hp": 0},
    "elixir_collector": {"princess_hp": 3052},
    "mirror": {"princess_hp": 3052},
    "night_witch": {"princess_hp": 1300},
    "skeleton_army": {"princess_hp": 541},
    "goblin_barrel": {"princess_hp": 2212},
    "balloon": {"princess_hp": 252},
    "lightning": {"princess_hp": 2787},
    "arrows": {"princess_hp": 2977},
    "zap": {"princess_hp": 3004},
    "golem": {"princess_hp": 0, "king_hp": 3669},
    "lava_hound": {"princess_hp": 1260},
}


def naked_bridge_max_ticks(card_id):
    if card_id == "princess":
        return PRINCESS_NAKED_BRIDGE_MAX_TICKS
    return NAKED_BRIDGE_MAX_TICKS


def run_naked_bridge(card_id, max_ticks=None):
    if max_ticks is None:
        max_ticks = naked_bridge_max_ticks(card_id)
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


def naked_bridge_outcomes():
    outcomes = []
    for card_id, expected in EXPECTED_NAKED_BRIDGE.items():
        actual = run_naked_bridge(card_id)
        mismatches = {
            key: {"expected": value, "actual": actual[key]}
            for key, value in expected.items()
            if actual[key] != value
        }
        outcomes.append(
            {
                "card_id": card_id,
                "expected": expected,
                "actual": actual,
                "mismatches": mismatches,
            }
        )
    return outcomes


def naked_bridge_report(outcomes, failed_only=False):
    total = len(outcomes)
    failed = sum(1 for outcome in outcomes if outcome["mismatches"])
    passed = total - failed
    lines = [
        "Naked bridge benchmark: %d passed, %d failed, %d total, max_ticks=%d, princess_max_ticks=%d"
        % (passed, failed, total, NAKED_BRIDGE_MAX_TICKS, PRINCESS_NAKED_BRIDGE_MAX_TICKS)
    ]
    for outcome in outcomes:
        if failed_only and not outcome["mismatches"]:
            continue
        status = "FAIL" if outcome["mismatches"] else "PASS"
        parts = [
            "%s %s" % (status, outcome["card_id"]),
            "expected=%s" % outcome["expected"],
            "actual={princess_hp:%s, king_hp:%s, ticks:%s}" % (
                outcome["actual"]["princess_hp"],
                outcome["actual"]["king_hp"],
                outcome["actual"]["ticks"],
            ),
        ]
        if outcome["mismatches"]:
            parts.append("mismatches=%s" % outcome["mismatches"])
        lines.append(" | ".join(parts))
    return "\n".join(lines)


def tower_hp(engine, role):
    tower = engine._tower_entity(SIDE_RED, role)
    return tower.hp if tower is not None else 0


class NakedBridgeBenchmarkTests(unittest.TestCase):
    @unittest.skipUnless(
        RUN_BALANCE_BENCHMARKS,
        "set CLASHBOT_RUN_BALANCE_BENCHMARKS=1 to run balance calibration benchmarks",
    )
    def test_naked_at_bridge_expected_princess_tower_damage(self):
        outcomes = naked_bridge_outcomes()
        if BENCHMARK_REPORT_MODE in ("all", "failed", "failures", "failed-only"):
            failed_only = BENCHMARK_REPORT_MODE in ("failed", "failures", "failed-only")
            print("\n" + naked_bridge_report(outcomes, failed_only=failed_only))
            return
        for outcome in outcomes:
            with self.subTest(card_id=outcome["card_id"]):
                self.assertEqual(outcome["mismatches"], {}, outcome)

    def test_naked_bridge_benchmark_harness_smoke(self):
        actual = run_naked_bridge("mini_pekka")
        self.assertGreaterEqual(actual["princess_hp"], 0)
        self.assertLessEqual(actual["princess_hp"], 3052)
        self.assertGreater(actual["princess_hits"], 0)

    def test_princess_benchmark_runs_past_old_cutoff(self):
        actual = run_naked_bridge("princess")
        self.assertGreater(actual["ticks"], OLD_NAKED_BRIDGE_CUTOFF_TICKS)
        self.assertLessEqual(actual["ticks"], PRINCESS_NAKED_BRIDGE_MAX_TICKS)
        self.assertEqual(actual["princess_hp"], 0)
        self.assertEqual(actual["king_hp"], 0)

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
            "baby_dragon",
            "flying_machine",
            "princess",
            "elixir_collector",
            "mirror",
            "night_witch",
            "skeleton_army",
            "goblin_barrel",
            "balloon",
            "lightning",
            "arrows",
            "zap",
            "golem",
            "lava_hound",
        ):
            with self.subTest(card_id=card_id):
                actual = run_naked_bridge(card_id)
                self.assertGreaterEqual(actual["princess_hp"], 0)
                self.assertLessEqual(actual["princess_hp"], 3052)
                self.assertGreater(actual["ticks"], 0)


if __name__ == "__main__":
    unittest.main()
