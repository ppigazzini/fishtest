from __future__ import annotations

import os
from dataclasses import dataclass

from authlib.integrations.requests_client import OAuth2Session


@dataclass(frozen=True)
class OAuthProvider:
    name: str
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str


PROVIDERS: dict[str, OAuthProvider] = {
    "github": OAuthProvider(
        name="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        userinfo_url="https://api.github.com/user",
        scope="read:user user:email",
    ),
    "google": OAuthProvider(
        name="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
    ),
}


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def provider_enabled(provider: str) -> bool:
    if provider not in PROVIDERS:
        return False
    if provider == "github":
        return bool(
            _env("FISHTEST_OAUTH_GITHUB_CLIENT_ID")
            and _env("FISHTEST_OAUTH_GITHUB_CLIENT_SECRET")
        )
    if provider == "google":
        return bool(
            _env("FISHTEST_OAUTH_GOOGLE_CLIENT_ID")
            and _env("FISHTEST_OAUTH_GOOGLE_CLIENT_SECRET")
        )
    return False


def enabled_providers() -> list[str]:
    return [p for p in PROVIDERS if provider_enabled(p)]


def make_oauth_client(provider: str, *, redirect_uri: str) -> OAuth2Session:
    if provider not in PROVIDERS:
        raise ValueError("Unknown provider")

    if provider == "github":
        client_id = _env("FISHTEST_OAUTH_GITHUB_CLIENT_ID")
        client_secret = _env("FISHTEST_OAUTH_GITHUB_CLIENT_SECRET")
    else:
        client_id = _env("FISHTEST_OAUTH_GOOGLE_CLIENT_ID")
        client_secret = _env("FISHTEST_OAUTH_GOOGLE_CLIENT_SECRET")

    if not client_id or not client_secret:
        raise ValueError(f"OAuth provider not configured: {provider}")

    p = PROVIDERS[provider]
    return OAuth2Session(
        client_id=client_id,
        client_secret=client_secret,
        scope=p.scope,
        redirect_uri=redirect_uri,
        token_endpoint_auth_method="client_secret_post",
    )


def fetch_profile(provider: str, oauth: OAuth2Session) -> dict:
    """Return normalized profile for auth/linking only.

    Intentionally excludes personal/profile fields (e.g., avatar URL) to avoid
    storing unnecessary personal data.
    """

    if provider == "github":
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        user = oauth.get(
            PROVIDERS[provider].userinfo_url, headers=headers, timeout=20
        ).json()
        sub = str(user.get("id") or "")
        login = user.get("login")
        email = user.get("email")
        email_verified = None
        emails = oauth.get(
            "https://api.github.com/user/emails", headers=headers, timeout=20
        )
        if emails.status_code == 200:
            email_rows = emails.json() or []
            primary_verified = next(
                (
                    r
                    for r in email_rows
                    if r.get("primary") is True
                    and r.get("verified") is True
                    and r.get("email")
                ),
                None,
            )
            any_verified = next(
                (r for r in email_rows if r.get("verified") is True and r.get("email")),
                None,
            )
            row = primary_verified or any_verified
            if row:
                email = row.get("email")
                email_verified = bool(row.get("verified"))

        return {
            "sub": sub,
            "email": email,
            "email_verified": bool(email_verified)
            if email_verified is not None
            else False,
            "login": login,
        }

    if provider == "google":
        profile = oauth.get(PROVIDERS[provider].userinfo_url, timeout=20).json()
        return {
            "sub": str(profile.get("sub") or ""),
            "email": profile.get("email"),
            "email_verified": bool(profile.get("email_verified")),
            "login": None,
        }

    raise ValueError("Unknown provider")


def exchange_code_for_token(
    provider: str, oauth: OAuth2Session, *, authorization_response_url: str
):
    headers = None
    if provider == "github":
        headers = {"Accept": "application/json"}
    return oauth.fetch_token(
        PROVIDERS[provider].token_url,
        authorization_response=authorization_response_url,
        headers=headers,
        timeout=20,
    )
