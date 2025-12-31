from __future__ import annotations

import unittest

from fishtest.kvstore import KeyValueStore


class CreateKeyValueStoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.kvstore = KeyValueStore(db_name="fishtest_tests", collection="test_kvstore")

    def tearDown(self) -> None:
        self.kvstore.clear()

    def test_invalid_invocation(self) -> None:
        with self.assertRaises(ValueError):
            KeyValueStore()

    def test_clear(self) -> None:
        self.kvstore["a"] = 1
        self.kvstore.clear()
        self.assertEqual(len(self.kvstore), 0)

    def test_getsetitem(self) -> None:
        with self.assertRaises(KeyError):
            self.kvstore["a"]
        self.kvstore["a"] = 1
        self.assertEqual(self.kvstore["a"], 1)

    def test_delitem(self) -> None:
        with self.assertRaises(KeyError):
            del self.kvstore["a"]
        self.kvstore["a"] = 1
        del self.kvstore["a"]
        with self.assertRaises(KeyError):
            del self.kvstore["a"]

    def test_contains(self) -> None:
        self.assertNotIn("a", self.kvstore)
        self.kvstore["a"] = 1
        self.assertIn("a", self.kvstore)
        del self.kvstore["a"]
        self.assertNotIn("a", self.kvstore)

    def test_len(self) -> None:
        self.kvstore["a"] = 1
        self.assertEqual(len(self.kvstore), 1)
        self.kvstore["b"] = 1
        self.assertEqual(len(self.kvstore), 2)

    def test_get(self) -> None:
        with self.assertRaises(KeyError):
            self.kvstore["a"]
        self.assertEqual(self.kvstore.get("a", 10), 10)

    def test_pop(self) -> None:
        with self.assertRaises(KeyError):
            self.kvstore.pop("a")
        x = self.kvstore.pop("a", 1)
        self.assertEqual(x, 1)

    def test_popitem(self) -> None:
        with self.assertRaises(KeyError):
            self.kvstore.popitem()
        self.kvstore["a"] = 1
        self.kvstore["b"] = 2
        x = self.kvstore.popitem()
        self.assertIn(x, {("a", 1), ("b", 2)})
        self.assertNotIn(x, self.kvstore.items())

    def test_iter(self) -> None:
        self.kvstore["a"] = 1
        self.kvstore["b"] = 2
        self.assertEqual(set(iter(self.kvstore)), {"a", "b"})

    def test_keys(self) -> None:
        self.kvstore["a"] = 1
        self.kvstore["b"] = 2
        self.assertEqual(set(self.kvstore.keys()), {"a", "b"})

    def test_values(self) -> None:
        self.kvstore["a"] = 1
        self.kvstore["b"] = 2
        self.assertEqual(set(self.kvstore.values()), {1, 2})

    def test_items(self) -> None:
        self.kvstore["a"] = 1
        self.kvstore["b"] = 2
        self.assertEqual(set(self.kvstore.items()), {("a", 1), ("b", 2)})

    @classmethod
    def tearDownClass(cls) -> None:
        cls.kvstore.drop()
