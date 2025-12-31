import unittest

from fishtest.lru_cache import LRUCache


class CreateLRUCacheTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lru_cache = LRUCache(10)

    def tearDown(self):
        self.lru_cache.clear()

    def test_lru_cache_clear(self):
        self.lru_cache["a"] = 1
        self.lru_cache.clear()
        self.assertEqual(len(self.lru_cache), 0)

    def test_lru_cache_getsetitem(self):
        with self.assertRaises(KeyError):
            self.lru_cache["a"]
        self.lru_cache["a"] = 1
        self.assertEqual(self.lru_cache["a"], 1)

    def test_lru_cache_delitem(self):
        with self.assertRaises(KeyError):
            del self.lru_cache["a"]
        self.lru_cache["a"] = 1
        del self.lru_cache["a"]
        with self.assertRaises(KeyError):
            del self.lru_cache["a"]

    def test_lru_cache_contains(self):
        self.assertNotIn("a", self.lru_cache)
        self.lru_cache["a"] = 1
        self.assertIn("a", self.lru_cache)
        del self.lru_cache["a"]
        self.assertNotIn("a", self.lru_cache)

    def test_lru_cache_len(self):
        self.assertEqual(len(self.lru_cache), 0)
        self.lru_cache["a"] = 1
        self.assertEqual(len(self.lru_cache), 1)
        self.lru_cache["b"] = 1
        self.assertEqual(len(self.lru_cache), 2)

    def test_lru_cache_get(self):
        with self.assertRaises(KeyError):
            self.lru_cache["a"]
        self.assertEqual(self.lru_cache.get("a", 10), 10)

    def test_lru_cache_pop(self):
        with self.assertRaises(KeyError):
            self.lru_cache.pop("a")
        x = self.lru_cache.pop("a", 1)
        self.assertEqual(x, 1)

    def test_lru_cache_popitem(self):
        with self.assertRaises(KeyError):
            self.lru_cache.popitem()
        self.lru_cache["a"] = 1
        self.lru_cache["b"] = 2
        x = self.lru_cache.popitem()
        self.assertIn(x, {("a", 1), ("b", 2)})
        self.assertNotIn(x, self.lru_cache.items())

    def test_lru_cache_iter(self):
        self.lru_cache["a"] = 1
        self.lru_cache["b"] = 2
        self.assertEqual(set(iter(self.lru_cache)), {"a", "b"})

    def test_lru_cache_keys(self):
        self.lru_cache["a"] = 1
        self.lru_cache["b"] = 2
        self.assertEqual(set(self.lru_cache.keys()), {"a", "b"})

    def test_lru_cache_values(self):
        self.lru_cache["a"] = 1
        self.lru_cache["b"] = 2
        self.assertEqual(set(self.lru_cache.values()), {1, 2})

    def test_lru_cache_items(self):
        self.lru_cache["a"] = 1
        self.lru_cache["b"] = 2
        self.assertEqual(set(self.lru_cache.items()), {("a", 1), ("b", 2)})
