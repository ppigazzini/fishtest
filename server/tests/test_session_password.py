# ruff: noqa: ANN201, D100, D101, D102
import base64
import json
import unittest

import itsdangerous
import test_support
from ui_user_test_case import UiUserTestCase

from fishtest.http.cookie_session import SESSION_COOKIE_NAME
from fishtest.http.session_middleware import _enforce_size_limit

NEW_PASSWORD = "Corr3ct-H0rse-Battery-Staple-Zebra!"
GATED_PAGE = "/tests/machines?q=abc"


class TestSessionFollowsThePassword(UiUserTestCase):
    username = "TestSessionUser"

    def setUp(self):
        super().setUp()
        self.userdb = self.rundb.userdb

    def tearDown(self):
        self.userdb.users.update_one(
            {"username": self.username},
            {"$set": {"password": self.password, "blocked": False}},
        )
        self.userdb.clear_cache()

    def logged_in_client(self):
        client = test_support.make_test_client(
            rundb=self.rundb,
            include_api=True,
            include_views=True,
        )
        page = client.get("/login")
        response = client.post(
            "/login",
            data={
                "username": self.username,
                "password": self.password,
                "csrf_token": test_support.extract_csrf_token(page.text),
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        return client

    def post_profile(self, client, *, old_password=None, new_password=""):
        page = client.get("/user")
        return client.post(
            "/user",
            data={
                "csrf_token": test_support.extract_csrf_token(page.text),
                "user": self.username,
                "old_password": self.password if old_password is None else old_password,
                "password": new_password,
                "password2": new_password,
                "email": "",
                "tests_repo": self.tests_repo,
            },
            follow_redirects=False,
        )

    def opens_a_gated_page(self, client):
        return client.get(GATED_PAGE, follow_redirects=False).status_code == 200

    def opens_gated_json(self, client):
        return client.post("/api/actions", json={}).status_code == 200

    def session_cookie(self, client):
        return client.cookies.get(SESSION_COOKIE_NAME)

    def session_data(self, client):
        payload = self.session_cookie(client).strip('"').split(".", 1)[0]
        return json.loads(base64.b64decode(payload))

    def test_login_opens_gated_routes(self):
        client = self.logged_in_client()
        self.assertTrue(self.opens_a_gated_page(client))
        self.assertTrue(self.opens_gated_json(client))

    def test_password_change_ends_every_other_session(self):
        changer, other = self.logged_in_client(), self.logged_in_client()
        self.assertEqual(
            self.post_profile(changer, new_password=NEW_PASSWORD).status_code, 302
        )
        self.assertFalse(self.opens_gated_json(other))
        self.assertFalse(self.opens_a_gated_page(other))

    def test_ended_session_is_a_logged_out_session(self):
        changer, other = self.logged_in_client(), self.logged_in_client()
        self.post_profile(changer, new_password=NEW_PASSWORD)
        response = other.get("/user", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["location"].startswith("/login?next="))
        # The next request finds no user in the cookie either.
        self.assertNotIn(self.username, str(self.session_data(other)))

    def test_public_page_still_serves_an_ended_session(self):
        changer, other = self.logged_in_client(), self.logged_in_client()
        self.post_profile(changer, new_password=NEW_PASSWORD)
        response = other.get("/tests", follow_redirects=False)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="run-tables-poller"', response.text)

    def test_browser_that_changed_the_password_stays_logged_in(self):
        changer = self.logged_in_client()
        self.post_profile(changer, new_password=NEW_PASSWORD)
        self.assertTrue(self.opens_a_gated_page(changer))
        self.assertTrue(self.opens_gated_json(changer))

    def test_new_login_works_after_the_change(self):
        changer = self.logged_in_client()
        self.post_profile(changer, new_password=NEW_PASSWORD)
        self.password = NEW_PASSWORD
        try:
            self.assertTrue(self.opens_a_gated_page(self.logged_in_client()))
        finally:
            self.password = type(self).password

    def test_refused_change_keeps_every_session(self):
        changer, other = self.logged_in_client(), self.logged_in_client()
        self.post_profile(changer, old_password="wrong", new_password=NEW_PASSWORD)
        self.post_profile(changer, new_password="weak")
        self.assertTrue(self.opens_gated_json(other))
        self.assertTrue(self.opens_a_gated_page(other))

    def test_profile_save_without_a_new_password_keeps_every_session(self):
        changer, other = self.logged_in_client(), self.logged_in_client()
        self.assertEqual(self.post_profile(changer).status_code, 302)
        self.assertTrue(self.opens_a_gated_page(other))

    def test_password_changed_in_the_database_ends_sessions_too(self):
        client = self.logged_in_client()
        self.userdb.users.update_one(
            {"username": self.username},
            {"$set": {"password": NEW_PASSWORD}},
        )
        self.assertFalse(self.opens_gated_json(client))
        self.assertFalse(self.opens_a_gated_page(client))

    def test_session_from_before_this_check_counts_as_logged_out(self):
        client = self.logged_in_client()
        test_support.set_session(client, {"user": self.username})
        self.assertFalse(self.opens_gated_json(client))
        self.assertFalse(self.opens_a_gated_page(client))

    def test_session_of_a_removed_user_counts_as_logged_out(self):
        client = self.logged_in_client()
        test_support.set_session(client, {"user": "NoSuchSessionUser"})
        self.assertFalse(self.opens_a_gated_page(client))

    def test_cookie_does_not_reveal_the_password(self):
        session = self.session_data(self.logged_in_client())
        self.assertNotIn(self.password, json.dumps(session))


class TestSessionSizeLimit(unittest.TestCase):
    def test_trimming_a_large_session_keeps_the_login(self):
        signer = itsdangerous.TimestampSigner("test-secret")
        payload = {
            "user": "SomeUser",
            "password_stamp": "a" * 64,
            "padding": "x" * 8000,
        }
        trimmed = _enforce_size_limit(payload, signer)
        self.assertEqual(trimmed["user"], "SomeUser")
        self.assertEqual(trimmed["password_stamp"], "a" * 64)
        self.assertNotIn("padding", trimmed)


if __name__ == "__main__":
    unittest.main()
