import tempfile
import unittest
from pathlib import Path

from clashbot.engine.cards import CARD_SPECS
from clashbot.engine.constants import SIDE_BLUE, SIDE_RED
from clashbot.two_player_runtime import TwoPlayerRuntime


BLUE_TOKEN = "blue-token"
RED_TOKEN = "red-token"
CUSTOM_DECK = tuple(list(CARD_SPECS.keys())[:8])


class TwoPlayerRuntimeTests(unittest.TestCase):
    def make_runtime(self, tmpdir):
        return TwoPlayerRuntime(history_path=Path(tmpdir) / "games.jsonl")

    def join_both(self, runtime):
        runtime.join({"token": BLUE_TOKEN, "side": SIDE_BLUE, "name": "Jia Ming"})
        runtime.join({"token": RED_TOKEN, "side": SIDE_RED, "name": "Aaron Cheng"})

    def start_match(self, runtime):
        self.join_both(runtime)
        runtime.set_ready({"token": BLUE_TOKEN, "ready": True})
        return runtime.set_ready({"token": RED_TOKEN, "ready": True})

    def test_players_join_choose_decks_and_ready_starts_match(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self.make_runtime(tmpdir)
            self.join_both(runtime)

            state = runtime.set_deck({"token": BLUE_TOKEN, "deck": list(CUSTOM_DECK)})
            self.assertEqual(state["remote"]["players"][SIDE_BLUE]["deck"], list(CUSTOM_DECK))
            self.assertEqual(state["remote"]["phase"], "lobby")

            runtime.set_ready({"token": BLUE_TOKEN, "ready": True})
            state = runtime.set_ready({"token": RED_TOKEN, "ready": True})
            self.assertEqual(state["remote"]["phase"], "running")
            self.assertEqual(state["players"][SIDE_BLUE]["deck"], list(CUSTOM_DECK))

    def test_unjoined_tokens_cannot_play(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self.make_runtime(tmpdir)
            self.start_match(runtime)

            with self.assertRaises(ValueError):
                runtime.play({"token": "not-a-player", "handSlot": 0, "x": 9.5, "y": 24.5})

    def test_finished_match_records_history_with_crowns_and_decks(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            runtime = self.make_runtime(tmpdir)
            self.start_match(runtime)

            with runtime.condition:
                red_king = runtime.engine._king_entity(SIDE_RED)
                runtime.engine._damage_entity(SIDE_BLUE, red_king, red_king.hp)
                runtime.engine._cleanup_dead()
                runtime._finish_match_locked()

            state = runtime.state(token=BLUE_TOKEN)
            record = state["remote"]["history"][0]
            self.assertEqual(record["outcome"]["winner"], SIDE_BLUE)
            self.assertEqual(record["outcome"]["blue"]["crownsWon"], 3)
            self.assertEqual(record["outcome"]["red"]["crownsLost"], 3)
            self.assertEqual(record["decks"][SIDE_BLUE], list(runtime.players[SIDE_BLUE].deck))
            self.assertTrue((Path(tmpdir) / "games.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
