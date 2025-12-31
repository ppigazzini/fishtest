import re
import sys
import threading
import time
from datetime import UTC, datetime
from secrets import token_urlsafe

from fishtest.schemas import user_schema
from pymongo import ASCENDING
from vtjson import ValidationError, validate

DEFAULT_MACHINE_LIMIT = 16


def validate_user(user):
    try:
        validate(user_schema, user, "user")
    except ValidationError as e:
        message = f"The user object does not validate: {str(e)}"
        print(message, flush=True)
        raise Exception(message)


class UserDb:
    def __init__(self, db):
        self.db = db
        self.users = self.db["users"]
        self.user_cache = self.db["user_cache"]
        self.top_month = self.db["top_month"]

    # Cache user lookups for 120s
    user_lock = threading.Lock()
    cache = {}

    def find_by_username(self, name):
        with self.user_lock:
            user = self.cache.get(name)
            if user and time.time() < user["time"] + 120:
                return user["user"]
            user = self.users.find_one({"username": name})
            if user is not None:
                self.cache[name] = {"user": user, "time": time.time()}
            return user

    def find_by_email(self, email):
        return self.users.find_one({"email": email})

    def find_by_oauth_subject(self, provider: str, subject: str):
        if not provider or not subject:
            return None
        return self.users.find_one({f"oauth.{provider}.sub": subject})

    def _sanitize_username_base(self, value: str) -> str:
        value = (value or "").strip()
        value = re.sub(r"[^A-Za-z0-9]", "", value)
        return value[:20]

    def _generate_unique_username(self, preferred: str) -> str:
        base = self._sanitize_username_base(preferred) or "user"
        candidate = base
        suffix = 0
        while self.find_by_username(candidate):
            suffix += 1
            candidate = f"{base}{suffix}"
        return candidate

    def link_oauth_identity(
        self,
        user: dict,
        provider: str,
        subject: str,
        *,
        email: str | None = None,
        email_verified: bool | None = None,
        login: str | None = None,
    ) -> dict:
        if not provider or not subject:
            raise ValueError("provider and subject are required")

        existing = self.find_by_oauth_subject(provider, subject)
        if existing is not None and existing.get("_id") != user.get("_id"):
            raise ValueError(
                f"OAuth identity already linked to another user: {provider}"
            )

        oauth = dict(user.get("oauth") or {})
        identity = {
            "sub": subject,
            "updated_at": datetime.now(UTC),
        }
        if login:
            identity["login"] = login
        if email:
            identity["email"] = email
        if email_verified is not None:
            identity["email_verified"] = bool(email_verified)

        oauth[provider] = identity
        user["oauth"] = oauth
        self.save_user(user)
        return user

    def get_or_create_user_from_oauth(
        self,
        provider: str,
        subject: str,
        *,
        email: str,
        email_verified: bool,
        preferred_username: str,
        login: str | None = None,
    ):
        if not email:
            return {"error": "Email is required"}

        user = self.find_by_oauth_subject(provider, subject)
        if user is None and email_verified:
            user = self.find_by_email(email)
            if user is not None:
                self.link_oauth_identity(
                    user,
                    provider,
                    subject,
                    email=email,
                    email_verified=email_verified,
                    login=login,
                )

        if user is not None:
            if user.get("blocked"):
                return {
                    "error": f"Account blocked for user: {user.get('username', '')}"
                }
            if user.get("pending"):
                return {
                    "error": f"Account pending for user: {user.get('username', '')}"
                }
            return {"username": user["username"], "authenticated": True}

        username = self._generate_unique_username(preferred_username)
        user = {
            "username": username,
            # Keep password login as a fallback for existing users.
            # For OAuth-created users, set a strong random password that is not disclosed.
            "password": token_urlsafe(32),
            "registration_time": datetime.now(UTC),
            "pending": True,
            "blocked": False,
            "email": email,
            "groups": [],
            "tests_repo": "",
            "machine_limit": DEFAULT_MACHINE_LIMIT,
            "oauth": {
                provider: {
                    "sub": subject,
                    "email": email,
                    "email_verified": bool(email_verified),
                    "updated_at": datetime.now(UTC),
                    **({"login": login} if login else {}),
                }
            },
        }
        try:
            validate_user(user)
        except Exception as e:
            return {"error": str(e)}
        self.users.insert_one(user)
        self.last_pending_time = 0
        self.last_blocked_time = 0
        self.clear_cache()
        return {"error": f"Account pending for user: {username}"}

    def clear_cache(self):
        with self.user_lock:
            self.cache.clear()

    def authenticate(self, username, password):
        user = self.get_user(username)
        if not user or user["password"] != password:
            sys.stderr.write("Invalid login: '{}' '{}'\n".format(username, password))
            return {"error": "Invalid password for user: {}".format(username)}
        if "blocked" in user and user["blocked"]:
            sys.stderr.write("Blocked account: '{}' '{}'\n".format(username, password))
            return {"error": "Account blocked for user: {}".format(username)}
        if "pending" in user and user["pending"]:
            sys.stderr.write("Pending account: '{}' '{}'\n".format(username, password))
            return {"error": "Account pending for user: {}".format(username)}

        return {"username": username, "authenticated": True}

    def get_users(self):
        return self.users.find(sort=[("_id", ASCENDING)])

    # Cache pending for 1s
    last_pending_time = 0
    last_blocked_time = 0
    last_pending = None
    pending_lock = threading.Lock()
    blocked_lock = threading.Lock()

    def get_pending(self):
        with self.pending_lock:
            if time.time() > self.last_pending_time + 1:
                self.last_pending = list(
                    self.users.find({"pending": True}, sort=[("_id", ASCENDING)])
                )
                self.last_pending_time = time.time()
            return self.last_pending

    def get_blocked(self):
        with self.blocked_lock:
            if time.time() > self.last_blocked_time + 1:
                self.last_blocked = list(
                    self.users.find({"blocked": True}, sort=[("_id", ASCENDING)])
                )
                self.last_blocked_time = time.time()
            return self.last_blocked

    def get_user(self, username):
        return self.find_by_username(username)

    def get_user_groups(self, username):
        user = self.get_user(username)
        if user is not None:
            groups = user["groups"]
            return groups

    def add_user_group(self, username, group):
        user = self.get_user(username)
        user["groups"].append(group)
        validate_user(user)
        self.users.replace_one({"_id": user["_id"]}, user)
        self.clear_cache()

    def create_user(self, username, password, email, tests_repo):
        try:
            if self.find_by_username(username) or self.find_by_email(email):
                return False
            # insert the new user in the db
            user = {
                "username": username,
                "password": password,
                "registration_time": datetime.now(UTC),
                "pending": True,
                "blocked": False,
                "email": email,
                "groups": [],
                "tests_repo": tests_repo,
                "machine_limit": DEFAULT_MACHINE_LIMIT,
            }
            validate_user(user)
            self.users.insert_one(user)
            self.last_pending_time = 0
            self.last_blocked_time = 0

            return True
        except Exception:
            return None

    def save_user(self, user):
        validate_user(user)
        self.users.replace_one({"_id": user["_id"]}, user)
        self.last_pending_time = 0
        self.last_blocked_time = 0
        self.clear_cache()

    def remove_user(self, user, rejector):
        result = self.users.delete_one({"_id": user["_id"]})
        if result.deleted_count > 0:
            # User successfully deleted
            self.last_pending_time = 0
            self.clear_cache()
            # logs rejected users to the server
            print(
                f"user: {user['username']} with email: {user['email']} was rejected by: {rejector}",
                flush=True,
            )
            return True
        else:
            # User not found
            return False

    def get_machine_limit(self, username):
        user = self.get_user(username)
        if user and "machine_limit" in user:
            return user["machine_limit"]
        return DEFAULT_MACHINE_LIMIT
