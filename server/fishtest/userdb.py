import hashlib
import re
import secrets
from datetime import UTC, datetime, timedelta

from pymongo import ASCENDING
from vtjson import ValidationError, validate

import fishtest.github_api as gh
from fishtest.http.settings import API_TOKEN_MAX_AGE_DAYS
from fishtest.lru_cache import lru_cache
from fishtest.schemas import api_token_schema, user_schema

DEFAULT_MACHINE_LIMIT = 16

# 32 random bytes in URL-safe base64. Junk is refused without a database read.
API_TOKEN_SHAPE = re.compile(r"fst_[A-Za-z0-9_-]{43}")


def validate_user(user):
    try:
        validate(user_schema, user, "user")
    except ValidationError as e:
        message = f"The user object does not validate: {str(e)}"
        print(message, flush=True)
        raise ValidationError(message) from None


def _hash_api_token(token):
    # A token is 256 random bits, so it cannot be guessed offline from its hash
    # and a fast hash can serve as the lookup key.
    return hashlib.sha256(token.encode()).hexdigest()


class UserDb:
    def __init__(self, db):
        self.db = db
        self.users = self.db["users"]
        self.user_cache = self.db["user_cache"]
        self.api_tokens = self.db["api_tokens"]
        self.top_month = self.db["top_month"]

    def clear_cache(self):
        self.get_pending.cache_clear()
        self.get_blocked.cache_clear()
        self.find_by_username.cache_clear()
        self.get_usernames.cache_clear()

    @lru_cache(
        expiration=120, refresh=False, filter=lambda f, args, kw, val: val is not None
    )
    def find_by_username(self, name):
        return self.users.find_one({"username": name})

    def find_by_email(self, email):
        return self.users.find_one({"email": email})

    def authenticate(self, username, password):
        def fail(*, user_message: str, code: str, log_message: str | None = None):
            print(log_message or user_message, flush=True)
            return {"error": user_message, "error_code": code}

        user = self.get_user(username)
        if user is None:
            # Avoid username enumeration: user-facing message is identical to wrong-password.
            return fail(
                user_message="Invalid username or password.",
                code="invalid_credentials",
                log_message=f"Login failed (unknown user): '{username}'",
            )

        if user.get("password") != password:
            return fail(
                user_message="Invalid username or password.",
                code="invalid_credentials",
                log_message=f"Login failed (wrong password): '{username}'",
            )

        if user.get("blocked"):
            return fail(
                user_message="Your account is blocked.",
                code="blocked",
                log_message=f"Login rejected (account blocked): '{username}'",
            )

        if user.get("pending"):
            return fail(
                user_message="Your account is pending approval.",
                code="pending",
                log_message=f"Login rejected (pending approval): '{username}'",
            )

        return {"username": username, "authenticated": True}

    def create_api_token(self, username):
        """Replace the token of a user and return the new one.

        Only the hash is stored, so this is the only time the token exists. A blocked
        or pending user gets None and no token.
        """
        if not self.is_active_user(username):
            return None
        token = f"fst_{secrets.token_urlsafe(32)}"
        record = {
            "token_hash": _hash_api_token(token),
            "username": username,
            "created_at": datetime.now(UTC),
        }
        validate(api_token_schema, record, "api_token")
        self.api_tokens.replace_one({"username": username}, record, upsert=True)
        print(f"API token created for user: {username}", flush=True)
        return token

    def revoke_api_token(self, username):
        """Remove the token of a user and report whether there was one."""
        # delete_many: without the unique index, concurrent creates can leave two.
        deleted = self.api_tokens.delete_many({"username": username}).deleted_count
        if deleted:
            print(f"API token revoked for user: {username}", flush=True)
        return bool(deleted)

    def get_api_token(self, username):
        return self.api_tokens.find_one({"username": username})

    @staticmethod
    def api_token_expires(record):
        return record["created_at"] + timedelta(days=API_TOKEN_MAX_AGE_DAYS)

    def is_active_user(self, username):
        """Report whether a user exists and is neither blocked nor pending.

        Reads the database and not the per-process user cache, so a block reaches
        every instance at once. Only a request that needs a login pays for this.
        """
        user = self.users.find_one(
            {"username": username},
            {"blocked": 1, "pending": 1},
        )
        return not (user is None or user.get("blocked") or user.get("pending"))

    def username_for_api_token(self, token):
        if not API_TOKEN_SHAPE.fullmatch(token):
            return None
        record = self.api_tokens.find_one({"token_hash": _hash_api_token(token)})
        if record is None or datetime.now(UTC) >= self.api_token_expires(record):
            return None
        username = record["username"]
        if self.is_active_user(username):
            return username
        # A block written outside the app leaves the token, and an unblock would
        # bring it back, so it ends here.
        self.revoke_api_token(username)
        return None

    def get_users(self):
        return self.users.find(sort=[("_id", ASCENDING)])

    @lru_cache(maxsize=1, expiration=30, refresh=False)
    def get_usernames(self):
        usernames = self.users.distinct("username")
        return sorted(
            [
                username
                for username in usernames
                if isinstance(username, str) and username
            ],
            key=str.lower,
        )

    @lru_cache(expiration=1, refresh=False)
    def get_pending(self):
        return list(self.users.find({"pending": True}, sort=[("_id", ASCENDING)]))

    @lru_cache(expiration=1, refresh=False)
    def get_blocked(self):
        return list(self.users.find({"blocked": True}, sort=[("_id", ASCENDING)]))

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
                "tests_repo": gh.canonicalize_repo_url(tests_repo),
                "machine_limit": DEFAULT_MACHINE_LIMIT,
            }
            validate_user(user)
            self.users.insert_one(user)
            self.clear_cache()

            return True
        except Exception:
            return None

    def save_user(self, user):
        if "tests_repo" in user:
            user["tests_repo"] = gh.canonicalize_repo_url(user["tests_repo"])
        validate_user(user)
        self.users.replace_one({"_id": user["_id"]}, user)
        self.clear_cache()

    def save_tests_repo(self, username, tests_repo):
        """Store the tests repository of a user and nothing else.

        The caller can hold a copy of the user cached before another instance changed
        the password, which save_user would write back.
        """
        tests_repo = gh.canonicalize_repo_url(tests_repo)
        validate(user_schema["tests_repo"], tests_repo, "tests_repo")
        self.users.update_one(
            {"username": username},
            {"$set": {"tests_repo": tests_repo}},
        )
        self.clear_cache()

    def remove_user(self, user, rejector):
        result = self.users.delete_one({"_id": user["_id"]})
        if result.deleted_count > 0:
            # User successfully deleted
            self.clear_cache()
            # The username can be registered again.
            self.revoke_api_token(user["username"])
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
