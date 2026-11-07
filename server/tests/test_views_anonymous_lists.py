# ruff: noqa: ANN201, D100, D101, D102
import copy
import re
import threading
import time
from datetime import UTC, datetime
from unittest.mock import patch

import test_support
from ui_user_test_case import UiUserTestCase

from fishtest.http.settings import ANONYMOUS_LIST_TTL_S, CONTRIBUTORS_PAGE_SIZE
from fishtest.http.ui_cookies import (
    ACTIVE_RUN_FILTERS_COOKIE_NAME,
    MASTER_ONLY_COOKIE_NAME,
    THEME_COOKIE_NAME,
)
from fishtest.lru_cache import LRUCache
from fishtest.views import (
    _anonymous_contributors_list,
    _anonymous_contributors_rows,
    _anonymous_nns_list,
    _anonymous_nns_rows,
    _anonymous_unfinished_runs,
)
from fishtest.views_actions import _anonymous_actions, _anonymous_actions_page
from fishtest.views_finished import _anonymous_finished_runs
from fishtest.views_machines import _anonymous_machine_rows

NET_PREFIX = "nn-anonymous-list-"
USER_PREFIX = "AnonymousListUser"
SNAPSHOTS = (
    _anonymous_actions,
    _anonymous_actions_page,
    _anonymous_contributors_list,
    _anonymous_contributors_rows,
    _anonymous_finished_runs,
    _anonymous_machine_rows,
    _anonymous_nns_list,
    _anonymous_nns_rows,
    _anonymous_unfinished_runs,
)
LIST_PAGES = (
    "/tests",
    "/tests/finished",
    "/tests/machines",
    "/nns",
    "/contributors",
    "/contributors/monthly",
    "/actions",
    "/nns?sort=name&order=asc",
    "/contributors?sort=games",
    "/actions?sort=event",
    "/actions?action=new_run",
    "/tests/finished?success_only=1",
)
HX_FRAGMENT = {"HX-Request": "true"}


class _AnonymousListTestCase(UiUserTestCase):
    def setUp(self):
        super().setUp()
        self.logged_in = self.new_client()
        test_support.authenticate_client(self.logged_in, username=self.username)

    def tearDown(self):
        self.rundb.nndb.delete_many({"name": {"$regex": f"^{NET_PREFIX}"}})
        self.rundb.userdb.user_cache.delete_many(
            {"username": {"$regex": f"^{USER_PREFIX}"}},
        )

    def new_client(self):
        return test_support.make_test_client(
            rundb=self.rundb,
            include_api=False,
            include_views=True,
        )

    def add_net(self, suffix, *, is_master=False):
        name = f"{NET_PREFIX}{suffix}"
        self.rundb.nndb.insert_one(
            {"name": name, "user": "NetOwner", "downloads": 0, "is_master": is_master},
        )
        return name

    def add_contributors(self, count):
        self.rundb.userdb.user_cache.insert_many(
            {
                "username": f"{USER_PREFIX}{index:03}",
                "cpu_hours": 1000 - index,
                "games": 10,
                "tests": 1,
                "games_per_hour": 0,
                "last_updated": datetime(2026, 1, 1, tzinfo=UTC),
                "tests_repo": self.tests_repo,
            }
            for index in range(count)
        )

    def read_targets(self):
        userdb = self.rundb.userdb
        return (
            ("/tests", self.rundb, "aggregate_unfinished_runs"),
            ("/tests", self.rundb, "get_finished_runs"),
            ("/tests/finished", self.rundb, "get_finished_runs"),
            ("/tests/machines", self.rundb, "get_machines"),
            ("/nns", self.rundb, "get_nns"),
            ("/contributors", userdb.user_cache, "find"),
            ("/contributors/monthly", userdb.top_month, "find"),
            ("/actions", self.rundb.actiondb, "get_actions"),
        )

    def count_reads(self, client, paths, target, method):
        test_support.clear_anonymous_lists()
        with patch.object(target, method, wraps=getattr(target, method)) as read:
            for path in paths:
                response = client.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 200, path)
        return read.call_count


class TestSharedRead(_AnonymousListTestCase):
    username = "TestAnonymousReadUser"

    def test_anonymous_requests_share_one_read(self):
        for path, target, method in self.read_targets():
            with self.subTest(path=path, method=method):
                paths = (path, path, f"{path}?page=1", f"{path}?page=")
                self.assertEqual(
                    self.count_reads(self.client, paths, target, method), 1
                )

    def test_logged_in_requests_read_every_time(self):
        for path, target, method in self.read_targets():
            with self.subTest(path=path, method=method):
                reads = self.count_reads(
                    self.logged_in,
                    (path, path, path),
                    target,
                    method,
                )
                self.assertEqual(reads, 3)

    def test_every_sort_shares_one_read(self):
        userdb = self.rundb.userdb
        for paths, target, method in (
            (
                (
                    "/nns",
                    "/nns?sort=name&order=asc",
                    "/nns?sort=downloads",
                    "/nns?sort=x",
                ),
                self.rundb,
                "get_nns",
            ),
            (
                ("/contributors", "/contributors?sort=games", "/contributors?sort=x"),
                userdb.user_cache,
                "find",
            ),
            (
                ("/actions?sort=event", "/actions?sort=source&order=desc"),
                self.rundb.actiondb,
                "get_actions",
            ),
        ):
            with self.subTest(paths=paths):
                self.assertEqual(
                    self.count_reads(self.client, paths, target, method), 1
                )

    def test_each_tab_has_one_read(self):
        paths = (
            "/tests/finished?success_only=1",
            "/tests/finished?success_only=1&sort=time&order=desc",
            "/tests/finished?ltc_only=1",
            "/tests/finished?ltc_only=1",
        )
        reads = self.count_reads(self.client, paths, self.rundb, "get_finished_runs")
        self.assertEqual(reads, 2)

    def test_a_later_sort_does_not_change_the_summary(self):
        self.add_net("summary-a", is_master=True)
        self.add_net("summary-b")
        first = _anonymous_nns_list(self.rundb, master_only=False)
        other = _anonymous_nns_list(
            self.rundb,
            master_only=False,
            sort_param="name",
            order_param="asc",
        )
        self.assertEqual(first["nns_summary"], other["nns_summary"])
        self.assertEqual(first["num_nns"], other["num_nns"])

    def test_ended_session_counts_as_anonymous(self):
        test_support.set_session(self.client, {"user": self.username})
        reads = self.count_reads(
            self.client,
            ("/nns", "/nns"),
            self.rundb,
            "get_nns",
        )
        self.assertEqual(reads, 1)

    def test_user_runs_page_reads_every_time(self):
        path = f"/tests/user/{self.username}"
        reads = self.count_reads(
            self.client,
            (path, path),
            self.rundb,
            "get_finished_runs",
        )
        self.assertEqual(reads, 2)

    def test_concurrent_requests_wait_for_one_read(self):
        entered = threading.Event()
        release = threading.Event()
        self.addCleanup(release.set)
        results = []

        def slow_read(**_):
            entered.set()
            release.wait(5)
            return iter(()), 0

        def request():
            results.append(_anonymous_nns_list(self.rundb, master_only=False))

        test_support.clear_anonymous_lists()
        threads = [threading.Thread(target=request) for _ in range(8)]
        with patch.object(self.rundb, "get_nns", side_effect=slow_read) as read:
            threads[0].start()
            self.assertTrue(entered.wait(5))
            for thread in threads[1:]:
                thread.start()
            time.sleep(0.05)
            release.set()
            for thread in threads:
                thread.join(5)
        self.assertEqual(read.call_count, 1)
        self.assertEqual(len(results), 8)


class TestSnapshotAge(_AnonymousListTestCase):
    username = "TestAnonymousAgeUser"

    def test_snapshots_expire_from_their_read_and_not_their_use(self):
        for snapshot in SNAPSHOTS:
            with self.subTest(snapshot=snapshot.__name__):
                self.assertEqual(snapshot.cache.expiration, ANONYMOUS_LIST_TTL_S)
                self.assertFalse(snapshot.cache.refresh)

    def test_anonymous_page_shows_a_write_once_the_snapshot_expires(self):
        caches = (_anonymous_nns_list.cache, _anonymous_nns_rows.cache)
        for cache in caches:
            self.addCleanup(setattr, cache, "expiration", ANONYMOUS_LIST_TTL_S)
        first = self.add_net("first")
        self.assertIn(first, self.client.get("/nns").text)

        second = self.add_net("second")
        self.assertNotIn(second, self.client.get("/nns").text)
        self.assertIn(second, self.logged_in.get("/nns").text)

        for cache in caches:
            cache.expiration = 0.01
        time.sleep(0.02)
        self.assertIn(second, self.client.get("/nns").text)


class TestVisitorState(_AnonymousListTestCase):
    username = "TestAnonymousStateUser"

    def test_each_visitor_gets_its_own_csrf_token(self):
        tokens = {
            test_support.extract_csrf_token(self.new_client().get("/nns").text)
            for _ in range(2)
        }
        self.assertEqual(len(tokens), 2)
        self.assertNotIn("", tokens)

    def test_each_visitor_gets_its_own_theme(self):
        self.client.get("/nns")
        dark = self.new_client()
        dark.cookies.set(THEME_COOKIE_NAME, "dark")
        self.assertIn('content="dark light"', dark.get("/nns").text)
        self.assertIn('content="light dark"', self.client.get("/nns").text)

    def test_each_master_only_value_has_its_own_list(self):
        master = self.add_net("master", is_master=True)
        other = self.add_net("other")
        filtered = self.new_client()
        filtered.cookies.set(MASTER_ONLY_COOKIE_NAME, "1")
        get_nns = self.rundb.get_nns
        with patch.object(self.rundb, "get_nns", wraps=get_nns) as read:
            for _ in range(2):
                response = self.client.get("/nns")
                self.assertIn(master, response.text)
                self.assertIn(other, response.text)
                response = filtered.get("/nns")
                self.assertIn(master, response.text)
                self.assertNotIn(other, response.text)
        self.assertEqual(
            sorted(call.kwargs["master_only"] for call in read.call_args_list),
            [False, True],
        )

    def test_active_run_filter_cookie_applies_to_the_shared_runs(self):
        hiding = self.new_client()
        hiding.cookies.set(ACTIVE_RUN_FILTERS_COOKIE_NAME, "none")
        for _ in range(2):
            self.assertIn("display: none !important", hiding.get("/tests").text)
            self.assertNotIn("display: none !important", self.client.get("/tests").text)


class TestSnapshotContent(_AnonymousListTestCase):
    username = "TestAnonymousContentUser"

    def test_rendering_leaves_the_shared_values_unchanged(self):
        self.add_net("shared", is_master=True)
        self.add_contributors(3)
        self.rundb.userdb.top_month.insert_one(
            {"username": f"{USER_PREFIX}Month", "cpu_hours": 1, "games": 1},
        )
        self.addCleanup(
            self.rundb.userdb.top_month.delete_many,
            {"username": f"{USER_PREFIX}Month"},
        )
        self.rundb.db["actions"].insert_one(
            {
                "action": "new_run",
                "username": f"{USER_PREFIX}Action",
                "run_id": "653db116cc309ae839563103",
                "time": datetime.now(UTC).timestamp(),
            },
        )
        self.addCleanup(
            self.rundb.db["actions"].delete_many,
            {"username": f"{USER_PREFIX}Action"},
        )
        active_id = self._create_run()
        finished_id = self._create_run()
        runs = self.rundb.runs
        runs.update_one(
            {"_id": self.rundb.get_run(active_id)["_id"]},
            {"$set": {"workers": 2, "cores": 2}},
        )
        runs.update_one(
            {"_id": self.rundb.get_run(finished_id)["_id"]},
            {"$set": {"finished": True, "last_updated": datetime.now(UTC)}},
        )

        # A copy taken when the read is stored sees a write by the first render.
        caches = [snapshot.cache for snapshot in SNAPSHOTS]
        stored = {}
        store = LRUCache.__setitem__

        def record(cache, key, value):
            if any(cache is snapshot_cache for snapshot_cache in caches):
                stored[id(cache), key] = copy.deepcopy(value)
            store(cache, key, value)

        hiding = self.new_client()
        hiding.cookies.set(ACTIVE_RUN_FILTERS_COOKIE_NAME, "none")
        with patch.object(LRUCache, "__setitem__", record):
            for client in (self.client, hiding):
                for path in LIST_PAGES:
                    self.assertEqual(client.get(path).status_code, 200, path)
                    response = client.get(path, headers=HX_FRAGMENT)
                    self.assertEqual(response.status_code, 200, path)

        for snapshot in SNAPSHOTS:
            with self.subTest(snapshot=snapshot.__name__):
                items = list(snapshot.cache.items())
                self.assertTrue(items)
                for key, value in items:
                    self.assertEqual(value, stored[id(snapshot.cache), key])

    def test_anonymous_and_logged_in_lists_match(self):
        self.add_net("same-a", is_master=True)
        self.add_net("same-b")
        self.add_contributors(3)
        for path in (
            "/tests/finished",
            "/tests/finished?success_only=1",
            "/nns",
            "/nns?sort=name&order=asc",
            "/nns?master_only=1",
            "/contributors",
            "/contributors?sort=games&order=asc",
        ):
            with self.subTest(path=path):
                anonymous = self.client.get(path, headers=HX_FRAGMENT).text
                logged_in = self.logged_in.get(path, headers=HX_FRAGMENT).text
                self.assertEqual(anonymous, logged_in)

    def test_contributors_snapshot_keeps_one_page_and_counts_everyone(self):
        self.add_contributors(CONTRIBUTORS_PAGE_SIZE + 5)
        collection = self.rundb.userdb.user_cache
        users, summary, num_users = _anonymous_contributors_list(collection)
        self.assertEqual(len(users), CONTRIBUTORS_PAGE_SIZE)
        self.assertEqual([user["_rank"] for user in users[:2]], [1, 2])
        self.assertEqual(num_users, collection.count_documents({}))
        self.assertEqual(
            summary["developers"],
            collection.count_documents({"tests": {"$gt": 0}}),
        )

        response = self.client.get("/contributors")
        shown = set(re.findall(rf"/tests/user/{USER_PREFIX}\d+", response.text))
        self.assertEqual(len(shown), CONTRIBUTORS_PAGE_SIZE)
