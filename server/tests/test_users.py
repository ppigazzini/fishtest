import unittest
from datetime import UTC, datetime

import util
from fastapi.testclient import TestClient
from util import extract_csrf_token, get_test_app


class Create10UsersTest(unittest.TestCase):
    def setUp(self):
        self.rundb = util.get_rundb()
        self.client = TestClient(get_test_app(self.rundb))

    def tearDown(self):
        self.rundb.userdb.users.delete_many({"username": "JoeUser"})
        self.rundb.userdb.user_cache.delete_many({"username": "JoeUser"})

    def test_create_user(self):
        html = self.client.get("/signup").text
        csrf = extract_csrf_token(html)
        response = self.client.post(
            "/signup",
            data={
                "csrf_token": csrf,
                "username": "JoeUser",
                "password": "CorrectHorseBatteryStaple-1",
                "password2": "CorrectHorseBatteryStaple-1",
                "email": "joe@user.net",
                "tests_repo": "https://github.com/official-stockfish/Stockfish",
                # captcha is only required when FISHTEST_CAPTCHA_SECRET is set
                "g-recaptcha-response": "",
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers.get("location"), "/login")


class Create50LoginTest(unittest.TestCase):
    def setUp(self):
        self.rundb = util.get_rundb()
        self.rundb.userdb.create_user(
            "JoeUser",
            "secret",
            "email@email.email",
            "https://github.com/official-stockfish/Stockfish",
        )
        self.client = TestClient(get_test_app(self.rundb))

    def tearDown(self):
        self.rundb.userdb.users.delete_many({"username": "JoeUser"})
        self.rundb.userdb.user_cache.delete_many({"username": "JoeUser"})

    def test_login(self):
        html = self.client.get("/login").text
        csrf = extract_csrf_token(html)

        response = self.client.post(
            "/login",
            data={"csrf_token": csrf, "username": "JoeUser", "password": "badsecret"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Login failed", response.text)

        # Correct password, but still pending
        html = self.client.get("/login").text
        csrf = extract_csrf_token(html)
        response = self.client.post(
            "/login",
            data={"csrf_token": csrf, "username": "JoeUser", "password": "secret"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("Account pending for user: JoeUser", response.text)

        # Unblock, then user can log in successfully
        user = self.rundb.userdb.get_user("JoeUser")
        user["pending"] = False
        self.rundb.userdb.save_user(user)

        html = self.client.get("/login").text
        csrf = extract_csrf_token(html)
        response = self.client.post(
            "/login",
            data={"csrf_token": csrf, "username": "JoeUser", "password": "secret"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 303)


class Create90APITest(unittest.TestCase):
    def setUp(self):
        self.rundb = util.get_rundb()
        self.run_id = self.rundb.new_run(
            "master",
            "master",
            100000,
            "100+0.01",
            "100+0.01",
            "book",
            10,
            1,
            "",
            "",
            username="travis",
            tests_repo="travis",
            start_time=datetime.now(UTC),
        )
        self.rundb.userdb.user_cache.insert_one(
            {"username": "JoeUser", "cpu_hours": 12345},
        )
        self.client = TestClient(get_test_app(self.rundb))

    def tearDown(self):
        self.rundb.userdb.users.delete_many({"username": "JoeUser"})
        self.rundb.userdb.user_cache.delete_many({"username": "JoeUser"})


if __name__ == "__main__":
    unittest.main()
