# ruff: noqa: ANN201, D100, D101, D102
import base64
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import test_support
from bson.objectid import ObjectId
from ui_user_test_case import UiUserTestCase

from fishtest.http.cookie_session import SESSION_COOKIE_NAME
from fishtest.http.settings import (
    ANONYMOUS_RUN_MAX_AGE_DAYS,
    API_TOKEN_MAX_AGE_DAYS,
)
from fishtest.userdb import API_TOKEN_SHAPE as TOKEN_RE
from utils import delta_update_users

OLD_RUN_ID = str(
    ObjectId.from_datetime(
        datetime.now(UTC) - timedelta(days=ANONYMOUS_RUN_MAX_AGE_DAYS + 1)
    )
)


class _TokenTestCase(UiUserTestCase):
    def setUp(self):
        self.client = test_support.make_test_client(
            rundb=self.rundb,
            include_api=True,
            include_views=True,
        )
        self._ensure_user_can_login()
        self.userdb = self.rundb.userdb
        self.userdb.clear_cache()

    def tearDown(self):
        self.userdb.api_tokens.delete_many({})
        self.userdb.clear_cache()

    def bearer(self, token):
        return {"Authorization": f"Bearer {token}"}

    def approve(self, username):
        self.userdb.users.update_one(
            {"username": username},
            {"$set": {"pending": False}},
        )

    def set_blocked_in_database(self, username, *, blocked):
        # A write that does not go through the app, such as one from a mongo shell.
        self.userdb.users.update_one(
            {"username": username},
            {"$set": {"blocked": blocked}},
        )


class TestApiTokenStore(_TokenTestCase):
    username = "TestTokenStoreUser"

    def test_token_identifies_its_user(self):
        token = self.userdb.create_api_token(self.username)
        self.assertRegex(token, TOKEN_RE)
        self.assertEqual(self.userdb.username_for_api_token(token), self.username)

    def test_only_the_hash_is_stored(self):
        token = self.userdb.create_api_token(self.username)
        record = self.userdb.get_api_token(self.username)
        self.assertNotIn(token, str(record))
        self.assertEqual(
            set(record),
            {"_id", "token_hash", "username", "created_at"},
        )

    def test_new_token_replaces_the_old_one(self):
        old = self.userdb.create_api_token(self.username)
        new = self.userdb.create_api_token(self.username)
        self.assertIsNone(self.userdb.username_for_api_token(old))
        self.assertEqual(self.userdb.username_for_api_token(new), self.username)
        self.assertEqual(
            self.userdb.api_tokens.count_documents({"username": self.username}),
            1,
        )

    def test_revoked_token_is_refused(self):
        token = self.userdb.create_api_token(self.username)
        self.userdb.revoke_api_token(self.username)
        self.assertIsNone(self.userdb.username_for_api_token(token))

    def test_blocked_or_pending_user_is_refused(self):
        token = self.userdb.create_api_token(self.username)
        for field in ("blocked", "pending"):
            with self.subTest(field=field):
                user = self.userdb.get_user(self.username)
                user[field] = True
                self.userdb.save_user(user)
                self.assertIsNone(self.userdb.username_for_api_token(token))
                user[field] = False
                self.userdb.save_user(user)

    def test_block_reaches_every_instance_at_once(self):
        # Another instance holds a cached user record for up to 120 s.
        token = self.userdb.create_api_token(self.username)
        self.userdb.get_user(self.username)
        self.userdb.users.update_one(
            {"username": self.username},
            {"$set": {"blocked": True}},
        )
        try:
            self.assertIsNone(self.userdb.username_for_api_token(token))
        finally:
            self.userdb.users.update_one(
                {"username": self.username},
                {"$set": {"blocked": False}},
            )

    def test_removed_user_loses_the_token(self):
        username = f"{self.username}Removed"
        self.userdb.create_user(
            username,
            "test-token-password",
            "token-removed@example.com",
            self.tests_repo,
        )
        self.approve(username)
        token = self.userdb.create_api_token(username)
        self.userdb.remove_user(self.userdb.get_user(username), self.username)
        self.assertIsNone(self.userdb.get_api_token(username))
        self.assertIsNone(self.userdb.username_for_api_token(token))

    def test_block_outside_the_app_removes_the_token_at_its_next_use(self):
        token = self.userdb.create_api_token(self.username)
        self.set_blocked_in_database(self.username, blocked=True)
        try:
            self.assertIsNone(self.userdb.username_for_api_token(token))
            self.assertIsNone(self.userdb.get_api_token(self.username))
        finally:
            self.set_blocked_in_database(self.username, blocked=False)
        self.assertIsNone(self.userdb.username_for_api_token(token))

    def test_blocked_user_cannot_create_a_token(self):
        self.set_blocked_in_database(self.username, blocked=True)
        try:
            self.assertIsNone(self.userdb.create_api_token(self.username))
            self.assertIsNone(self.userdb.get_api_token(self.username))
        finally:
            self.set_blocked_in_database(self.username, blocked=False)

    def test_token_stops_working_when_it_expires(self):
        token = self.userdb.create_api_token(self.username)
        self.assertEqual(self.userdb.username_for_api_token(token), self.username)
        self.userdb.api_tokens.update_one(
            {"username": self.username},
            {
                "$set": {
                    "created_at": datetime.now(UTC)
                    - timedelta(days=API_TOKEN_MAX_AGE_DAYS + 1),
                },
            },
        )
        self.assertIsNone(self.userdb.username_for_api_token(token))

    def test_revoke_removes_every_row_of_a_user(self):
        # Without the unique index, concurrent creates can leave two.
        self.userdb.create_api_token(self.username)
        record = self.userdb.get_api_token(self.username)
        record.pop("_id")
        record["token_hash"] = "0" * 64
        self.userdb.api_tokens.insert_one(record)
        self.assertTrue(self.userdb.revoke_api_token(self.username))
        self.assertIsNone(self.userdb.get_api_token(self.username))
        self.assertFalse(self.userdb.revoke_api_token(self.username))

    def test_create_and_revoke_are_logged_without_the_token(self):
        with patch("builtins.print") as printed:
            token = self.userdb.create_api_token(self.username)
            self.userdb.revoke_api_token(self.username)
        lines = [str(call.args[0]) for call in printed.call_args_list]
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertIn(self.username, line)
            self.assertNotIn(token, line)

    def test_idle_user_cleanup_removes_the_token(self):
        # The script deletes users directly, and the name can be registered again.
        username = f"{self.username}Idle"
        self.userdb.create_user(
            username,
            "test-token-password",
            "token-idle@example.com",
            self.tests_repo,
        )
        self.userdb.users.update_one(
            {"username": username},
            {
                "$set": {
                    "registration_time": datetime.now(UTC) - timedelta(days=30),
                },
            },
        )
        self.approve(username)
        token = self.userdb.create_api_token(username)
        delta_update_users.cleanup_users(self.rundb)
        self.assertIsNone(self.userdb.users.find_one({"username": username}))
        self.assertIsNone(self.userdb.get_api_token(username))
        self.assertIsNone(self.userdb.username_for_api_token(token))

    def test_malformed_token_reads_nothing(self):
        with patch.object(self.userdb, "api_tokens") as api_tokens:
            for token in ("", "abc", "fst_short", f"xxx_{'a' * 43}", f"fst_{'a' * 44}"):
                with self.subTest(token=token):
                    self.assertIsNone(self.userdb.username_for_api_token(token))
            api_tokens.find_one.assert_not_called()


class TestApiTokenRequests(_TokenTestCase):
    username = "TestTokenRequestUser"

    def test_token_opens_the_login_only_json(self):
        headers = self.bearer(self.userdb.create_api_token(self.username))
        run_id = self._create_run()
        # 404: past the gate, and the run has no such task or the id no such run.
        for path in (f"/api/get_run/{OLD_RUN_ID}", f"/api/get_elo/{OLD_RUN_ID}"):
            with self.subTest(path=path):
                response = self.client.get(path, headers=headers)
                self.assertEqual(response.status_code, 404)
        response = self.client.get(f"/api/get_task/{run_id}/0", headers=headers)
        self.assertEqual(response.status_code, 404)
        response = self.client.post("/api/actions", json={}, headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_scheme_is_case_insensitive(self):
        token = self.userdb.create_api_token(self.username)
        response = self.client.post(
            "/api/actions",
            json={},
            headers={"Authorization": f"bearer {token}"},
        )
        self.assertEqual(response.status_code, 200)

    def test_anything_else_is_anonymous(self):
        token = self.userdb.create_api_token(self.username)
        unknown = f"fst_{'a' * 43}"
        for headers in (
            {},
            self.bearer(unknown),
            self.bearer("junk"),
            {"Authorization": f"Basic {token}"},
            {"Authorization": token},
        ):
            with self.subTest(headers=headers):
                response = self.client.get(
                    f"/api/get_run/{OLD_RUN_ID}",
                    headers=headers,
                )
                self.assertEqual(response.status_code, 401)

    def test_query_string_is_not_a_credential(self):
        token = self.userdb.create_api_token(self.username)
        for name in ("token", "access_token", "api_token"):
            with self.subTest(name=name):
                response = self.client.get(
                    f"/api/get_run/{OLD_RUN_ID}?{name}={token}",
                )
                self.assertEqual(response.status_code, 401)

    def test_bad_token_keeps_public_data(self):
        response = self.client.get(
            f"/api/get_run/{self._create_run()}",
            headers=self.bearer("junk"),
        )
        self.assertEqual(response.status_code, 200)

    def test_public_requests_look_up_no_token(self):
        token = self.userdb.create_api_token(self.username)
        run_id = self._create_run()
        with patch.object(
            self.userdb,
            "username_for_api_token",
            side_effect=AssertionError,
        ):
            response = self.client.get(
                f"/api/get_run/{run_id}",
                headers=self.bearer(token),
            )
            self.assertEqual(response.status_code, 200)
            response = self.client.post(
                "/api/actions",
                json={"run_id": run_id},
                headers=self.bearer(token),
            )
            self.assertEqual(response.status_code, 200)

    def test_token_does_not_open_pages(self):
        headers = self.bearer(self.userdb.create_api_token(self.username))
        response = self.client.get(
            "/tests/machines?q=abc",
            headers=headers,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["location"].startswith("/login?next="))

    def test_token_does_not_work_for_a_worker_call(self):
        # Worker calls authenticate with the password in the body.
        headers = self.bearer(self.userdb.create_api_token(self.username))
        body = {
            "worker_info": {"username": self.username},
            "password": "wrong",
        }
        response = self.client.post(
            "/api/request_version",
            json=body,
            headers=headers,
        )
        self.assertEqual(response.status_code, 401)
        body["password"] = self.password
        response = self.client.post("/api/request_version", json=body)
        self.assertEqual(response.status_code, 200)


class TestApiTokenProfilePage(_TokenTestCase):
    username = "TestTokenPageUser"

    def setUp(self):
        super().setUp()
        self._login_user()

    def client_without_session(self, path, headers):
        anonymous = test_support.make_test_client(
            rundb=self.rundb,
            include_api=True,
            include_views=True,
        )
        return anonymous.get(path, headers=headers).status_code

    def post_form(self, action, *, password=None, path="/user"):
        page = self.client.get(path)
        self.assertEqual(page.status_code, 200)
        return self.client.post(
            path,
            data={
                "csrf_token": test_support.extract_csrf_token(page.text),
                "api_token": action,
                "api_token_password": self.password if password is None else password,
            },
            follow_redirects=False,
        )

    def test_page_starts_without_a_token(self):
        response = self.client.get("/user")
        self.assertIn('id="api_token_form"', response.text)
        self.assertIn("No token.", response.text)
        self.assertIn("Create token", response.text)
        self.assertNotIn('value="revoke"', response.text)

    def test_new_token_is_shown_once_and_works(self):
        response = self.post_form("create")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        token = TOKEN_RE.search(response.text).group(0)
        self.assertEqual(self.userdb.username_for_api_token(token), self.username)

        response = self.client.get("/user")
        self.assertNotIn(token, response.text)
        self.assertNotIn('id="new-api-token"', response.text)
        self.assertIn("Created: ", response.text)
        self.assertIn("Expires: ", response.text)
        self.assertIn("Replace token", response.text)
        self.assertIn('value="revoke"', response.text)

    def test_new_token_stays_out_of_the_session_cookie(self):
        response = self.post_form("create")
        token = TOKEN_RE.search(response.text).group(0)
        payload = self.client.cookies[SESSION_COOKIE_NAME].split(".", 1)[0]
        session = base64.b64decode(payload).decode()
        self.assertIn(self.username, session)
        self.assertNotIn(token, session)

    def test_wrong_password_changes_nothing(self):
        token = self.userdb.create_api_token(self.username)
        for action in ("create", "revoke"):
            with self.subTest(action=action):
                response = self.post_form(action, password="wrong")
                self.assertEqual(response.status_code, 200)
                self.assertIn("Invalid password!", response.text)
                self.assertIsNone(TOKEN_RE.search(response.text))
                self.assertEqual(
                    self.userdb.username_for_api_token(token),
                    self.username,
                )

    def test_revoke_removes_the_token(self):
        token = self.userdb.create_api_token(self.username)
        response = self.post_form("revoke")
        self.assertEqual(response.status_code, 200)
        self.assertIn("API token revoked", response.text)
        self.assertIn("No token.", response.text)
        self.assertIsNone(self.userdb.username_for_api_token(token))

    def test_unknown_action_changes_nothing(self):
        response = self.post_form("rotate")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(self.userdb.get_api_token(self.username))

    def test_post_requires_csrf(self):
        response = self.client.post(
            "/user",
            data={"api_token": "create", "api_token_password": self.password},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.userdb.get_api_token(self.username))

    def test_expired_token_is_named_on_the_page(self):
        self.userdb.create_api_token(self.username)
        self.userdb.api_tokens.update_one(
            {"username": self.username},
            {
                "$set": {
                    "created_at": datetime.now(UTC)
                    - timedelta(days=API_TOKEN_MAX_AGE_DAYS + 1),
                },
            },
        )
        self.assertIn("Expired: ", self.client.get("/user").text)

    def test_anonymous_post_is_sent_to_login(self):
        self.client.cookies.clear()
        page = self.client.get("/login")
        response = self.client.post(
            "/user",
            data={
                "csrf_token": test_support.extract_csrf_token(page.text),
                "api_token": "create",
                "api_token_password": self.password,
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["location"].startswith("/login?next="))
        self.assertIsNone(self.userdb.get_api_token(self.username))

    def test_password_change_revokes_the_token(self):
        # Whoever knew the old password could have made the token.
        token = self.userdb.create_api_token(self.username)
        page = self.client.get("/user")
        new_password = "Corr3ct-H0rse-Battery-Staple-Zebra!"
        response = self.client.post(
            "/user",
            data={
                "csrf_token": test_support.extract_csrf_token(page.text),
                "user": self.username,
                "old_password": self.password,
                "password": new_password,
                "password2": new_password,
                "email": "",
                "tests_repo": self.tests_repo,
            },
            follow_redirects=False,
        )
        try:
            self.assertEqual(response.status_code, 302)
            self.assertIsNone(self.userdb.username_for_api_token(token))
        finally:
            user = self.userdb.get_user(self.username)
            user["password"] = self.password
            self.userdb.save_user(user)

    def post_block(self, path, username, blocked):
        page = self.client.get(path)
        return self.client.post(
            path,
            data={
                "csrf_token": test_support.extract_csrf_token(page.text),
                "user": username,
                "blocked": blocked,
            },
            follow_redirects=False,
        )

    def test_block_revokes_the_token_for_good(self):
        other = f"{self.username}Blocked"
        self.userdb.create_user(
            other,
            "test-token-password",
            "token-blocked@example.com",
            self.tests_repo,
        )
        self.approve(other)
        token = self.userdb.create_api_token(other)
        headers = self.bearer(token)
        path = f"/api/get_run/{OLD_RUN_ID}"
        self.assertEqual(self.client_without_session(path, headers), 404)
        original_pending, original_groups = self._set_approver_state()
        try:
            user_path = f"/user/{other}"
            self.post_block(user_path, other, "1")
            self.assertTrue(self.userdb.users.find_one({"username": other})["blocked"])
            self.assertIsNone(self.userdb.get_api_token(other))
            self.assertEqual(self.client_without_session(path, headers), 401)

            self.post_block(user_path, other, "0")
            self.assertFalse(self.userdb.users.find_one({"username": other})["blocked"])
            self.assertIsNone(self.userdb.get_api_token(other))
            self.assertEqual(self.client_without_session(path, headers), 401)
        finally:
            self._restore_approver_state(original_pending, original_groups)
            self.userdb.users.delete_many({"username": other})

    def test_unblock_removes_a_token_that_a_block_outside_the_app_left(self):
        other = f"{self.username}Shell"
        self.userdb.create_user(
            other,
            "test-token-password",
            "token-shell@example.com",
            self.tests_repo,
        )
        self.approve(other)
        token = self.userdb.create_api_token(other)
        self.set_blocked_in_database(other, blocked=True)
        original_pending, original_groups = self._set_approver_state()
        try:
            self.post_block(f"/user/{other}", other, "0")
            self.assertFalse(self.userdb.users.find_one({"username": other})["blocked"])
            self.assertIsNone(self.userdb.username_for_api_token(token))
        finally:
            self._restore_approver_state(original_pending, original_groups)
            self.userdb.users.delete_many({"username": other})

    def test_session_of_a_blocked_user_gets_no_token_from_the_form(self):
        # The middleware that ends the session caches its list for a moment, so a
        # request can still reach the form after the block.
        page = self.client.get("/user")
        self.set_blocked_in_database(self.username, blocked=True)
        try:
            self.client.post(
                "/user",
                data={
                    "csrf_token": test_support.extract_csrf_token(page.text),
                    "api_token": "create",
                    "api_token_password": self.password,
                },
                follow_redirects=False,
            )
            self.assertIsNone(self.userdb.get_api_token(self.username))
        finally:
            self.set_blocked_in_database(self.username, blocked=False)

    def test_approver_cannot_issue_a_token_for_another_user(self):
        other = f"{self.username}Other"
        self.userdb.create_user(
            other,
            "test-token-password",
            "token-other@example.com",
            self.tests_repo,
        )
        original_pending, original_groups = self._set_approver_state()
        try:
            path = f"/user/{other}"
            self.assertNotIn('id="api_token_form"', self.client.get(path).text)
            self.post_form("create", path=path)
            self.assertIsNone(self.userdb.get_api_token(other))
        finally:
            self._restore_approver_state(original_pending, original_groups)
            self.userdb.users.delete_many({"username": other})


if __name__ == "__main__":
    unittest.main()
