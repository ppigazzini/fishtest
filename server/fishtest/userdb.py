from __future__ import annotations

import sys
import threading
import time
from datetime import UTC, datetime

from fishtest.schemas import user_schema
from pymongo import ASCENDING
from vtjson import ValidationError, validate

DEFAULT_MACHINE_LIMIT = 16


type UserDoc = dict[str, object]


def validate_user(user: UserDoc) -> None:
    try:
        validate(user_schema, user, "user")
    except ValidationError as e:
        message = f"The user object does not validate: {str(e)}"
        print(message, flush=True)
        raise Exception(message)


class UserDb:
    def __init__(self, db: object) -> None:
        self.db: object = db
        self.users = self.db["users"]
        self.user_cache = self.db["user_cache"]
        self.top_month = self.db["top_month"]

    # Cache user lookups for 120s
    user_lock = threading.Lock()
    cache: dict[str, dict[str, object]] = {}

    def find_by_username(self, name: str) -> UserDoc | None:
        with self.user_lock:
            user = self.cache.get(name)
            if user and time.time() < user["time"] + 120:
                return user["user"]  # type: ignore[return-value]
            user = self.users.find_one({"username": name})
            if user is not None:
                self.cache[name] = {"user": user, "time": time.time()}
            return user

    def find_by_email(self, email: str) -> UserDoc | None:
        return self.users.find_one({"email": email})

    def clear_cache(self) -> None:
        with self.user_lock:
            self.cache.clear()

    def authenticate(self, username: str, password: str) -> dict[str, object]:
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

    def get_users(self) -> object:
        return self.users.find(sort=[("_id", ASCENDING)])

    # Cache pending for 1s
    last_pending_time = 0
    last_blocked_time = 0
    last_pending = None
    pending_lock = threading.Lock()
    blocked_lock = threading.Lock()

    def get_pending(self) -> list[UserDoc] | None:
        with self.pending_lock:
            if time.time() > self.last_pending_time + 1:
                self.last_pending = list(
                    self.users.find({"pending": True}, sort=[("_id", ASCENDING)])
                )
                self.last_pending_time = time.time()
            return self.last_pending

    def get_blocked(self) -> list[UserDoc] | None:
        with self.blocked_lock:
            if time.time() > self.last_blocked_time + 1:
                self.last_blocked = list(
                    self.users.find({"blocked": True}, sort=[("_id", ASCENDING)])
                )
                self.last_blocked_time = time.time()
            return self.last_blocked

    def get_user(self, username: str) -> UserDoc | None:
        return self.find_by_username(username)

    def get_user_groups(self, username: str) -> list[str] | None:
        user = self.get_user(username)
        if user is not None:
            groups = user["groups"]
            return groups  # type: ignore[return-value]
        return None

    def add_user_group(self, username: str, group: str) -> None:
        user = self.get_user(username)
        user["groups"].append(group)
        validate_user(user)
        self.users.replace_one({"_id": user["_id"]}, user)
        self.clear_cache()

    def create_user(
        self, username: str, password: str, email: str, tests_repo: str
    ) -> bool | None:
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

    def save_user(self, user: UserDoc) -> None:
        validate_user(user)
        self.users.replace_one({"_id": user["_id"]}, user)
        self.last_pending_time = 0
        self.last_blocked_time = 0
        self.clear_cache()

    def remove_user(self, user: UserDoc, rejector: str) -> bool:
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

    def get_machine_limit(self, username: str) -> int:
        user = self.get_user(username)
        if user and "machine_limit" in user:
            return user["machine_limit"]  # type: ignore[return-value]
        return DEFAULT_MACHINE_LIMIT
