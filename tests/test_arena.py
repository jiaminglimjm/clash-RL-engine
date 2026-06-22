import unittest

from clashbot.engine.arena import BANNED, NORMAL, RIVER, is_bridge_tile, is_bridge_world, tile_type
from clashbot.engine.geometry import Vec2


class ArenaTests(unittest.TestCase):
    def test_edge_row_only_center_is_placeable(self):
        self.assertEqual(tile_type(31, 5), BANNED)
        self.assertEqual(tile_type(31, 6), NORMAL)
        self.assertEqual(tile_type(31, 11), NORMAL)
        self.assertEqual(tile_type(31, 12), BANNED)

    def test_river_has_two_bridge_columns(self):
        self.assertEqual(tile_type(15, 2), RIVER)
        self.assertEqual(tile_type(15, 3), NORMAL)
        self.assertTrue(is_bridge_tile(15, 3))
        self.assertEqual(tile_type(16, 14), NORMAL)
        self.assertTrue(is_bridge_tile(16, 14))

    def test_bridge_world_corridor_has_half_tile_shoulders(self):
        self.assertTrue(is_bridge_world(Vec2(2.5, 15.5)))
        self.assertTrue(is_bridge_world(Vec2(4.5, 15.5)))
        self.assertFalse(is_bridge_world(Vec2(2.45, 15.5)))
        self.assertFalse(is_bridge_world(Vec2(4.55, 15.5)))


if __name__ == "__main__":
    unittest.main()
