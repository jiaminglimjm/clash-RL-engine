import unittest

from clashbot.engine.constants import (
    DOUBLE_ELIXIR_START_TICKS,
    MATCH_END_TICKS,
    MELEE_ATTACK_RANGE_FACTOR,
    MELEE_ATTACK_RANGE_MAX_TILES,
    MULTI_UNIT_SPAWN_STAGGER_TICKS,
    SIDE_BLUE,
    SIDE_RED,
    SUDDEN_DEATH_START_TICKS,
    TICKS_PER_SECOND,
    TRIPLE_ELIXIR_START_TICKS,
    TROOP_MOVEMENT_SPEED_FACTOR,
)
from clashbot.engine.cards import CARD_SPECS
from clashbot.engine.geometry import Vec2
from clashbot.engine.geometry import tiles_per_minute_to_tiles_per_tick
from clashbot.engine.replay import CompactReplay
from clashbot.engine.simulation import GameEngine, GameOptions


STARTER_DECK = ("knight", "archers", "minions", "fireball", "cannon", "giant", "musketeer", "mini_pekka")


class SimulationTests(unittest.TestCase):
    def make_engine(self, blue_deck=None, red_deck=None):
        kwargs = {"options": GameOptions(placement_delay_ticks=0, shuffle_initial_hands=False)}
        if blue_deck is not None:
            kwargs["blue_deck"] = blue_deck
        if red_deck is not None:
            kwargs["red_deck"] = red_deck
        return GameEngine(**kwargs)

    def first_troop_slot(self, engine, side=SIDE_BLUE):
        for index, card_id in enumerate(engine.players[side].hand()):
            if CARD_SPECS[card_id].kind != "spell":
                return index, card_id
        raise AssertionError("expected at least one troop/building in hand")

    def test_play_card_spends_elixir_and_cycles_hand(self):
        engine = self.make_engine(blue_deck=STARTER_DECK)
        initial_hand = list(engine.players[SIDE_BLUE].hand())
        expected_replacement = engine.players[SIDE_BLUE].deck[engine.players[SIDE_BLUE].order[4]]
        hand_slot, card_id = self.first_troop_slot(engine)
        engine.submit_play(SIDE_BLUE, hand_slot, 9.0, 24.0)
        engine.step()
        cycled_hand = list(engine.players[SIDE_BLUE].hand())
        self.assertNotEqual(cycled_hand, initial_hand)
        self.assertEqual(cycled_hand[hand_slot], expected_replacement)
        self.assertLess(engine.players[SIDE_BLUE].elixir_milli, 5000)
        self.assertTrue(any(entity.card_id == card_id for entity in engine.entities.values()))

    def test_initial_hand_order_is_seeded_shuffle(self):
        first = GameEngine(
            blue_deck=STARTER_DECK,
            red_deck=STARTER_DECK,
            options=GameOptions(placement_delay_ticks=0),
            seed=12345,
        )
        second = GameEngine(
            blue_deck=STARTER_DECK,
            red_deck=STARTER_DECK,
            options=GameOptions(placement_delay_ticks=0),
            seed=12345,
        )

        self.assertEqual(first.players[SIDE_BLUE].order, second.players[SIDE_BLUE].order)
        self.assertCountEqual(first.players[SIDE_BLUE].order, range(len(STARTER_DECK)))
        self.assertNotEqual(first.players[SIDE_BLUE].order, list(range(len(STARTER_DECK))))

    def test_placement_snaps_to_clicked_tile_center(self):
        engine = self.make_engine(blue_deck=STARTER_DECK)
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

    def test_played_multi_unit_card_spawns_left_to_right_with_stagger(self):
        engine = GameEngine(
            blue_deck=("archers", "knight", "minions", "fireball", "cannon", "giant", "musketeer", "mini_pekka"),
            options=GameOptions(placement_delay_ticks=0, shuffle_initial_hands=False),
        )
        engine.submit_play(SIDE_BLUE, 0, 9.0, 24.0)
        engine.step()
        archers = [entity for entity in engine.entities.values() if entity.card_id == "archers"]
        self.assertEqual(len(archers), 1)
        self.assertLess(archers[0].pos.x, 9.5)
        self.assertEqual(len(engine.pending_spawns), 1)
        self.assertEqual(engine.pending_spawns[0].execute_tick, MULTI_UNIT_SPAWN_STAGGER_TICKS)

        engine.step(MULTI_UNIT_SPAWN_STAGGER_TICKS - 1)
        self.assertEqual(len([entity for entity in engine.entities.values() if entity.card_id == "archers"]), 1)

        engine.step()
        archers = sorted(
            [entity for entity in engine.entities.values() if entity.card_id == "archers"],
            key=lambda entity: entity.entity_id,
        )
        self.assertEqual(len(archers), 2)
        self.assertLess(archers[0].pos.x, archers[1].pos.x)

    def test_tower_pathing_uses_circular_collision_radius_not_footprint(self):
        engine = self.make_engine()
        tower = engine._tower_entity(SIDE_RED, "left_princess")
        assert tower is not None
        tower.radius = 1.0
        tower.footprint_tiles = 4.0
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(0.5, tower.pos.y + 1.8))
        knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_BLUE and entity.card_id == "knight")
        knight.deploy_ticks_remaining = 0

        near_square_edge = Vec2(6.5, tower.pos.y + 1.8)
        through_circle = Vec2(6.5, tower.pos.y + 1.0)

        self.assertIsNone(engine._first_blocking_building(knight, near_square_edge, None))
        self.assertEqual(engine._first_blocking_building(knight, through_circle, None), tower)

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

    def test_melee_attack_range_factor_applies_to_spawned_troop_entities(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["knight"], Vec2(9.5, 24.5))
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["archers"], Vec2(9.5, 24.5))

        knight_spec = CARD_SPECS["knight"].units[0]
        archer_spec = CARD_SPECS["archers"].units[0]
        knight = next(entity for entity in engine.entities.values() if entity.card_id == "knight")
        archer = next(entity for entity in engine.entities.values() if entity.card_id == "archers")

        self.assertLessEqual(knight_spec.attack_range, MELEE_ATTACK_RANGE_MAX_TILES)
        self.assertAlmostEqual(knight.attack_range, knight_spec.attack_range * MELEE_ATTACK_RANGE_FACTOR)
        self.assertAlmostEqual(archer.attack_range, archer_spec.attack_range)
        self.assertEqual(knight_spec.attack_range, 0.8)

    def test_requested_excel_cards_are_registered(self):
        def ticks(seconds):
            return max(1, int(round(seconds * TICKS_PER_SECOND)))

        expected = {
            "spear_goblins": ("Spear Goblins", "troop", 2, 3, "ground", "all", 133, 81, 120, 5.0, 1.7, 500, 0.0, 1, 0.5, 0.5, 1.2),
            "goblins": ("Goblins", "troop", 2, 4, "ground", "ground", 202, 120, 120, 0.5, 1.1, 0, 0.0, 2, 0.4, 0.2, 0.9),
            "bomber": ("Bomber", "troop", 2, 1, "ground", "ground", 304, 225, 60, 4.5, 1.8, 400, 1.5, 4, 0.5, 0.2, 1.6),
            "skeletons": ("Skeletons", "troop", 1, 3, "ground", "ground", 81, 81, 90, 0.5, 1.0, 0, 0.0, 1, 0.2, 0.5, 0.5),
            "barbarians": ("Barbarians", "troop", 5, 5, "ground", "ground", 670, 192, 60, 0.7, 1.4, 0, 0.0, 4, 0.5, 0.4, 1.0),
            "valkyrie": ("Valkyrie", "troop", 4, 1, "ground", "ground", 1907, 266, 60, 1.2, 1.5, 0, 1.9, 5, 0.5, 0.1, 1.4),
            "fire_spirit": ("Fire Spirit", "troop", 1, 1, "ground", "all", 230, 207, 120, 2.5, 1.0, 400, 1.7, 1, 0.4, 0.2, 0.1),
            "tombstone": ("Tombstone", "building", 3, 1, "building", "none", 529, 0, 0, 0.0, 0.0, 0, 0.0, 0, 1.0, None, None),
            "witch": ("Witch", "troop", 5, 1, "ground", "all", 839, 135, 60, 5.5, 1.1, 600, 1.0, 8, 0.5, 0.7, 0.4),
            "bats": ("Bats", "troop", 2, 5, "air", "all", 81, 81, 120, 1.2, 1.3, 0, 0.0, 1, 0.3, 0.6, 0.7),
            "mega_minion": ("Mega Minion", "troop", 3, 1, "air", "all", 837, 312, 60, 1.6, 1.6, 1000, 0.0, 6, 0.6, 0.4, 1.2),
            "goblin_hut": ("Goblin Hut", "building", 5, 1, "building", "none", 1180, 0, 0, 0.0, 0.0, 0, 0.0, 0, 1.0, None, None),
        }
        engine = self.make_engine()
        for card_id, values in expected.items():
            with self.subTest(card_id=card_id):
                (
                    display_name,
                    kind,
                    elixir,
                    count,
                    movement_type,
                    target_mode,
                    hp,
                    damage,
                    speed,
                    attack_range,
                    hit_speed_seconds,
                    projectile_speed,
                    splash_radius,
                    mass,
                    radius,
                    first_attack_seconds,
                    load_time_seconds,
                ) = values
                spec = CARD_SPECS[card_id]
                unit = spec.units[0]
                self.assertEqual(spec.display_name, display_name)
                self.assertEqual(spec.kind, kind)
                self.assertEqual(spec.elixir, elixir)
                self.assertEqual(unit.movement_type, movement_type)
                self.assertEqual(unit.target_mode, target_mode)
                self.assertEqual(unit.hp, hp)
                self.assertEqual(unit.damage, damage)
                self.assertEqual(unit.speed_tiles_per_minute, speed)
                self.assertEqual(unit.attack_range, attack_range)
                self.assertEqual(unit.hit_speed_ticks, 0 if hit_speed_seconds == 0.0 else ticks(hit_speed_seconds))
                self.assertEqual(unit.projectile_speed_tiles_per_minute, projectile_speed)
                self.assertEqual(unit.splash_radius, splash_radius)
                self.assertEqual(unit.mass, mass)
                self.assertEqual(unit.radius, radius)
                self.assertEqual(unit.sight_range, 0.0 if card_id in ("tombstone", "goblin_hut") else 5.5)
                if first_attack_seconds is not None:
                    self.assertEqual(unit.first_attack_ticks, ticks(first_attack_seconds))
                if load_time_seconds is not None:
                    self.assertEqual(unit.load_time_ticks, ticks(load_time_seconds))
                spawns = engine._expanded_card_unit_spawns(SIDE_BLUE, spec, Vec2(9.5, 24.5))
                self.assertEqual(len(spawns), count)

    def test_requested_added_retro_cards_are_registered(self):
        expected_units = {
            "royal_giant": ("Royal Giant", "troop", 6, 1, "ground", "buildings", 3164, 307, 45, 5.0, 1.8, 1000, 0.0, 18, 0.75, 7.5, 0.7, 1.0),
            "minion_horde": ("Minion Horde", "troop", 5, 6, "air", "all", 230, 107, 90, 2.5, 1.1, 1000, 0.0, 2, 0.5, 5.5, 0.5, 0.7),
            "elite_barbarians": ("Elite Barbarians", "troop", 6, 2, "ground", "ground", 1341, 384, 90, 0.7, 1.4, 0, 0.0, 4, 0.5, 5.5, 0.5, 0.9),
            "hog_rider": ("Hog Rider", "troop", 4, 1, "ground", "buildings", 1697, 317, 120, 0.8, 1.6, 0, 0.0, 4, 0.6, 9.5, 0.6, 1.0),
            "dart_goblin": ("Dart Goblin", "troop", 3, 1, "ground", "all", 261, 151, 120, 6.5, 0.8, 800, 0.0, 3, 0.5, 7.5, 0.35, 0.35),
            "pekka": ("P.E.K.K.A", "troop", 7, 1, "ground", "ground", 3760, 816, 45, 1.2, 1.8, 0, 0.0, 18, 0.75, 5.0, 0.5, 1.3),
            "bomb_tower": ("Bomb Tower", "building", 4, 1, "building", "ground", 1356, 222, 0, 6.0, 1.6, 400, 1.5, 0, 0.6, 5.5, 0.5, 1.1),
        }
        engine = self.make_engine()
        for card_id, values in expected_units.items():
            with self.subTest(card_id=card_id):
                (
                    display_name,
                    kind,
                    elixir,
                    count,
                    movement_type,
                    target_mode,
                    hp,
                    damage,
                    speed,
                    attack_range,
                    hit_speed_seconds,
                    projectile_speed,
                    splash_radius,
                    mass,
                    radius,
                    sight_range,
                    first_attack_seconds,
                    load_time_seconds,
                ) = values
                spec = CARD_SPECS[card_id]
                unit = spec.units[0]
                self.assertEqual(spec.display_name, display_name)
                self.assertEqual(spec.kind, kind)
                self.assertEqual(spec.elixir, elixir)
                self.assertEqual(unit.movement_type, movement_type)
                self.assertEqual(unit.target_mode, target_mode)
                self.assertEqual(unit.hp, hp)
                self.assertEqual(unit.damage, damage)
                self.assertEqual(unit.speed_tiles_per_minute, speed)
                self.assertEqual(unit.attack_range, attack_range)
                self.assertEqual(unit.hit_speed_ticks, self.ticks(hit_speed_seconds))
                self.assertEqual(unit.projectile_speed_tiles_per_minute, projectile_speed)
                self.assertEqual(unit.splash_radius, splash_radius)
                self.assertEqual(unit.mass, mass)
                self.assertEqual(unit.radius, radius)
                self.assertEqual(unit.sight_range, sight_range)
                self.assertEqual(unit.first_attack_ticks, self.ticks(first_attack_seconds))
                self.assertEqual(unit.load_time_ticks, self.ticks(load_time_seconds))
                spawns = engine._expanded_card_unit_spawns(SIDE_BLUE, spec, Vec2(9.5, 24.5))
                self.assertEqual(len(spawns), count)

        goblin_gang = CARD_SPECS["goblin_gang"]
        self.assertEqual(goblin_gang.display_name, "Goblin Gang")
        self.assertEqual(goblin_gang.elixir, 3)
        self.assertEqual(len(goblin_gang.units), 6)
        self.assertEqual([unit.unit_id for unit in goblin_gang.units].count("goblin"), 3)
        self.assertEqual([unit.unit_id for unit in goblin_gang.units].count("spear_goblin"), 3)

        rocket = CARD_SPECS["rocket"]
        self.assertEqual(rocket.kind, "spell")
        self.assertEqual(rocket.elixir, 6)
        assert rocket.spell is not None
        self.assertEqual(rocket.spell.damage, 1484)
        self.assertEqual(rocket.spell.crown_tower_damage, 342)
        self.assertEqual(rocket.spell.radius, 2.0)
        self.assertEqual(rocket.spell.projectile_speed_tiles_per_minute, 350)
        self.assertEqual(rocket.spell.knockback_tiles, 1.8)

    def test_bomb_tower_death_bomb_damages_enemies(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["bomb_tower"], Vec2(9.5, 24.5))
        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(9.5, 22.5))
        tower = next(entity for entity in engine.entities.values() if entity.card_id == "bomb_tower")
        knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_RED and entity.card_id == "knight")

        engine._damage_entity(SIDE_RED, tower, tower.hp)
        engine._cleanup_dead()

        self.assertEqual(knight.hp, knight.max_hp - CARD_SPECS["bomb_tower"].units[0].death_damage)

    def test_requested_second_wave_cards_are_registered(self):
        expected = {
            "baby_dragon": ("Baby Dragon", "troop", 4, 1),
            "flying_machine": ("Flying Machine", "troop", 4, 1),
            "princess": ("Princess", "troop", 3, 1),
            "elixir_collector": ("Elixir Collector", "building", 6, 1),
            "mirror": ("Mirror", "spell", 1, 0),
            "night_witch": ("Night Witch", "troop", 4, 1),
            "skeleton_army": ("Skeleton Army", "troop", 3, 15),
            "goblin_barrel": ("Goblin Barrel", "spell", 3, 3),
            "balloon": ("Balloon", "troop", 5, 1),
            "lightning": ("Lightning", "spell", 6, 0),
            "arrows": ("Arrows", "spell", 3, 0),
            "zap": ("Zap", "spell", 2, 0),
            "golem": ("Golem", "troop", 8, 1),
            "lava_hound": ("Lava Hound", "troop", 7, 1),
        }
        engine = self.make_engine()
        for card_id, (name, kind, elixir, count) in expected.items():
            with self.subTest(card_id=card_id):
                spec = CARD_SPECS[card_id]
                self.assertEqual(spec.display_name, name)
                self.assertEqual(spec.kind, kind)
                self.assertEqual(spec.elixir, elixir)
                if count > 0:
                    spawns = engine._expanded_card_unit_spawns(SIDE_BLUE, spec, Vec2(9.5, 24.5))
                    self.assertEqual(len(spawns), count)
                else:
                    self.assertEqual(len(spec.units), 0)

    def test_mirror_replays_previous_card_with_buffed_stats(self):
        deck = ("knight", "mirror", "archers", "minions", "cannon", "giant", "musketeer", "mini_pekka")
        engine = self.make_engine(blue_deck=deck)

        engine.submit_play(SIDE_BLUE, 0, 9.5, 24.5)
        engine.step()
        self.assertEqual(engine.players[SIDE_BLUE].last_played_card_id, "knight")

        engine.submit_play(SIDE_BLUE, 1, 10.5, 24.5)
        engine.step()

        knights = [entity for entity in engine.entities.values() if entity.card_id == "knight"]
        self.assertEqual(sorted(entity.max_hp for entity in knights), [1766, 1943])
        self.assertEqual(sorted(entity.damage for entity in knights), [202, 222])

    def test_goblin_barrel_spawns_goblins_on_arrival(self):
        engine = self.make_engine()
        engine._cast_spell(SIDE_BLUE, CARD_SPECS["goblin_barrel"], Vec2(9.5, 10.5))
        projectile = next(projectile for projectile in engine.projectiles.values() if projectile.source_card_id == "goblin_barrel")
        self.assertGreaterEqual(projectile.radius, 0.4)
        engine.step(95)

        goblins = [
            entity for entity in engine.entities.values()
            if entity.card_id == "goblin_barrel" and entity.unit_id == "goblin"
        ]
        self.assertEqual(len(goblins), 3)

    def test_lightning_warns_then_hits_three_highest_hp_targets(self):
        engine = self.make_engine()
        for card_id, pos in (
            ("giant", Vec2(8.8, 10.5)),
            ("knight", Vec2(9.5, 10.5)),
            ("musketeer", Vec2(10.2, 10.5)),
            ("skeletons", Vec2(9.5, 11.2)),
        ):
            engine._spawn_card_units(SIDE_RED, CARD_SPECS[card_id], pos)
        entities = {entity.unit_id: entity for entity in engine.entities.values() if entity.side == SIDE_RED}
        giant_hp = entities["giant"].hp
        knight_hp = entities["knight"].hp
        musketeer_hp = entities["musketeer"].hp
        skeleton_hp = next(entity for entity in engine.entities.values() if entity.unit_id == "skeleton").hp

        engine._cast_spell(SIDE_BLUE, CARD_SPECS["lightning"], Vec2(9.5, 10.5))
        strike_interval = self.ticks(0.5)
        engine.step(strike_interval - 1)
        self.assertEqual(entities["giant"].hp, giant_hp)
        projectile = next(projectile for projectile in engine.projectiles.values() if projectile.source_card_id == "lightning")
        self.assertFalse(projectile.effect_done)

        engine.step()
        self.assertLess(entities["giant"].hp, giant_hp)
        self.assertEqual(entities["knight"].hp, knight_hp)
        self.assertEqual(entities["musketeer"].hp, musketeer_hp)
        projectile = next(projectile for projectile in engine.projectiles.values() if projectile.source_card_id == "lightning")
        self.assertFalse(projectile.effect_done)
        self.assertEqual(len(projectile.visual_targets), 1)

        engine.step(strike_interval - 1)
        self.assertEqual(entities["knight"].hp, knight_hp)
        engine.step()
        self.assertLess(entities["knight"].hp, knight_hp)
        self.assertEqual(entities["musketeer"].hp, musketeer_hp)
        projectile = next(projectile for projectile in engine.projectiles.values() if projectile.source_card_id == "lightning")
        self.assertFalse(projectile.effect_done)
        self.assertEqual(len(projectile.visual_targets), 2)

        engine.step(strike_interval)
        self.assertLess(entities["musketeer"].hp, musketeer_hp)
        self.assertEqual(next(entity for entity in engine.entities.values() if entity.unit_id == "skeleton").hp, skeleton_hp)
        projectile = next(projectile for projectile in engine.projectiles.values() if projectile.source_card_id == "lightning")
        self.assertTrue(projectile.effect_done)
        self.assertEqual(len(projectile.visual_targets), 3)

    def test_zap_resolves_immediately(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(9.5, 10.5))
        knight = next(entity for entity in engine.entities.values() if entity.card_id == "knight")

        engine._cast_spell(SIDE_BLUE, CARD_SPECS["zap"], Vec2(9.5, 10.5))

        self.assertEqual(knight.hp, knight.max_hp - CARD_SPECS["zap"].spell.damage)

    def test_elixir_collector_generates_elixir(self):
        engine = GameEngine(options=GameOptions(placement_delay_ticks=0, elixir_regen_multiplier=0))
        engine.players[SIDE_BLUE].elixir_milli = 0
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["elixir_collector"], Vec2(9.5, 24.5))
        collector = next(entity for entity in engine.entities.values() if entity.card_id == "elixir_collector")
        collector.deploy_ticks_remaining = 0

        engine.step(self.ticks(13.0))

        self.assertEqual(engine.players[SIDE_BLUE].elixir_milli, 1000)

    def test_golem_and_lava_hound_death_spawns(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["golem"], Vec2(9.5, 24.5))
        golem = next(entity for entity in engine.entities.values() if entity.card_id == "golem")
        engine._damage_entity(SIDE_RED, golem, golem.hp)
        engine._cleanup_dead()
        self.assertEqual(
            len([entity for entity in engine.entities.values() if entity.unit_id == "golemite"]),
            2,
        )

        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["lava_hound"], Vec2(9.5, 24.5))
        hound = next(entity for entity in engine.entities.values() if entity.card_id == "lava_hound")
        engine._damage_entity(SIDE_RED, hound, hound.hp)
        engine._cleanup_dead()
        self.assertEqual(
            len([entity for entity in engine.entities.values() if entity.unit_id == "lava_pup"]),
            6,
        )

    def test_fire_spirit_self_destructs_after_launching_attack(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["fire_spirit"], Vec2(9.5, 10.5))
        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(9.5, 9.0))
        fire_spirit = next(entity for entity in engine.entities.values() if entity.card_id == "fire_spirit")
        knight = next(entity for entity in engine.entities.values() if entity.side == SIDE_RED and entity.card_id == "knight")
        fire_spirit.deploy_ticks_remaining = 0
        knight.deploy_ticks_remaining = 0

        engine.step((fire_spirit.first_attack_ticks or 0) + 1)

        self.assertNotIn(fire_spirit.entity_id, engine.entities)
        self.assertTrue(any(projectile.source_card_id == "fire_spirit" for projectile in engine.projectiles.values()))

        engine.step(20)

        self.assertEqual(knight.hp, knight.max_hp - CARD_SPECS["fire_spirit"].units[0].damage)

    def test_witch_periodically_summons_skeletons(self):
        engine = self.make_engine()
        for entity in list(engine.entities.values()):
            if entity.side == SIDE_RED and entity.kind == "tower":
                engine.entities.pop(entity.entity_id)
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["witch"], Vec2(9.5, 24.5))
        witch = next(entity for entity in engine.entities.values() if entity.card_id == "witch")
        witch.deploy_ticks_remaining = 0

        engine.step(max(0, self.ticks(1.0) - 1))
        self.assertEqual(self.count_units(engine, "witch", "skeleton"), 0)

        engine.step()
        self.assertEqual(self.count_units(engine, "witch", "skeleton"), 4)

        engine.step(self.ticks(7.0))
        self.assertEqual(self.count_units(engine, "witch", "skeleton"), 8)

    def test_tombstone_periodic_and_death_spawns_skeletons(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["tombstone"], Vec2(9.5, 24.5))
        tombstone = next(entity for entity in engine.entities.values() if entity.card_id == "tombstone")
        tombstone.deploy_ticks_remaining = 0

        engine.step(self.ticks(3.1))
        self.assertEqual(self.count_units(engine, "tombstone", "skeleton"), 2)

        engine._damage_entity(SIDE_RED, tombstone, tombstone.hp)
        engine._cleanup_dead()

        skeletons = [
            entity for entity in engine.entities.values()
            if entity.card_id == "tombstone" and entity.unit_id == "skeleton"
        ]
        self.assertEqual(len(skeletons), 5)
        self.assertTrue(all(entity.deploy_ticks_remaining == 0 for entity in skeletons))

    def test_goblin_hut_spawns_only_when_enemy_is_in_range_and_death_spawns(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["goblin_hut"], Vec2(9.5, 24.5))
        hut = next(entity for entity in engine.entities.values() if entity.card_id == "goblin_hut")
        hut.deploy_ticks_remaining = 0

        engine.step(self.ticks(4.5))
        self.assertEqual(self.count_units(engine, "goblin_hut", "spear_goblin"), 0)
        engine._damage_entity(SIDE_RED, hut, hut.hp)
        engine._cleanup_dead()
        self.assertEqual(self.count_units(engine, "goblin_hut", "spear_goblin"), 0)

        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["goblin_hut"], Vec2(9.5, 24.5))
        hut = next(entity for entity in engine.entities.values() if entity.card_id == "goblin_hut")
        hut.deploy_ticks_remaining = 0
        engine._spawn_card_units(SIDE_RED, CARD_SPECS["knight"], Vec2(9.5, 21.0))
        red_knight = next(
            entity for entity in engine.entities.values()
            if entity.side == SIDE_RED and entity.card_id == "knight"
        )
        red_knight.deploy_ticks_remaining = 0

        engine.step(self.ticks(4.5))
        self.assertEqual(self.count_units(engine, "goblin_hut", "spear_goblin"), 1)

        engine._damage_entity(SIDE_RED, hut, hut.hp)
        engine._cleanup_dead()

        spear_goblins = [
            entity for entity in engine.entities.values()
            if entity.card_id == "goblin_hut" and entity.unit_id == "spear_goblin"
        ]
        self.assertEqual(len(spear_goblins), 4)
        self.assertTrue(all(entity.deploy_ticks_remaining == 0 for entity in spear_goblins))

    def test_tombstone_death_spawns_on_lifetime_expiry(self):
        engine = self.make_engine()
        engine._spawn_card_units(SIDE_BLUE, CARD_SPECS["tombstone"], Vec2(9.5, 24.5))
        tombstone = next(entity for entity in engine.entities.values() if entity.card_id == "tombstone")
        tombstone.deploy_ticks_remaining = 0
        tombstone.lifetime_ticks_remaining = 1

        engine.step()

        self.assertFalse(any(entity.unit_id == "tombstone" for entity in engine.entities.values()))
        self.assertEqual(self.count_units(engine, "tombstone", "skeleton"), 3)

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
        engine = self.make_engine(blue_deck=STARTER_DECK)
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

    def test_elixir_multiplier_phases_match_regulation_and_overtime(self):
        engine = self.make_engine()
        engine.tick = DOUBLE_ELIXIR_START_TICKS - 1
        self.assertEqual(engine._current_elixir_multiplier(), 1)
        engine.tick = DOUBLE_ELIXIR_START_TICKS
        self.assertEqual(engine._current_elixir_multiplier(), 2)
        engine.tick = SUDDEN_DEATH_START_TICKS
        self.assertEqual(engine._current_elixir_multiplier(), 2)
        engine.tick = TRIPLE_ELIXIR_START_TICKS
        self.assertEqual(engine._current_elixir_multiplier(), 3)

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
        deck = ("archers", "knight", "minions", "fireball", "cannon", "giant", "musketeer", "mini_pekka")
        first = GameEngine(blue_deck=deck, red_deck=deck, options=GameOptions(placement_delay_ticks=0))
        first.submit_play(SIDE_BLUE, 0, 9.0, 24.0)
        first.step(8)
        first.submit_play(SIDE_RED, 0, 9.0, 8.0)
        first.step(120)
        replay_dict = first.export_replay()

        replay = CompactReplay.from_dict(replay_dict)
        second = GameEngine.from_replay(replay, options=GameOptions(placement_delay_ticks=0))
        second.run_replay_commands(replay, until_tick=first.tick)
        self.assertEqual(first.state_hash(), second.state_hash())

    def ticks(self, seconds):
        return max(1, int(round(seconds * TICKS_PER_SECOND)))

    def count_units(self, engine, card_id, unit_id):
        return len(
            [
                entity for entity in engine.entities.values()
                if entity.card_id == card_id and entity.unit_id == unit_id
            ]
        )


if __name__ == "__main__":
    unittest.main()
