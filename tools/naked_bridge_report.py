#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
import sys
from typing import Dict, Iterable, List, Optional, Tuple


ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS_PATH = ROOT / "tests" / "test_naked_bridge_benchmarks.py"


def _load_benchmarks():
    sys.path.insert(0, str(ROOT))
    spec = importlib.util.spec_from_file_location("naked_bridge_benchmarks", BENCHMARKS_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load %s" % BENCHMARKS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _format_pair(actual: int, expected: Optional[int]) -> str:
    if expected is None:
        return "%d / -" % actual
    return "%d / %d" % (actual, expected)


def _build_rows(card_ids: Iterable[str], max_ticks: int) -> List[Dict]:
    benchmarks = _load_benchmarks()
    rows = []
    for card_id in card_ids:
        expected = benchmarks.EXPECTED_NAKED_BRIDGE[card_id]
        actual = benchmarks.run_naked_bridge(card_id, max_ticks=max_ticks)
        mismatches = {
            key: (actual[key], value)
            for key, value in expected.items()
            if actual[key] != value
        }
        hp_diff = sum(
            abs(actual_value - expected_value)
            for key, (actual_value, expected_value) in mismatches.items()
            if key.endswith("_hp")
        )
        other_diffs = [
            "%s %s/%s" % (key, actual_value, expected_value)
            for key, (actual_value, expected_value) in mismatches.items()
            if not key.endswith("_hp")
        ]
        rows.append(
            {
                "card": card_id,
                "passed": not mismatches,
                "actual": actual,
                "expected": expected,
                "hp_diff": hp_diff,
                "other": ", ".join(other_diffs) if other_diffs else "-",
            }
        )
    rows.sort(key=lambda row: (row["passed"], -row["hp_diff"], row["card"]))
    return rows


def _print_table(rows: List[Dict]) -> None:
    headers = (
        "Result",
        "Card",
        "Princess HP actual/expected",
        "King HP actual/expected",
        "HP diff",
        "Other diffs",
        "Ticks",
    )
    table: List[Tuple[str, ...]] = []
    for row in rows:
        actual = row["actual"]
        expected = row["expected"]
        table.append(
            (
                "PASS" if row["passed"] else "FAIL",
                row["card"],
                _format_pair(actual["princess_hp"], expected.get("princess_hp")),
                _format_pair(actual["king_hp"], expected.get("king_hp")),
                str(row["hp_diff"]),
                row["other"],
                str(actual["ticks"]),
            )
        )

    widths = [
        max(len(str(value)) for value in [header] + [row[index] for row in table])
        for index, header in enumerate(headers)
    ]
    header_line = "  ".join(header.ljust(widths[index]) for index, header in enumerate(headers))
    divider = "  ".join("-" * width for width in widths)
    print(header_line)
    print(divider)
    for row in table:
        print("  ".join(value.ljust(widths[index]) for index, value in enumerate(row)))


def main(argv: Optional[List[str]] = None) -> int:
    benchmarks = _load_benchmarks()
    parser = argparse.ArgumentParser(
        description="Print naked-at-bridge benchmark results without unittest assertions."
    )
    parser.add_argument("--max-ticks", type=int, default=2400)
    parser.add_argument("--strict", action="store_true", help="exit 1 when any expected metric mismatches")
    parser.add_argument(
        "cards",
        nargs="*",
        help="optional card ids to run; defaults to every EXPECTED_NAKED_BRIDGE entry",
    )
    args = parser.parse_args(argv)

    unknown = [card_id for card_id in args.cards if card_id not in benchmarks.EXPECTED_NAKED_BRIDGE]
    if unknown:
        print("unknown card id(s): %s" % ", ".join(sorted(unknown)), file=sys.stderr)
        return 2

    card_ids = args.cards or list(benchmarks.EXPECTED_NAKED_BRIDGE)
    rows = _build_rows(card_ids, args.max_ticks)
    _print_table(rows)

    failures = sum(1 for row in rows if not row["passed"])
    print()
    print("%d passed, %d failed" % (len(rows) - failures, failures))
    if args.strict and failures:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
