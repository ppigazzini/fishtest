"""Minimal `pyramid.testing` stubs used by legacy fishtest unit tests."""

from __future__ import annotations

from dataclasses import dataclass

from .response import Response

_ROUTES: dict[str, str] = {}
_SECURITY_POLICY = None


class DummySession:
    def __init__(self):
        self._flashes: dict[str, list] = {}

    def flash(self, msg, queue: str = ""):
        self._flashes.setdefault(queue, []).append(msg)

    def pop_flash(self, queue: str = ""):
        return self._flashes.pop(queue, [])

    def invalidate(self):
        self._flashes.clear()


@dataclass
class _DummyRoute:
    name: str


class DummyRequest:
    def __init__(self, **kw):
        self.method = kw.pop("method", "GET")
        self.params = kw.pop("params", {})
        self.matchdict = kw.pop("matchdict", {})
        self.headers = kw.pop("headers", {})
        self.cookies = kw.pop("cookies", {})
        self.remote_addr = kw.pop("remote_addr", None)
        self.json_body = kw.pop("json_body", None)

        self.matched_route = kw.pop("matched_route", _DummyRoute(""))
        self.exception = kw.pop("exception", None)

        self.session = kw.pop("session", None) or DummySession()

        # Pyramid request attributes used by the legacy fishtest code/tests.
        authenticated_userid = kw.pop("authenticated_userid", None)
        if authenticated_userid is None and _SECURITY_POLICY is not None:
            authenticated_userid = getattr(_SECURITY_POLICY, "userid", None)
        self.authenticated_userid = authenticated_userid
        self.response = kw.pop("response", None) or Response()
        self.host_url = kw.pop("host_url", "http://localhost")
        self.url = kw.pop("url", "")
        self.path_qs = kw.pop("path_qs", "")
        self.path = kw.pop("path", "")

        for k, v in kw.items():
            setattr(self, k, v)

    @property
    def path_url(self) -> str:
        # In Pyramid, request.path_url is the absolute URL without query string.
        return f"{self.host_url}{self.path}"

    def has_permission(self, permission: str, context=None) -> bool:
        # Minimal behavior for unit tests: allow all permissions when a
        # permissive security policy is installed and a userid is present.
        if self.authenticated_userid is None:
            return False
        return bool(getattr(_SECURITY_POLICY, "permissive", True))

    @property
    def POST(self):
        # In Pyramid, POST is a MultiDict of form fields; in unit tests we pass
        # form fields via `params`, so expose it as an alias.
        return self.params

    @POST.setter
    def POST(self, value):
        self.params = value

    def route_url(self, route_name: str, **kw) -> str:
        pattern = _ROUTES.get(route_name, "")
        for k, v in kw.items():
            pattern = pattern.replace("{" + k + "}", str(v))
        return pattern


class _Config:
    def add_route(self, name: str, pattern: str) -> None:
        _ROUTES[name] = pattern

    def set_security_policy(self, policy) -> None:
        global _SECURITY_POLICY
        _SECURITY_POLICY = policy

    # Back-compat convenience used by some Pyramid tests.
    def testing_securitypolicy(self, userid=None, permissive=True):
        policy = DummySecurityPolicy(userid=userid, permissive=permissive)
        self.set_security_policy(policy)
        return policy


@dataclass
class DummySecurityPolicy:
    userid: str | None = None
    permissive: bool = True


def setUp():
    return _Config()


def tearDown():
    global _SECURITY_POLICY
    _ROUTES.clear()
    _SECURITY_POLICY = None
