import unittest

import util


class OAuthUserLinkingTest(unittest.TestCase):
    def setUp(self):
        self.rundb = util.get_rundb()

    def tearDown(self):
        self.rundb.userdb.users.delete_many(
            {"username": {"$in": ["OAuthExisting", "OAuthNew"]}}
        )
        self.rundb.userdb.user_cache.delete_many(
            {"username": {"$in": ["OAuthExisting", "OAuthNew"]}}
        )

    def test_link_oauth_to_existing_user_by_verified_email(self):
        self.rundb.userdb.create_user(
            "OAuthExisting",
            "secret",
            "oauth-existing@example.com",
            "https://github.com/official-stockfish/Stockfish",
        )
        user = self.rundb.userdb.get_user("OAuthExisting")
        user["pending"] = False
        self.rundb.userdb.save_user(user)

        token = self.rundb.userdb.get_or_create_user_from_oauth(
            "google",
            "google-sub-123",
            email="oauth-existing@example.com",
            email_verified=True,
            preferred_username="OAuthExisting",
        )
        self.assertTrue("error" not in token)
        self.assertEqual(token["username"], "OAuthExisting")

        user2 = self.rundb.userdb.get_user("OAuthExisting")
        self.assertEqual(user2["oauth"]["google"]["sub"], "google-sub-123")

    def test_create_new_user_from_oauth_is_pending(self):
        token = self.rundb.userdb.get_or_create_user_from_oauth(
            "github",
            "github-sub-999",
            email="oauth-new@example.com",
            email_verified=True,
            preferred_username="OAuthNew",
            login="oauth-new",
        )
        self.assertTrue("error" in token)
        self.assertTrue("Account pending for user:" in token["error"])

        created = self.rundb.userdb.find_by_email("oauth-new@example.com")
        self.assertIsNotNone(created)
        self.assertIn("oauth", created)
        self.assertEqual(created["oauth"]["github"]["sub"], "github-sub-999")


if __name__ == "__main__":
    unittest.main()
