# ClashBot Engine Prototype

This is a deterministic, headless-first Clash Royale-style simulator scaffold with a small localhost debug renderer.

Current scope:

- Arena geometry, bridge/river placement rules, tower layout, elixir, 8-card decks, and 4-card hand cycling.
- Starting cards: Knight, Archers, Minions, Fireball, Cannon, Giant, Musketeer, Mini P.E.K.K.A.
- Data-driven card specs sourced from `legacy_ref/retro_royale_80_cards_stats_v2.xlsx`.
- Tick-based command scheduling for future online play concepts: simulated latency, placement delay, and authoritative synchronization.
- Compact deterministic replay export where the version/header plus command tuples are enough to reconstruct a match.
- Canvas renderer at `localhost:8000` with blue/red circles, labels, and per-card secondary colors.

Run tests:

```bash
python3 -m unittest discover -s tests
```

Run the debug server:

```bash
python3 -m clashbot.debug_server --host 127.0.0.1 --port 8000
```

Open `http://localhost:8000`.

The engine package is intentionally split by responsibility:

- `clashbot.engine.arena`: tile classification, placement legality, bridge helpers.
- `clashbot.engine.cards`: card/tower stats and spawn formations.
- `clashbot.engine.simulation`: deterministic authoritative tick loop.
- `clashbot.engine.replay`: compact replay format.
- `clashbot.debug_server`: stdlib HTTP server for human-in-the-loop checking.

