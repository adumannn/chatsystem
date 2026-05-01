import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile

from chat_utils import RUNTIME_DIR


PBKDF2_ITERATIONS = 260000
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,32}$")


def is_valid_username(name):
    return bool(USERNAME_RE.fullmatch(name or ""))


class PasswordAuthenticator:
    def __init__(self, path=None):
        self.path = path or os.path.join(RUNTIME_DIR, "users.json")
        self.users = {}
        self._load()

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            self.users = {}
            return
        except (OSError, ValueError):
            self.users = {}
            return

        users = data.get("users", {}) if isinstance(data, dict) else {}
        self.users = users if isinstance(users, dict) else {}

    def _save(self):
        directory = os.path.dirname(self.path)
        os.makedirs(directory, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(
            prefix=".users.",
            suffix=".json",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(
                    {"version": 1, "users": self.users},
                    f,
                    indent=2,
                    sort_keys=True,
                )
                f.write("\n")
            os.replace(tmp_path, self.path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def authenticate(self, name, password):
        name = (name or "").strip()
        if not name:
            return False, "invalid", "Username cannot be empty"
        if not is_valid_username(name):
            return (
                False,
                "invalid",
                "Username must be 1-32 letters, numbers, dots, dashes, or underscores",
            )
        if password is None or password == "":
            return False, "password-required", "Password required"
        if len(password) > 256:
            return False, "invalid", "Password is too long"

        record = self.users.get(name)
        if record is None:
            self.users[name] = self._make_record(password)
            self._save()
            return True, "ok", "Account created"

        if self._verify(password, record):
            return True, "ok", "Login successful"
        return False, "wrong-password", "Password is wrong"

    def _make_record(self, password):
        salt = secrets.token_bytes(16)
        digest = self._hash_password(password, salt, PBKDF2_ITERATIONS)
        return {
            "salt": base64.b64encode(salt).decode("ascii"),
            "password_hash": base64.b64encode(digest).decode("ascii"),
            "iterations": PBKDF2_ITERATIONS,
        }

    def _verify(self, password, record):
        try:
            salt = base64.b64decode(record["salt"])
            expected = base64.b64decode(record["password_hash"])
            iterations = int(record.get("iterations", PBKDF2_ITERATIONS))
        except (KeyError, TypeError, ValueError):
            return False

        actual = self._hash_password(password, salt, iterations)
        return hmac.compare_digest(actual, expected)

    @staticmethod
    def _hash_password(password, salt, iterations):
        return hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            salt,
            iterations,
        )
