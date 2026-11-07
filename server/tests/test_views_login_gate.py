# ruff: noqa: ANN201, D100, D101, D102
import json
import re
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_support
from bson.objectid import ObjectId
from ui_user_test_case import UiUserTestCase

from fishtest import views as views_module
from fishtest.api import ACTIONS_BODY_MAX
from fishtest.http.settings import ANONYMOUS_RUN_MAX_AGE_DAYS
from fishtest.http.ui_cookies import MACHINES_QUERY_COOKIE_NAME
from fishtest.schemas import ACTION_QUERY_CLAUSE_MAX
from fishtest.util import is_old_run_id, parse_page_number
from fishtest.views_actions import _ACTION_FILTER_VALUES


def _run_id_created_days_ago(days):
    return str(ObjectId.from_datetime(datetime.now(UTC) - timedelta(days=days)))


OLD_RUN_ID = _run_id_created_days_ago(ANONYMOUS_RUN_MAX_AGE_DAYS + 1)
HX_FRAGMENT = {"HX-Request": "true", "HX-Current-URL": "http://testserver/tests?page=1"}


class TestIsOldRunId(unittest.TestCase):
    def test_age_comes_from_the_object_id(self):
        self.assertTrue(is_old_run_id(OLD_RUN_ID))
        self.assertFalse(
            is_old_run_id(_run_id_created_days_ago(ANONYMOUS_RUN_MAX_AGE_DAYS - 1))
        )

    def test_invalid_id_is_not_old(self):
        self.assertFalse(is_old_run_id("not-an-object-id"))


class TestParsePageNumber(unittest.TestCase):
    def test_page_numbers(self):
        self.assertEqual(parse_page_number("3"), 3)
        self.assertEqual(parse_page_number(" 3 "), 3)

    def test_what_int_refuses_is_not_a_page(self):
        for value in ("", "x", "-1", "1.5", "\u00b2", "1" * 5000, "1" * 8):
            with self.subTest(value=value[:8]):
                self.assertIsNone(parse_page_number(value))


class _GateTestCase(UiUserTestCase):
    def setUp(self):
        self.client = test_support.make_test_client(
            rundb=self.rundb,
            include_api=True,
            include_views=True,
        )
        self._ensure_user_can_login()

    def get(self, path, **kwargs):
        # A followed redirect ends on the login page with a 200, which would hide
        # a refusal from the tests that expect a page.
        return self.client.get(path, follow_redirects=False, **kwargs)

    def assert_served(self, path, **kwargs):
        response = self.get(path, **kwargs)
        self.assertEqual(response.status_code, 200, path)
        return response

    def assert_sent_to_login(self, path):
        response = self.client.get(path, follow_redirects=False)
        self.assertEqual(response.status_code, 302, path)
        location = urlsplit(response.headers["location"])
        self.assertEqual(location.path, "/login", path)
        self.assertEqual(parse_qs(location.query)["next"], [path], path)
        self.assertEqual(response.headers["cache-control"], "no-store", path)


class TestAnonymousPages(_GateTestCase):
    username = "TestGatePagesUser"

    def test_login_only_routes_send_a_page_load_to_login(self):
        run_id = self._create_run()
        for path in (
            f"/tests/tasks/{run_id}",
            f"/tests/stats/{run_id}",
        ):
            with self.subTest(path=path):
                self.assert_sent_to_login(path)

    def test_fragment_request_navigates_back_from_the_page_on_screen(self):
        # fetch() follows a 302, which would swap the login page into the target.
        run_id = self._create_run()
        response = self.client.get(
            f"/tests/tasks/{run_id}",
            headers=HX_FRAGMENT,
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers["hx-redirect"],
            "/login?next=%2Ftests%3Fpage%3D1",
        )
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_old_run_fragment_navigates_too(self):
        for path in (
            f"/tests/view/{OLD_RUN_ID}/detail",
            f"/tests/live_elo_update/{OLD_RUN_ID}",
        ):
            with self.subTest(path=path):
                response = self.get(path, headers=HX_FRAGMENT)
                self.assertEqual(response.status_code, 204)
                self.assertIn("hx-redirect", response.headers)

    def test_history_restore_navigates_instead_of_swapping_login_in(self):
        # A 302 would be followed by fetch() and swapped in at the gated URL, where
        # the login form posts to a route that takes no POST.
        for headers in (
            {"HX-History-Restore-Request": "true"},
            {"HX-Request": "true", "HX-History-Restore-Request": "true"},
            {"HX-Request": "true", "HX-Request-Type": "full"},
        ):
            with self.subTest(headers=headers):
                response = self.get("/contributors?page=2", headers=headers)
                self.assertEqual(response.status_code, 204)
                self.assertEqual(
                    response.headers["hx-redirect"],
                    "/login?next=%2Fcontributors%3Fpage%3D2",
                )

    def test_current_url_header_cannot_break_or_steer_the_redirect(self):
        for current_url in (
            "http://[",
            "https://x//evil.example/a",
            "https://x/\\evil.example",
            "",
        ):
            with self.subTest(current_url=current_url):
                response = self.get(
                    "/tests/machines?q=abc",
                    headers={"HX-Request": "true", "HX-Current-URL": current_url},
                )
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.headers["hx-redirect"], "/login?next=%2F")

    def test_head_meets_the_gate(self):
        response = self.client.head(
            f"/tests/view/{OLD_RUN_ID}",
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)

    def test_repeated_parameter_meets_the_gate(self):
        self.assert_sent_to_login("/tests/finished?page=1&page=2")

    def test_old_run_is_refused_without_reading_it(self):
        with patch.object(self.rundb, "get_run", side_effect=AssertionError):
            for path in (
                f"/tests/view/{OLD_RUN_ID}",
                f"/tests/view/{OLD_RUN_ID}/detail",
                f"/tests/live_elo/{OLD_RUN_ID}",
                f"/tests/live_elo_update/{OLD_RUN_ID}",
            ):
                with self.subTest(path=path):
                    self.assert_sent_to_login(path)

    def test_recent_run_page_stays_public_without_the_tasks_panel(self):
        run_id = self._create_run()
        response = self.assert_served(f"/tests/view/{run_id}")
        self.assertIn('id="tasks-totals"', response.text)
        self.assertNotIn('id="tasks-button"', response.text)
        self.assertNotIn('id="tasks-content"', response.text)
        self.assertIn(f"/login?next=%2Ftests%2Fview%2F{run_id}", response.text)
        self.assertNotIn("document size", response.text)

    def test_recent_run_page_builds_no_task_rows(self):
        run_id = self._create_run()
        with patch("fishtest.views._task_table_state", side_effect=AssertionError):
            response = self.client.get(f"/tests/view/{run_id}")
        self.assertEqual(response.status_code, 200)

    def test_task_link_is_login_only(self):
        self.assert_sent_to_login(f"/tests/view/{self._create_run()}?show_task=0")

    def test_lists_serve_the_first_page_and_send_the_rest_to_login(self):
        for path in (
            "/tests",
            "/tests/finished",
            f"/tests/user/{self.username}",
            "/actions",
            "/nns",
            "/contributors",
            "/contributors/monthly",
        ):
            with self.subTest(path=path):
                self.assert_served(path)
                self.assert_served(f"{path}?page=1")
                self.assert_sent_to_login(f"{path}?page=2")
                self.assert_sent_to_login(f"{path}?unknown=1")

    def test_homepage_shows_the_workers_panel_as_saved(self):
        opened = 'id="machines-panel" class="collapse show"'
        response = self.assert_served("/tests")
        self.assertIn('id="machines-button"', response.text)
        self.assertIn('id="machines-panel"', response.text)
        self.assertNotIn(opened, response.text)
        self.client.cookies.set("machines_state", "Hide")
        self.assertIn(opened, self.assert_served("/tests").text)

    def test_homepage_applies_no_saved_search(self):
        # Applied, it would send the panel's first request to the login page.
        self.client.cookies.set(MACHINES_QUERY_COOKIE_NAME, "saved-search")
        self.client.cookies.set("machines_my_workers", "1")
        self.client.cookies.set("machines_page", "3")
        with patch(
            "fishtest.views._filtered_machine_count",
            side_effect=AssertionError,
        ):
            response = self.assert_served("/tests")
        self.assertNotIn("saved-search", response.text)
        self.assertNotIn('id="machines_my_workers"', response.text)
        self.assertIn('id="machines_page" name="page" value="1"', response.text)

    def test_workers_table_is_public_and_only_its_search_needs_a_login(self):
        for path in (
            "/tests/machines",
            "/tests/machines?q=",
            "/tests/machines?q=%20",
            "/tests/machines?sort=machine&order=asc&page=1&q=",
            "/tests/machines?my_workers=1",
        ):
            with self.subTest(path=path):
                self.assert_served(path, headers=HX_FRAGMENT)
        self.assert_sent_to_login("/tests/machines?q=abc")
        self.assert_sent_to_login("/tests/machines?page=2")
        response = self.get("/tests/machines?q=abc", headers=HX_FRAGMENT)
        self.assertEqual(response.status_code, 204)
        self.assertEqual(
            response.headers["hx-redirect"],
            "/login?next=%2Ftests%3Fpage%3D1",
        )

    def test_workers_table_leaves_the_saved_search_alone(self):
        self.client.cookies.set(MACHINES_QUERY_COOKIE_NAME, "saved-search")
        self.client.cookies.set("machines_my_workers", "1")
        self.client.cookies.set("machines_page", "3")
        response = self.assert_served(
            "/tests/machines?sort=machine&order=asc",
            headers=HX_FRAGMENT,
        )
        written = " ".join(response.headers.get_list("set-cookie"))
        self.assertIn("machines_sort=machine", written)
        self.assertNotIn(f"{MACHINES_QUERY_COOKIE_NAME}=", written)
        self.assertNotIn("machines_my_workers=", written)
        self.assertNotIn("machines_page=", written)
        self.assertEqual(
            self.client.cookies.get(MACHINES_QUERY_COOKIE_NAME),
            "saved-search",
        )

    def test_refused_search_writes_no_preference(self):
        self.client.cookies.set(MACHINES_QUERY_COOKIE_NAME, "saved-search")
        response = self.get("/tests/machines?q=other", headers=HX_FRAGMENT)
        written = " ".join(response.headers.get_list("set-cookie"))
        self.assertNotIn(f"{MACHINES_QUERY_COOKIE_NAME}=", written)

    def test_pages_render_no_login_only_poller(self):
        for path in ("/tests", f"/tests/user/{self.username}"):
            with self.subTest(path=path):
                response = self.assert_served(path)
                self.assertIn('id="pending-users-nav"', response.text)
                self.assertNotIn("/user_management/pending_count", response.text)
                self.assertNotIn('id="run-tables-poller"', response.text)

    def test_poll_from_a_stale_tab_is_answered_without_moving_it(self):
        for path in (
            "/tests?live=run_tables",
            f"/tests/user/{self.username}?live=run_tables",
            "/user_management/pending_count",
        ):
            with self.subTest(path=path):
                response = self.get(path, headers=HX_FRAGMENT)
                self.assertEqual(response.status_code, 204)
                self.assertNotIn("hx-redirect", response.headers)

    def test_anonymous_page_reads_no_pending_users(self):
        with patch.object(
            self.rundb.userdb,
            "get_pending",
            side_effect=AssertionError,
        ):
            self.assert_served("/tests")


class TestAnonymousControls(_GateTestCase):
    """Every table follows one rule: a click works, typing or paging needs a login."""

    username = "TestGateControlsUser"

    def clicks(self):
        form = "network_name=&user=&master_only=0&view=paged&sort=time&order=desc"
        return (
            "/nns?sort=name&order=asc&view=paged",
            "/nns?sort=downloads&order=desc",
            "/nns?master_only=1",
            f"/nns?{form}&master_only=1",
            "/contributors?sort=games&order=desc&view=paged",
            "/contributors?search=&view=paged&sort=cpu_hours&order=desc",
            "/contributors/monthly?sort=tests&order=asc&view=paged",
            "/actions?sort=event&order=asc",
            "/actions?action=new_run",
            "/actions?action=&user=&text=&max_count=5000&sort=time&order=desc",
            "/tests/finished?success_only=1",
            "/tests/finished?yellow_only=1&sort=time&order=desc",
            "/tests/finished?ltc_only=1",
            "/tests/finished?mode=search",
            "/tests/finished?mode=search&max_count=1000&sort=time&order=desc&text=&user=",
            f"/tests/user/{self.username}?success_only=1&sort=time&order=desc",
            "/tests/machines?sort=machine&order=asc&page=1&q=",
        )

    def login_needed(self):
        return (
            # What a visitor types.
            "/nns?network_name=abc",
            "/nns?user=abc",
            "/contributors?search=abc",
            "/actions?user=abc",
            "/actions?text=abc",
            "/tests/finished?text=abc",
            "/tests/finished?user=abc",
            "/tests/machines?q=abc",
            # Later pages.
            "/tests?page=2",
            "/tests/finished?page=2",
            "/tests/finished?success_only=1&page=2",
            f"/tests/user/{self.username}?page=2",
            "/nns?page=2",
            "/nns?sort=name&page=2",
            "/contributors?page=2",
            "/contributors/monthly?page=2",
            "/actions?page=2",
            "/actions?action=new_run&page=2",
            "/tests/machines?page=2",
            "/tests/machines?sort=machine&page=2",
            # All rows.
            "/nns?view=all",
            "/contributors?view=all",
            "/contributors/monthly?view=all",
            # Values with no fixed choice, which would step around the shared reads.
            "/actions?action=unknown",
            "/actions?max_count=7",
            "/actions?run_id=abc",
            "/tests/finished?success_only=abc",
            "/tests/finished?max_count=7",
            "/nns?unknown=1",
        )

    def test_a_click_works_without_a_login(self):
        for path in self.clicks():
            with self.subTest(path=path):
                self.assert_served(path)
                self.assert_served(path, headers=HX_FRAGMENT)

    def test_typing_later_pages_and_all_rows_need_a_login(self):
        for path in self.login_needed():
            with self.subTest(path=path):
                self.assert_sent_to_login(path)
                response = self.get(path, headers=HX_FRAGMENT)
                self.assertEqual(response.status_code, 204)
                self.assertIn("hx-redirect", response.headers)

    def test_a_logged_in_user_is_never_sent_to_login(self):
        self._login_user()
        # A logged-in text search on /actions needs a text index the test database
        # does not build.
        paths = [p for p in self.login_needed() if p != "/actions?text=abc"]
        for path in (*self.clicks(), *paths):
            with self.subTest(path=path):
                response = self.get(path)
                self.assertNotIn("/login", response.headers.get("location", ""))

    def test_the_event_list_offers_only_choices_the_gate_accepts(self):
        template = Path(views_module.__file__).parent / "templates/actions.html.j2"
        offered = set(re.findall(r'<option value="([^"]*)"', template.read_text()))
        self.assertEqual(offered, set(_ACTION_FILTER_VALUES))


class TestAuthenticatedPages(_GateTestCase):
    username = "TestGateAuthUser"

    def setUp(self):
        super().setUp()
        self._login_user()

    def test_every_gated_page_is_served(self):
        run_id = self._create_run()
        for path in (
            f"/tests/stats/{run_id}",
            f"/tests/view/{run_id}?show_task=0",
            "/tests/finished?page=2",
            f"/tests/user/{self.username}?page=2",
            "/tests?page=2",
            "/actions?page=2",
            "/nns?page=2",
            "/contributors?page=2",
            "/contributors/monthly?page=2",
        ):
            with self.subTest(path=path):
                self.assert_served(path)

    def test_workers_panel_follows_the_saved_state(self):
        opened = 'id="machines-panel" class="collapse show"'
        self.client.cookies.set("machines_state", "Hide")
        self.assertIn(opened, self.client.get("/tests").text)
        self.client.cookies.set("machines_state", "Show")
        self.assertNotIn(opened, self.client.get("/tests").text)

    def test_page_keeps_the_login_only_panels_and_pollers(self):
        response = self.client.get("/tests")
        self.assertIn('id="machines-panel"', response.text)
        self.assertIn('id="run-tables-poller"', response.text)
        self.assertIn('hx-get="/user_management/pending_count"', response.text)
        response = self.client.get(f"/tests/view/{self._create_run()}")
        self.assertIn('id="tasks-button"', response.text)

    def test_workers_fragment_is_only_cached_by_the_browser(self):
        response = self.assert_served("/tests/machines")
        self.assertEqual(response.headers["cache-control"], "private, max-age=10")

    def test_page_past_any_int_does_not_fail(self):
        for page in ("\u00b2", "1" * 5000, "9" * 7):
            with self.subTest(page=page[:8]):
                for path in ("/tests/finished", "/actions", "/nns", "/tests"):
                    response = self.get(f"{path}?page={page}")
                    self.assertLess(response.status_code, 500, path)


class TestLoginRedirect(_GateTestCase):
    username = "TestGateRedirectUser"

    def login_with(self, params):
        page = self.client.get("/login")
        response = self.client.post(
            "/login",
            params=params,
            data={
                "username": self.username,
                "password": self.password,
                "csrf_token": test_support.extract_csrf_token(page.text),
            },
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 302)
        return response.headers["location"]

    def test_local_path_is_kept(self):
        target = "/tests/view/653db116cc309ae839563103?show_task=3"
        self.assertEqual(self.login_with({"next": target}), target)

    def test_any_other_host_goes_home(self):
        for param in ("next", "came_from"):
            for value in (
                "https://evil.example/x",
                "//evil.example/x",
                "/\\evil.example",
                "/\t/evil.example",
                "\\\\evil.example",
                "javascript:alert(1)",
                "http://[",
            ):
                with self.subTest(param=param, value=value):
                    self.client.cookies.clear()
                    self.assertEqual(self.login_with({param: value}), "/")


class TestRunCacheSpelling(_GateTestCase):
    username = "TestGateCaseUser"

    def test_every_spelling_of_a_run_id_is_one_cache_entry(self):
        run_id = self._create_run()
        run = self.rundb.get_run(run_id)
        cache = self.rundb.run_cache.run_cache
        before = len(cache)
        self.assertIs(self.rundb.get_run(run_id.upper()), run)
        self.assertEqual(len(cache), before)


class TestAnonymousApi(_GateTestCase):
    username = "TestGateApiUser"

    def tearDown(self):
        self.rundb.db["actions"].delete_many({"username": self.username})
        self.rundb.active_runs_feed.cache_clear()

    def add_action(self, run_id):
        self.rundb.db["actions"].insert_one(
            {
                "action": "new_run",
                "username": self.username,
                "run_id": run_id,
                "time": datetime.now(UTC).timestamp(),
            },
        )

    def test_refusal_says_how_to_authenticate(self):
        response = self.client.get(f"/api/get_run/{OLD_RUN_ID}")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["www-authenticate"], "Bearer")
        self.assertEqual(response.headers["cache-control"], "no-store")
        # A page on another origin can read the refusal, as it can read the run.
        self.assertEqual(response.headers["access-control-allow-origin"], "*")

    def test_refused_token_is_named_in_the_challenge(self):
        response = self.client.get(
            f"/api/get_run/{OLD_RUN_ID}",
            headers={"Authorization": "Bearer junk"},
        )
        self.assertEqual(
            response.headers["www-authenticate"],
            'Bearer error="invalid_token"',
        )

    def test_active_feed_is_served_from_one_read(self):
        self.client.get("/api/active_runs")
        with patch.object(self.rundb, "runs") as runs:
            self.assertEqual(self.client.get("/api/active_runs").status_code, 200)
        runs.find.assert_not_called()

    def test_history_feed_needs_a_login(self):
        now = datetime.now(UTC).timestamp()
        day = 24 * 3600
        recent = now - 7 * day
        old = now - (ANONYMOUS_RUN_MAX_AGE_DAYS + 1) * day
        for path, status in (
            ("/api/finished_runs?page=1", 200),
            (f"/api/finished_runs?page=1&username={self.username}", 200),
            (f"/api/finished_runs?page=2&timestamp={recent}", 200),
            (f"/api/finished_runs?page=2&timestamp={recent}&ltc_only=1", 200),
            ("/api/finished_runs?page=2", 401),
            (f"/api/finished_runs?page=2&timestamp={old}", 401),
            ("/api/finished_runs?page=2&timestamp=0000000001", 401),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, status)

    def test_page_that_cannot_be_a_page_is_refused(self):
        for page in ("\u00b2", "1" * 5000, "9" * 30, "0", "-1"):
            with self.subTest(page=page[:8]):
                response = self.client.get(f"/api/finished_runs?page={page}")
                self.assertEqual(response.status_code, 400)

    def test_actions_of_an_old_run_are_left_out(self):
        # Left out and not refused: notifications.js stops at an error.
        recent = self._create_run()
        self.add_action(recent)
        self.add_action(OLD_RUN_ID)
        response = self.client.post(
            "/api/actions",
            json={"run_id": {"$in": [recent, OLD_RUN_ID]}},
        )
        self.assertEqual(response.status_code, 200)
        run_ids = {action["run_id"] for action in response.json()}
        self.assertIn(recent, run_ids)
        self.assertNotIn(OLD_RUN_ID, run_ids)
        response = self.client.post("/api/actions", json={"run_id": OLD_RUN_ID})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_long_or_broken_body_is_refused_before_it_costs_anything(self):
        run_id = self._create_run()
        padded = json.dumps({"run_id": run_id, "pad": "x" * ACTIONS_BODY_MAX})
        response = self.client.post(
            "/api/actions",
            content=padded,
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 413)
        self.assertIn("error", response.json())
        for body in ("[" * (ACTIONS_BODY_MAX - 1), b"\xff\xfe\xfd", "not json"):
            with self.subTest(body=body[:12]):
                response = self.client.post(
                    "/api/actions",
                    content=body,
                    headers={"content-type": "application/json"},
                )
                self.assertEqual(response.status_code, 401)

    def test_old_run_json_is_refused_without_reading_it(self):
        with patch.object(self.rundb, "get_run", side_effect=AssertionError):
            for path in (f"/api/get_run/{OLD_RUN_ID}", f"/api/get_elo/{OLD_RUN_ID}"):
                with self.subTest(path=path):
                    response = self.client.get(path)
                    self.assertEqual(response.status_code, 401)
                    self.assertIn("error", response.json())

    def test_refusal_writes_nothing_to_the_log(self):
        with patch("builtins.print") as printed:
            self.client.get(f"/api/get_run/{OLD_RUN_ID}")
        printed.assert_not_called()

    def test_recent_run_json_stays_public(self):
        run_id = self._create_run()
        self.assertEqual(self.client.get(f"/api/get_run/{run_id}").status_code, 200)
        self.assertEqual(self.client.get(f"/api/get_elo/{run_id}").status_code, 200)

    def test_task_json_is_login_only(self):
        run_id = self._create_run()
        self.assertEqual(self.client.get(f"/api/get_task/{run_id}/0").status_code, 401)

    def test_notification_filters_stay_public(self):
        # The three requests static/js/notifications.js sends.
        run_id = self._create_run()
        finished = ["finished_run", "stop_run", "delete_run"]
        for query in (
            {
                "action": {"$in": finished},
                "run_id": run_id,
                "time": {"$gte": 1700000000.5},
            },
            {"action": {"$in": finished}, "run_id": {"$in": [run_id]}},
            {"action": "new_run", "run_id": run_id},
        ):
            with self.subTest(query=query):
                response = self.client.post("/api/actions", json=query)
                self.assertEqual(response.status_code, 200)
                self.assertIsInstance(response.json(), list)

    def test_any_other_action_filter_is_login_only(self):
        run_id = self._create_run()
        for query in (
            {},
            {"username": self.username},
            {"action": "new_run"},
            {"run_id": {"$ne": run_id}},
            {"run_id": {"$regex": ".*"}},
            {"run_id": {"$in": [run_id] * 21}},
            {"run_id": run_id, "action": "failed_task"},
            {"run_id": run_id, "action": {"$in": ["failed_task"]}},
            {"run_id": run_id, "time": {"$gte": 10**30}},
            {"run_id": "not-an-object-id"},
            {"run_id": run_id, "$where": "sleep(1000)"},
            {"run_id": run_id, "message": {"$regex": ".*"}},
            {"run_id": run_id, "time": {"$lt": 1700000000}},
            [],
        ):
            with self.subTest(query=query):
                response = self.client.post("/api/actions", json=query)
                self.assertEqual(response.status_code, 401)

    def test_body_that_is_not_json_is_login_only(self):
        response = self.client.post(
            "/api/actions",
            content="not json",
            headers={"content-type": "application/json"},
        )
        self.assertEqual(response.status_code, 401)

    def test_feeds_and_game_archives_stay_public(self):
        for path, status in (
            ("/api/active_runs", 200),
            ("/api/finished_runs?page=1", 200),
            ("/api/calc_elo?W=10&D=10&L=10", 200),
            (f"/api/pgn/{OLD_RUN_ID}-0.pgn", 404),
            (f"/api/run_pgns/{OLD_RUN_ID}.pgn.gz", 404),
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, status)


class TestAuthenticatedApi(_GateTestCase):
    username = "TestGateApiAuthUser"

    def tearDown(self):
        self.rundb.db["actions"].delete_many({"username": self.username})
        user = self.rundb.userdb.get_user(self.username)
        user["blocked"] = False
        self.rundb.userdb.save_user(user)

    def test_filter_runs_no_operator_the_schema_does_not_name(self):
        # None of these may reach MongoDB: one $where can take the server down.
        for query in (
            {"$where": "sleep(1000) || true"},
            {"$or": [{"$where": "true"}]},
            {"$expr": {"$eq": [1, 1]}},
            {"$bad": 1},
            {"message": {"$regex": "x"}},
            {"unknown_field": "x"},
            {"$or": []},
            [],
            {"time": 10**30},
            {"$or": [{"action": "x"}] * (ACTION_QUERY_CLAUSE_MAX + 1)},
        ):
            with self.subTest(query=str(query)[:40]):
                with patch.object(self.rundb, "db") as db:
                    response = self.client.post("/api/actions", json=query)
                self.assertEqual(response.status_code, 400)
                db.__getitem__.assert_not_called()

    def test_refused_filter_gets_a_fixed_answer(self):
        for body, message in (
            ("not json", "not json encoded"),
            ('{"action": {"$bad": 1}}', "not a filter"),
            ('{"action": "\\ud800"}', "not a filter"),
        ):
            with self.subTest(body=body):
                response = self.client.post(
                    "/api/actions",
                    content=body,
                    headers={"content-type": "application/json"},
                )
                self.assertEqual(response.status_code, 400)
                self.assertIn(message, response.json()["error"])

    def test_filter_returns_newest_first(self):
        run_id = self._create_run()
        for when in (1.0, 3.0, 2.0):
            self.rundb.db["actions"].insert_one(
                {
                    "action": "new_run",
                    "username": self.username,
                    "run_id": run_id,
                    "time": when,
                },
            )
        response = self.client.post(
            "/api/actions",
            json={"run_id": run_id, "time": {"$lt": 10}},
        )
        self.assertEqual([a["time"] for a in response.json()], [3.0, 2.0, 1.0])

    def test_answer_is_not_kept_by_a_cache(self):
        for response in (
            self.client.get(f"/api/get_task/{self._create_run()}/0"),
            self.client.post("/api/actions", json={}),
            self.client.get("/api/finished_runs?page=2"),
        ):
            self.assertEqual(response.headers.get("cache-control"), "no-store")

    def test_blocked_session_counts_as_no_login(self):
        # The middleware that ends a blocked session skips /api.
        user = self.rundb.userdb.get_user(self.username)
        user["blocked"] = True
        self.rundb.userdb.save_user(user)
        response = self.client.get(f"/api/get_run/{OLD_RUN_ID}")
        self.assertEqual(response.status_code, 401)
        response = self.client.post("/api/actions", json={})
        self.assertEqual(response.status_code, 401)

    def test_session_of_a_removed_user_counts_as_no_login(self):
        test_support.set_session(self.client, {"user": "NoSuchGateUser"})
        response = self.client.get(f"/api/get_run/{OLD_RUN_ID}")
        self.assertEqual(response.status_code, 401)

    def setUp(self):
        super().setUp()
        self._login_user()

    def test_gated_json_is_served(self):
        run_id = self._create_run()
        self.assertEqual(self.client.get(f"/api/get_run/{run_id}").status_code, 200)
        self.assertEqual(self.client.post("/api/actions", json={}).status_code, 200)

    def test_old_run_passes_the_gate(self):
        response = self.client.get(f"/api/get_run/{OLD_RUN_ID}")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
