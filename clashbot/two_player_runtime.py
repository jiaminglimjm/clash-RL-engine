from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import secrets
import threading
import time
from typing import Dict, List, Optional, Tuple

from .engine.cards import CARD_SPECS, DEFAULT_DECK
from .engine.constants import ENGINE_VERSION, SIDE_BLUE, SIDE_RED, SIDES, TICKS_PER_SECOND
from .engine.simulation import GameEngine, GameOptions


PACKAGE_ROOT = Path(__file__).resolve().parent
DEFAULT_HISTORY_PATH = PACKAGE_ROOT / "data" / "two_player_games.jsonl"
DEFAULT_PLAYER_NAMES = {
    SIDE_BLUE: "Player 1",
    SIDE_RED: "Player 2",
}
CONNECTED_SECONDS = 8.0
STALE_SECONDS = 60.0
LONG_POLL_SECONDS = 1.0
MAX_HISTORY_ITEMS = 100


@dataclass
class RemotePlayer:
    side: str
    default_name: str
    token: str = ""
    name: str = ""
    deck: Tuple[str, ...] = DEFAULT_DECK
    ready: bool = False
    last_seen: float = 0.0

    @property
    def display_name(self) -> str:
        return self.name or self.default_name


class TwoPlayerRuntime:
    def __init__(
        self,
        options: Optional[GameOptions] = None,
        history_path: Optional[Path] = None,
    ) -> None:
        self.options = options or GameOptions(placement_delay_ticks=3, lockstep_delay_ticks=0)
        self.history_path = Path(history_path) if history_path is not None else DEFAULT_HISTORY_PATH
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.players: Dict[str, RemotePlayer] = {
            side: RemotePlayer(side=side, default_name=DEFAULT_PLAYER_NAMES[side])
            for side in SIDES
        }
        self.engine = self._new_engine()
        self.phase = "lobby"
        self.revision = 1
        self.match_number = 0
        self.match_id = "lobby"
        self.match_started_at: Optional[datetime] = None
        self.match_started_mono: Optional[float] = None
        self.running = True
        self.history_error: Optional[str] = None
        self.history = self._load_history()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._started = False

    def start(self) -> None:
        self._started = True
        self._thread.start()

    def stop(self) -> None:
        self.running = False
        with self.condition:
            self.condition.notify_all()
        if self._started:
            self._thread.join(timeout=1.0)

    def state(self, token: str = "", since: Optional[int] = None) -> Dict:
        with self.condition:
            self._touch_locked(token)
            if since is not None and since >= self.revision:
                deadline = time.monotonic() + LONG_POLL_SECONDS
                while self.running and since >= self.revision:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    self.condition.wait(timeout=remaining)
            self._touch_locked(token)
            return self._snapshot_locked(token)

    def history_state(self) -> Dict:
        with self.lock:
            return {
                "history": list(reversed(self.history[-MAX_HISTORY_ITEMS:])),
                "historyPath": str(self.history_path),
                "historyError": self.history_error,
            }

    def join(self, payload: Dict) -> Dict:
        side = str(payload.get("side", "")).lower()
        token = self._payload_token(payload)
        name = str(payload.get("name", "")).strip()[:48]
        with self.condition:
            if side not in SIDES and side not in ("spectator", ""):
                raise ValueError("side must be blue, red, or spectator")
            if side in SIDES:
                self._join_side_locked(side, token, name)
            self._touch_locked(token)
            self._bump_locked()
            return self._snapshot_locked(token)

    def set_deck(self, payload: Dict) -> Dict:
        token = self._payload_token(payload, required=True)
        deck = self._validate_deck(payload.get("deck"))
        with self.condition:
            side = self._require_player_locked(token)
            if self.phase == "running":
                raise ValueError("deck changes are only allowed before the match starts")
            if self.phase == "ended":
                self._new_lobby_locked(bump=False)
            player = self.players[side]
            player.deck = tuple(deck)
            player.ready = False
            self.engine = self._new_engine()
            self._bump_locked()
            return self._snapshot_locked(token)

    def set_ready(self, payload: Dict) -> Dict:
        token = self._payload_token(payload, required=True)
        ready = bool(payload.get("ready", True))
        with self.condition:
            side = self._require_player_locked(token)
            if self.phase == "running":
                raise ValueError("the match is already running")
            if self.phase == "ended":
                self._new_lobby_locked(bump=False)
            if ready:
                self._validate_deck(self.players[side].deck)
            self.players[side].ready = ready
            if self._both_players_ready_locked():
                self._start_match_locked()
            else:
                self._bump_locked()
            return self._snapshot_locked(token)

    def play(self, payload: Dict) -> Dict:
        token = self._payload_token(payload, required=True)
        with self.condition:
            side = self._require_player_locked(token)
            if self.phase != "running":
                raise ValueError("match is not running")
            command = self.engine.submit_play(
                side=side,
                hand_slot=int(payload["handSlot"]),
                x=float(payload["x"]),
                y=float(payload["y"]),
                client_tick=int(payload["clientTick"]) if payload.get("clientTick") is not None else None,
            )
            self._bump_locked()
            return {
                "queued": True,
                "executeTick": command.execute_tick,
                "sequence": command.sequence,
                "state": self._snapshot_locked(token),
            }

    def leave(self, payload: Dict) -> Dict:
        token = self._payload_token(payload, required=True)
        with self.condition:
            side = self._side_for_token_locked(token)
            if side is None:
                return self._snapshot_locked(token)
            player = self.players[side]
            player.token = ""
            player.name = ""
            player.ready = self.phase == "running"
            player.last_seen = 0.0
            if self.phase == "ended":
                self._new_lobby_locked(bump=False)
            if self.phase != "running":
                self.engine = self._new_engine()
            self._bump_locked()
            return self._snapshot_locked(token)

    def new_lobby(self, payload: Dict) -> Dict:
        token = self._payload_token(payload, required=True)
        with self.condition:
            self._require_player_locked(token)
            if self.phase == "running" and not bool(payload.get("force")):
                raise ValueError("match is running")
            self._new_lobby_locked()
            return self._snapshot_locked(token)

    def _loop(self) -> None:
        frame_time = 1.0 / TICKS_PER_SECOND
        next_time = time.monotonic()
        while self.running:
            now = time.monotonic()
            if now >= next_time:
                with self.condition:
                    if self.phase == "running":
                        self.engine.step()
                        if self.engine.game_over:
                            self._finish_match_locked()
                        else:
                            self._bump_locked()
                next_time += frame_time
                if next_time < now - frame_time:
                    next_time = now + frame_time
            else:
                time.sleep(min(0.005, next_time - now))

    def _join_side_locked(self, side: str, token: str, name: str) -> None:
        player = self.players[side]
        now = time.monotonic()
        replacing = player.token and player.token != token
        occupied = replacing and (now - player.last_seen) <= STALE_SECONDS
        if occupied:
            raise ValueError("%s is already occupied by %s" % (side, player.display_name))
        if replacing and self.phase == "running":
            player.ready = True
        elif replacing:
            player.ready = False
        player.token = token
        if name:
            player.name = name
        elif not player.name:
            player.name = player.default_name
        player.last_seen = now

    def _payload_token(self, payload: Dict, required: bool = False) -> str:
        token = str(payload.get("token") or "").strip()
        if not token and not required:
            token = secrets.token_urlsafe(18)
        if required and not token:
            raise ValueError("missing player token")
        return token

    def _require_player_locked(self, token: str) -> str:
        side = self._side_for_token_locked(token)
        if side is None:
            raise ValueError("join blue or red before sending match commands")
        self.players[side].last_seen = time.monotonic()
        return side

    def _side_for_token_locked(self, token: str) -> Optional[str]:
        if not token:
            return None
        for side, player in self.players.items():
            if player.token == token:
                return side
        return None

    def _touch_locked(self, token: str) -> None:
        side = self._side_for_token_locked(token)
        if side is not None:
            self.players[side].last_seen = time.monotonic()

    def _validate_deck(self, raw_deck: object) -> Tuple[str, ...]:
        if isinstance(raw_deck, tuple):
            deck = raw_deck
        elif isinstance(raw_deck, list):
            deck = tuple(str(card_id) for card_id in raw_deck)
        else:
            raise ValueError("deck must be a list of 8 card ids")
        if len(deck) != 8:
            raise ValueError("deck must contain exactly 8 cards")
        if len(set(deck)) != len(deck):
            raise ValueError("deck cannot contain duplicate cards")
        unknown = [card_id for card_id in deck if card_id not in CARD_SPECS]
        if unknown:
            raise ValueError("unknown card id: %s" % ", ".join(unknown))
        return deck

    def _both_players_ready_locked(self) -> bool:
        return all(player.token and player.ready for player in self.players.values())

    def _start_match_locked(self) -> None:
        self.match_number += 1
        self.match_id = "match-%d-%d" % (self.match_number, int(time.time()))
        self.match_started_at = datetime.now(timezone.utc)
        self.match_started_mono = time.monotonic()
        self.engine = self._new_engine(seed=secrets.randbits(63))
        self.phase = "running"
        self._bump_locked()

    def _finish_match_locked(self) -> None:
        if self.phase != "running":
            return
        self.phase = "ended"
        for player in self.players.values():
            player.ready = False
        record = self._history_record_locked()
        self.history.append(record)
        self.history = self.history[-MAX_HISTORY_ITEMS:]
        self._append_history_record(record)
        self._bump_locked()

    def _new_lobby_locked(self, bump: bool = True) -> None:
        self.phase = "lobby"
        self.match_id = "lobby"
        self.match_started_at = None
        self.match_started_mono = None
        for player in self.players.values():
            player.ready = False
        self.engine = self._new_engine()
        if bump:
            self._bump_locked()

    def _new_engine(self, seed: int = 0) -> GameEngine:
        return GameEngine(
            blue_deck=self.players[SIDE_BLUE].deck,
            red_deck=self.players[SIDE_RED].deck,
            options=self.options,
            seed=seed,
        )

    def _snapshot_locked(self, token: str) -> Dict:
        snapshot = self.engine.snapshot()
        for side in SIDES:
            player = self.engine.players[side]
            next_card_id = player.deck[player.order[4]] if len(player.order) > 4 else None
            snapshot["players"][side]["nextCard"] = (
                {
                    "cardId": next_card_id,
                    "name": CARD_SPECS[next_card_id].display_name,
                }
                if next_card_id is not None
                else None
            )
        snapshot["stateHash"] = self.engine.state_hash()
        viewer_side = self._side_for_token_locked(token)
        now = time.monotonic()
        snapshot["remote"] = {
            "phase": self.phase,
            "revision": self.revision,
            "matchId": self.match_id,
            "viewerSide": viewer_side,
            "serverTimeMs": int(time.time() * 1000),
            "tickRate": TICKS_PER_SECOND,
            "engineVersion": ENGINE_VERSION,
            "players": {
                side: self._player_snapshot_locked(player, now, viewer_side)
                for side, player in self.players.items()
            },
            "cardCatalog": self._card_catalog(),
            "history": list(reversed(self.history[-12:])),
            "historyPath": str(self.history_path),
            "historyError": self.history_error,
        }
        return snapshot

    def _player_snapshot_locked(self, player: RemotePlayer, now: float, viewer_side: Optional[str]) -> Dict:
        age = now - player.last_seen if player.token else None
        return {
            "side": player.side,
            "name": player.display_name,
            "occupied": bool(player.token),
            "connected": bool(player.token and age is not None and age <= CONNECTED_SECONDS),
            "takeoverAvailable": bool(player.token and age is not None and age > STALE_SECONDS),
            "ready": player.ready,
            "deck": list(player.deck),
            "lastSeenSeconds": round(age, 1) if age is not None else None,
            "isYou": viewer_side == player.side,
        }

    def _card_catalog(self) -> List[Dict]:
        cards = []
        for card_id, spec in CARD_SPECS.items():
            cards.append(
                {
                    "cardId": card_id,
                    "name": spec.display_name,
                    "elixir": spec.elixir,
                    "kind": spec.kind,
                }
            )
        return sorted(cards, key=lambda item: (item["elixir"], item["name"]))

    def _history_record_locked(self) -> Dict:
        started_at = self.match_started_at or datetime.now(timezone.utc)
        ended_at = datetime.now(timezone.utc)
        ended_tick = self.engine.ended_tick if self.engine.ended_tick is not None else self.engine.tick
        crowns = self._crowns_locked()
        return {
            "matchId": self.match_id,
            "startedAt": self._isoformat(started_at),
            "endedAt": self._isoformat(ended_at),
            "durationSeconds": round(ended_tick / float(TICKS_PER_SECOND), 3),
            "engineVersion": ENGINE_VERSION,
            "players": {
                side: self.players[side].display_name
                for side in SIDES
            },
            "decks": {
                side: list(self.players[side].deck)
                for side in SIDES
            },
            "outcome": {
                "winner": self.engine.winner,
                "reason": self.engine.end_reason,
                "crowns": crowns,
                SIDE_BLUE: {
                    "crownsWon": crowns[SIDE_BLUE],
                    "crownsLost": crowns[SIDE_RED],
                },
                SIDE_RED: {
                    "crownsWon": crowns[SIDE_RED],
                    "crownsLost": crowns[SIDE_BLUE],
                },
            },
        }

    def _crowns_locked(self) -> Dict[str, int]:
        def living_roles(side: str) -> set:
            return {
                entity.tower_role
                for entity in self.engine.entities.values()
                if entity.side == side and entity.kind == "tower" and entity.tower_role is not None
            }

        def crowns_for(side: str) -> int:
            enemy = SIDE_RED if side == SIDE_BLUE else SIDE_BLUE
            roles = living_roles(enemy)
            if "king" not in roles:
                return 3
            return max(0, min(3, 3 - len(roles)))

        return {
            SIDE_BLUE: crowns_for(SIDE_BLUE),
            SIDE_RED: crowns_for(SIDE_RED),
        }

    def _load_history(self) -> List[Dict]:
        records: List[Dict] = []
        if not self.history_path.exists():
            return records
        try:
            for line in self.history_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line:
                    records.append(json.loads(line))
            return records[-MAX_HISTORY_ITEMS:]
        except (OSError, json.JSONDecodeError) as exc:
            self.history_error = str(exc)
            return records[-MAX_HISTORY_ITEMS:]

    def _append_history_record(self, record: Dict) -> None:
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with self.history_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(",", ":")) + "\n")
            self.history_error = None
        except OSError as exc:
            self.history_error = str(exc)

    def _bump_locked(self) -> None:
        self.revision += 1
        self.condition.notify_all()

    def _isoformat(self, value: datetime) -> str:
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
