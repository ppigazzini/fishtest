import unittest

from fishtest.userdb import UserDb


class _FakeUsersCollection:
    def __init__(self, docs_by_username):
        self._docs_by_username = docs_by_username

    def find_one(self, query):
        username = query.get("username")
        if isinstance(username, str):
            doc = self._docs_by_username.get(username)
            return dict(doc) if doc is not None else None
        email = query.get("email")
        if isinstance(email, str):
            for doc in self._docs_by_username.values():
                if doc.get("email") == email:
                    return dict(doc)
        return None

    def find_one_and_update(self, query, update):
        _id = query.get("_id")
        if _id is None:
            return None

        doc = None
        for candidate in self._docs_by_username.values():
            if candidate.get("_id") == _id:
                doc = candidate
                break
        if doc is None:
            return None

        def missing_api_key(d):
            if "api_key" not in d:
                return True
            value = d.get("api_key")
            return value is None or value == ""

        if not missing_api_key(doc):
            return None

        set_doc = update.get("$set")
        if not isinstance(set_doc, dict):
            return None
        if "api_key" in set_doc:
            doc["api_key"] = set_doc["api_key"]
        return dict(doc)


class _FakeDb:
    def __init__(self, users):
        self._users = users

    def __getitem__(self, key):
        if key == "users":
            return self._users
        # Unused by these tests, but required by UserDb.__init__.
        return object()


class _UserDbForTest(UserDb):
    def __init__(self, db, *, generated_key):
        super().__init__(db)
        self._generated_key = generated_key
        self._cleared = False

    def _generate_api_key(self):
        return self._generated_key

    def clear_cache(self):
        self._cleared = True
        super().clear_cache()


class EnsureWorkerApiKeyTest(unittest.TestCase):
    def tearDown(self):
        # UserDb uses a class-level cache shared across instances.
        UserDb.cache.clear()

    def test_returns_none_for_missing_user(self):
        users = _FakeUsersCollection({})
        db = _FakeDb(users)
        userdb = _UserDbForTest(db, generated_key="ft_test")
        self.assertIsNone(userdb.ensure_worker_api_key("missing"))

    def test_returns_existing_key_without_update(self):
        users = _FakeUsersCollection(
            {"u": {"_id": 1, "username": "u", "api_key": "ft_existing"}}
        )
        db = _FakeDb(users)
        userdb = _UserDbForTest(db, generated_key="ft_new")
        self.assertEqual(userdb.ensure_worker_api_key("u"), "ft_existing")
        self.assertFalse(userdb._cleared)

    def test_sets_key_when_missing(self):
        users = _FakeUsersCollection({"u": {"_id": 1, "username": "u"}})
        db = _FakeDb(users)
        userdb = _UserDbForTest(db, generated_key="ft_generated")
        self.assertEqual(userdb.ensure_worker_api_key("u"), "ft_generated")
        self.assertTrue(userdb._cleared)
        # Verify it was stored.
        self.assertEqual(
            users.find_one({"username": "u"}).get("api_key"), "ft_generated"
        )

    def test_race_returns_key_set_by_other(self):
        users = _FakeUsersCollection({"u": {"_id": 1, "username": "u"}})

        original = users.find_one_and_update

        def raced_find_one_and_update(query, update):
            # Simulate another caller winning the race before our atomic update.
            users._docs_by_username["u"]["api_key"] = "ft_other"
            return None

        users.find_one_and_update = raced_find_one_and_update
        db = _FakeDb(users)
        userdb = _UserDbForTest(db, generated_key="ft_generated")
        self.assertEqual(userdb.ensure_worker_api_key("u"), "ft_other")
        # We should not clear cache because we didn't write.
        self.assertFalse(userdb._cleared)
        users.find_one_and_update = original


class IsAccountRestrictedTest(unittest.TestCase):
    def tearDown(self):
        # UserDb uses a class-level cache shared across instances.
        UserDb.cache.clear()

    def test_returns_blocked(self):
        userdb = UserDb(_FakeDb(_FakeUsersCollection({})))
        self.assertEqual(userdb.is_account_restricted({"blocked": True}), "blocked")

    def test_blocked_takes_precedence_over_pending(self):
        userdb = UserDb(_FakeDb(_FakeUsersCollection({})))
        self.assertEqual(
            userdb.is_account_restricted({"blocked": True, "pending": True}),
            "blocked",
        )

    def test_returns_pending(self):
        userdb = UserDb(_FakeDb(_FakeUsersCollection({})))
        self.assertEqual(userdb.is_account_restricted({"pending": True}), "pending")

    def test_returns_none_for_unrestricted(self):
        userdb = UserDb(_FakeDb(_FakeUsersCollection({})))
        self.assertIsNone(
            userdb.is_account_restricted({"blocked": False, "pending": False})
        )


if __name__ == "__main__":
    unittest.main()
