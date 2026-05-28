# ruff: noqa: ANN201, ANN206, D100, D101, D102, E501, INP001, PT009
"""Test admin-facing UI route contracts."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import test_support
from ui_user_test_case import UiUserTestCase

from fishtest.http.settings import (
    POLL_PENDING_USERS_NAV_S,
    POLL_RATE_LIMITS_GITHUB_S,
    POLL_RATE_LIMITS_SERVER_S,
    POLL_TYPESENSE_STATUS_SERVER_S,
)


class _TypesenseStatusServiceStub:
    def __init__(self, snapshot):
        self._snapshot = dict(snapshot)

    def status_snapshot(self):
        return dict(self._snapshot)


class TestAdminViews(UiUserTestCase):
    username = "TestAdminUser"

    def test_workers_server_side_filter_hx_fragment(self):
        recent_worker = "hxrecent-1cores-abcd"
        old_worker = "hxold-1cores-abcd"

        self.rundb.workerdb.update_worker(recent_worker, blocked=True, message="recent")
        self.rundb.workerdb.update_worker(old_worker, blocked=True, message="old")
        self.rundb.workerdb.workers.update_one(
            {"worker_name": old_worker},
            {"$set": {"last_updated": datetime.now(UTC) - timedelta(days=10)}},
        )
        self.rundb.workerdb.workers.update_one(
            {"worker_name": recent_worker},
            {"$set": {"last_updated": datetime.now(UTC) - timedelta(days=1)}},
        )

        try:
            response = self.client.get(
                "/workers/show?filter=gt-5days",
                headers={"HX-Request": "true"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(old_worker, response.text)
            self.assertNotIn(recent_worker, response.text)
            self.assertIn('id="workers_table"', response.text)
            self.assertIn("filter=gt-5days", response.text)
        finally:
            self.rundb.workerdb.workers.delete_many(
                {"worker_name": {"$in": [recent_worker, old_worker]}}
            )

    def test_workers_table_hx_sort_search_and_pagination_contract(self):
        worker_names = [f"H19Worker{idx:02d}-1cores-abcd" for idx in range(30)]
        for name in worker_names:
            self.rundb.workerdb.update_worker(name, blocked=True, message="h19")

        try:
            response = self.client.get(
                "/workers/show?filter=all-workers&sort=worker&order=asc&page=2&q=H19Worker&view=paged",
                headers={"HX-Request": "true"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn('id="workers_table"', response.text)
            self.assertIn('aria-sort="ascending"', response.text)
            self.assertIn(worker_names[25], response.text)
            self.assertNotIn(worker_names[0], response.text)
            self.assertIn(
                "filter=all-workers&amp;sort=worker&amp;order=asc&amp;q=H19Worker",
                response.text,
            )
            self.assertIn(
                'hx-get="/workers/show?filter=all-workers&sort=last_changed',
                response.text,
            )
            self.assertIn('hx-target="#workers-content"', response.text)
            self.assertIn('hx-push-url="true"', response.text)
            self.assertIn("view=all", response.text)
            self.assertIn(
                'id="workers_sort" name="sort" value="worker" hx-swap-oob="true"',
                response.text,
            )
            self.assertIn(
                'id="workers_order" name="order" value="asc" hx-swap-oob="true"',
                response.text,
            )
            self.assertIn(
                'id="workers_view" name="view" value="paged" hx-swap-oob="true"',
                response.text,
            )

            non_worker_match = self.client.get(
                "/workers/show?filter=all-workers&sort=worker&order=asc&q=actions?text&view=paged",
                headers={"HX-Request": "true"},
            )
            self.assertEqual(non_worker_match.status_code, 200)
            self.assertNotIn(worker_names[0], non_worker_match.text)
        finally:
            self.rundb.workerdb.workers.delete_many(
                {"worker_name": {"$regex": "^H19Worker"}}
            )

    def test_workers_hx_empty_state_fragment_renders_placeholder_row(self):
        with patch.object(self.rundb.workerdb, "get_blocked_workers", return_value=[]):
            response = self.client.get(
                "/workers/show?filter=all-workers",
                headers={"HX-Request": "true"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("No blocked workers", response.text)
        self.assertIn('colspan="3"', response.text)
        self.assertIn('id="workers_table"', response.text)

    def test_user_management_hx_empty_state_fragment_renders_placeholder_row(self):
        original_pending, original_groups = self._set_approver_state()

        try:
            self._login_user()

            with patch.object(self.rundb.userdb, "get_users", return_value=[]):
                response = self.client.get(
                    "/user_management?group=blocked",
                    headers={"HX-Request": "true"},
                )

            self.assertEqual(response.status_code, 200)
            self.assertIn("No blocked users", response.text)
            self.assertIn('colspan="4"', response.text)
            self.assertIn('id="user_management_table"', response.text)
        finally:
            self._restore_approver_state(original_pending, original_groups)

    def test_server_authoritative_tables_retire_legacy_sorting_js(self):
        js_path = (
            Path(__file__).resolve().parents[1]
            / "fishtest"
            / "static"
            / "js"
            / "application.js"
        )
        js_source = js_path.read_text(encoding="utf-8")

        self.assertNotIn("handleSortingTables", js_source)
        self.assertNotIn('row.dataset.noSort === "true"', js_source)

        templates_root = Path(__file__).resolve().parents[1] / "fishtest" / "templates"
        for template_name in (
            "contributors_content_fragment.html.j2",
            "machines_fragment.html.j2",
            "nns_content_fragment.html.j2",
            "user_management_content_fragment.html.j2",
            "workers_content_fragment.html.j2",
        ):
            template_source = (templates_root / template_name).read_text(
                encoding="utf-8"
            )
            self.assertNotIn('data-server-sort="true"', template_source)

    def test_user_management_lazy_group_hx_fragment(self):
        pending_user = "TestPendingGroupUser"
        blocked_user = "TestBlockedGroupUser"

        self.rundb.userdb.create_user(
            pending_user,
            "test-admin-password",
            "pending-group@example.com",
            self.tests_repo,
        )
        self.rundb.userdb.create_user(
            blocked_user,
            "test-admin-password",
            "blocked-group@example.com",
            self.tests_repo,
        )

        blocked_doc = self.rundb.userdb.get_user(blocked_user)
        blocked_doc["pending"] = False
        blocked_doc["blocked"] = True
        self.rundb.userdb.save_user(blocked_doc)

        original_pending, original_groups = self._set_approver_state()

        try:
            response = self.client.get("/login")
            csrf = test_support.extract_csrf_token(response.text)
            login = self.client.post(
                "/login",
                data={
                    "username": self.username,
                    "password": self.password,
                    "csrf_token": csrf,
                },
                follow_redirects=False,
            )
            self.assertEqual(login.status_code, 302)

            response = self.client.get(
                "/user_management?group=blocked",
                headers={"HX-Request": "true"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn(blocked_user, response.text)
            self.assertNotIn(pending_user, response.text)
            self.assertIn('id="user_management_table"', response.text)
        finally:
            self._restore_approver_state(original_pending, original_groups)

            pending_doc = self.rundb.userdb.get_user(pending_user)
            if pending_doc is not None:
                self.rundb.userdb.remove_user(pending_doc, self.username)
            blocked_doc = self.rundb.userdb.get_user(blocked_user)
            if blocked_doc is not None:
                self.rundb.userdb.remove_user(blocked_doc, self.username)

    def test_user_management_table_hx_sort_search_and_pagination_contract(self):
        created_users = [f"H19UmUser{idx:02d}" for idx in range(30)]
        original_pending, original_groups = self._set_approver_state()

        for idx, username in enumerate(created_users):
            self.rundb.userdb.create_user(
                username,
                "test-admin-password",
                f"h19-{idx}@example.com",
                self.tests_repo,
            )

        try:
            self._login_user()
            response = self.client.get(
                "/user_management?group=pending&sort=username&order=asc&page=2&q=H19UmUser&view=paged",
                headers={"HX-Request": "true"},
            )
            self.assertEqual(response.status_code, 200)
            self.assertIn('id="user_management_table"', response.text)
            self.assertIn('aria-sort="ascending"', response.text)
            self.assertIn(created_users[25], response.text)
            self.assertNotIn(created_users[0], response.text)
            self.assertIn(
                "sort=username&amp;order=asc&amp;q=H19UmUser",
                response.text,
            )
            self.assertIn("view=all", response.text)
            self.assertIn(
                'id="user_management_sort" name="sort" value="username" hx-swap-oob="true"',
                response.text,
            )
            self.assertIn(
                'id="user_management_order" name="order" value="asc" hx-swap-oob="true"',
                response.text,
            )
            self.assertIn(
                'id="user_management_view" name="view" value="paged" hx-swap-oob="true"',
                response.text,
            )

            non_username_match = self.client.get(
                "/user_management?group=pending&sort=username&order=asc&q=%40example.com&view=paged",
                headers={"HX-Request": "true"},
            )
            self.assertEqual(non_username_match.status_code, 200)
            self.assertNotIn(created_users[0], non_username_match.text)
        finally:
            self._restore_approver_state(original_pending, original_groups)

            for username in created_users:
                doc = self.rundb.userdb.get_user(username)
                if doc is not None:
                    self.rundb.userdb.remove_user(doc, self.username)

    @patch("fishtest.views.gh.rate_limit")
    def test_rate_limits_full_page_and_hx_fragment(self, mock_rate_limit):
        mock_rate_limit.return_value = {
            "remaining": 4321,
            "used": 5000,
            "reset": 4102444800,
        }

        full_response = self.client.get("/rate_limits")
        self.assertEqual(full_response.status_code, 200)
        self.assertIn("<!doctype html>", full_response.text.lower())
        self.assertIn("<th>Server</th>", full_response.text)
        self.assertIn("<th>Client</th>", full_response.text)
        self.assertIn('id="server_rate_limit"', full_response.text)
        self.assertIn('id="client_rate_limit"', full_response.text)
        self.assertIn(
            f'hx-trigger="load, every {POLL_RATE_LIMITS_SERVER_S}s ',
            full_response.text,
        )
        self.assertIn(
            "visibilitychange[document.visibilityState === 'visible'] from:document",
            full_response.text,
        )

        fragment_response = self.client.get("/rate_limits/server")
        self.assertEqual(fragment_response.status_code, 200)
        self.assertNotIn("<!doctype html>", fragment_response.text.lower())
        self.assertIn("4321", fragment_response.text)
        self.assertIn(
            'id="server_reset" hx-swap-oob="innerHTML"', fragment_response.text
        )

    @patch("fishtest.views.gh.rate_limit")
    def test_rate_limits_hx_header_still_returns_full_page(self, mock_rate_limit):
        mock_rate_limit.return_value = {
            "remaining": 123,
            "reset": 1700000000,
        }
        response = self.client.get(
            "/rate_limits",
            headers={"HX-Request": "true", "Sec-Fetch-Mode": "navigate"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn("<!doctype html>", response.text.lower())

    def test_rate_limits_sidebar_link_and_client_poll_contract(self):
        response = self.client.get("/rate_limits")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="rate-limits-nav-link"', response.text)
        self.assertIn('id="typesense-status-nav-link"', response.text)
        self.assertIn(
            f'data-poll-seconds="{POLL_RATE_LIMITS_GITHUB_S}"',
            response.text,
        )
        self.assertIn("dataset.githubRateLimitLow", response.text)
        self.assertIn(
            f'id="client_rate_limit" data-poll-seconds="{POLL_RATE_LIMITS_GITHUB_S}"',
            response.text,
        )

    def test_typesense_status_requires_approver(self):
        self._login_user()

        response = self.client.get("/typesense_status", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers.get("location"), "/tests")

        fragment_response = self.client.get("/typesense_status/server")
        self.assertEqual(fragment_response.status_code, 403)

    def test_typesense_status_full_page_and_hx_fragment(self):
        original_pending, original_groups = self._set_approver_state()
        actions_snapshot = {
            "route": "/actions",
            "alias": "actions_current",
            "collection_name": "actions_20260528164000",
            "enabled": True,
            "shadow_reads_enabled": True,
            "fallback_to_mongo": True,
            "sync_interval_seconds": 30,
            "reindex_interval_seconds": 0,
            "last_sync_completed_at": 1716914400.0,
            "last_reindex_completed_at": 1716914460.0,
            "last_fallback_at": 1716914520.0,
            "last_backend_unavailable_at": 1716914580.0,
            "sync_batches": 4,
            "synced_document_count": 18,
            "last_reindex_document_count": 18,
            "alias_swap_count": 1,
            "fallback_count": 2,
            "backend_unavailable_count": 1,
            "count_mismatch_count": 1,
            "result_mismatch_count": 0,
            "last_error": "actions backend timeout",
            "indexed_lag_seconds": 10.5,
        }
        finished_snapshot = {
            "route": "/tests/finished",
            "alias": "finished_runs_current",
            "collection_name": "finished_runs_20260528164000",
            "enabled": False,
            "shadow_reads_enabled": True,
            "fallback_to_mongo": True,
            "sync_interval_seconds": 45,
            "reindex_interval_seconds": 3600,
            "last_sync_completed_at": 1716914400.0,
            "last_reindex_completed_at": None,
            "last_fallback_at": None,
            "last_backend_unavailable_at": None,
            "sync_batches": 7,
            "synced_document_count": 52,
            "last_reindex_document_count": 0,
            "alias_swap_count": 0,
            "fallback_count": 0,
            "backend_unavailable_count": 0,
            "count_mismatch_count": 0,
            "result_mismatch_count": 1,
            "last_error": "",
            "indexed_lag_seconds": 4.0,
        }

        try:
            self._login_user()
            self.client.app.state.actions_search_service = _TypesenseStatusServiceStub(
                actions_snapshot,
            )
            self.client.app.state.finished_runs_search_service = (
                _TypesenseStatusServiceStub(finished_snapshot)
            )

            full_response = self.client.get("/typesense_status")
            self.assertEqual(full_response.status_code, 200)
            self.assertIn("<!doctype html>", full_response.text.lower())
            self.assertIn('id="typesense-status-nav-link"', full_response.text)
            self.assertIn('id="typesense_status_table"', full_response.text)
            self.assertIn('hx-get="/typesense_status/server"', full_response.text)
            self.assertIn(
                f'hx-trigger="load, every {POLL_TYPESENSE_STATUS_SERVER_S}s ',
                full_response.text,
            )
            self.assertIn("actions_20260528164000", full_response.text)
            self.assertIn("finished_runs_20260528164000", full_response.text)
            self.assertIn("10.5s", full_response.text)
            self.assertIn("actions backend timeout", full_response.text)

            fragment_response = self.client.get("/typesense_status/server")
            self.assertEqual(fragment_response.status_code, 200)
            self.assertNotIn("<!doctype html>", fragment_response.text.lower())
            self.assertIn('id="typesense_status_table"', fragment_response.text)
            self.assertIn("Finished runs", fragment_response.text)
            self.assertIn("Disabled", fragment_response.text)
            self.assertIn("45s", fragment_response.text)
            self.assertIn("3600s", fragment_response.text)
        finally:
            self._restore_approver_state(original_pending, original_groups)
            self.client.app.state.actions_search_service = None
            self.client.app.state.finished_runs_search_service = None

    def test_pending_users_nav_full_page_and_fragment_polling(self):
        pending_username = "TestPendingNavUser"

        self.rundb.userdb.create_user(
            pending_username,
            "test-admin-password",
            "pending-nav@example.com",
            self.tests_repo,
        )

        try:
            expected_count = len(self.rundb.userdb.get_pending())

            full_response = self.client.get("/rate_limits")
            self.assertEqual(full_response.status_code, 200)
            self.assertIn('id="pending-users-nav"', full_response.text)
            self.assertIn(
                'hx-get="/user_management/pending_count"',
                full_response.text,
            )
            self.assertIn(
                f'hx-trigger="load, every {POLL_PENDING_USERS_NAV_S}s '
                "[document.visibilityState === 'visible'], "
                "visibilitychange[document.visibilityState === 'visible'] "
                'from:document"',
                full_response.text,
            )
            self.assertIn(f"Users ({expected_count})", full_response.text)

            fragment_response = self.client.get("/user_management/pending_count")
            self.assertEqual(fragment_response.status_code, 200)
            self.assertNotIn("<!doctype html>", fragment_response.text.lower())
            self.assertIn('href="/user_management"', fragment_response.text)
            self.assertIn(
                'class="links-link rounded text-danger"',
                fragment_response.text,
            )
            self.assertIn(f"Users ({expected_count})", fragment_response.text)
        finally:
            pending_doc = self.rundb.userdb.get_user(pending_username)
            if pending_doc is not None:
                self.rundb.userdb.remove_user(pending_doc, self.username)

    def test_github_rate_limits_polling_uses_visibility_activation_and_client_threshold(
        self,
    ):
        js_path = (
            Path(__file__).resolve().parents[1]
            / "fishtest"
            / "static"
            / "js"
            / "application.js"
        )
        js_source = js_path.read_text(encoding="utf-8")

        self.assertIn(
            'document.addEventListener("visibilitychange", () => {',
            js_source,
        )
        self.assertIn(
            'window.addEventListener("focus", () => {',
            js_source,
        )
        self.assertIn(
            'window.addEventListener("pageshow", (event) => {',
            js_source,
        )
        self.assertIn(
            "!(navLink instanceof HTMLAnchorElement) &&",
            js_source,
        )
        self.assertIn("function isClientRateLimitLow(rateLimit_) {", js_source)
        self.assertNotIn("function serverRateLimit()", js_source)
        self.assertIn("function setGitHubRateLimitLowState(isLow) {", js_source)
        self.assertIn("function refreshClientRateLimitOnActivation() {", js_source)
        self.assertIn("clearPollTimeout();", js_source)
        self.assertIn(
            'localStorage.setItem("fishtest_github_rate_limit_low", value);',
            js_source,
        )
        self.assertIn(
            'classList.toggle("text-danger", isLow)',
            js_source,
        )
