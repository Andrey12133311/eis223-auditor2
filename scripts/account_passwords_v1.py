"""Password hashing for accounts.

Install argon2-cffi in the isolated identity service before using.
No passwords or hashes are logged or stored in the EIS evidence database.
"""
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_HASHER = PasswordHasher(time_cost=3, memory_cost=65536, parallelism=2)


def hash_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 256:
        raise ValueError("Password must be 12 to 256 characters")
    return _HASHER.hash(password)


def verify_password(encoded, password):
    try:
        return _HASHER.verify(encoded, password)
    except (VerificationError, InvalidHashError, TypeError):
        return False
