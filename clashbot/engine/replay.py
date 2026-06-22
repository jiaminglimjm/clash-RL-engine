from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from .constants import ENGINE_VERSION, SIDE_BLUE, SIDE_RED
from .entities import ScheduledCommand, side_from_code


@dataclass(frozen=True)
class CompactReplay:
    version: str
    blue_deck: Tuple[str, ...]
    red_deck: Tuple[str, ...]
    commands: Tuple[ScheduledCommand, ...]
    seed: int = 0

    def to_dict(self) -> Dict:
        return {
            "v": self.version,
            "seed": self.seed,
            "bd": list(self.blue_deck),
            "rd": list(self.red_deck),
            "cmd": [command.compact_tuple() for command in self.commands],
        }

    @classmethod
    def from_dict(cls, payload: Dict) -> "CompactReplay":
        commands: List[ScheduledCommand] = []
        for raw in payload.get("cmd", []):
            execute_tick, side_code, hand_slot, x100, y100, sequence = raw
            side = side_from_code(side_code)
            commands.append(
                ScheduledCommand(
                    execute_tick=execute_tick,
                    sequence=sequence,
                    side=side,
                    hand_slot=hand_slot,
                    x=x100 / 100.0,
                    y=y100 / 100.0,
                )
            )
        return cls(
            version=payload.get("v", ENGINE_VERSION),
            seed=int(payload.get("seed", 0)),
            blue_deck=tuple(payload.get("bd", ())),
            red_deck=tuple(payload.get("rd", ())),
            commands=tuple(commands),
        )


def build_replay(
    blue_deck: Iterable[str],
    red_deck: Iterable[str],
    accepted_commands: Iterable[ScheduledCommand],
    seed: int = 0,
    version: Optional[str] = None,
) -> CompactReplay:
    return CompactReplay(
        version=version or ENGINE_VERSION,
        seed=seed,
        blue_deck=tuple(blue_deck),
        red_deck=tuple(red_deck),
        commands=tuple(accepted_commands),
    )

