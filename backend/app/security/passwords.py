"""Argon2id password hashing."""

from __future__ import annotations

import asyncio

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

# OWASP-aligned Argon2id parameters (memory 64 MiB, 3 iterations, 4 lanes).
_hasher = PasswordHasher(time_cost=3, memory_cost=64 * 1024, parallelism=4, hash_len=32, salt_len=16)

# A pre-computed hash used to equalise timing when the account does not exist.
_DUMMY_HASH = _hasher.hash("timing-equaliser-not-a-real-password")


def hash_password_sync(password: str) -> str:
    return _hasher.hash(password)


def verify_password_sync(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


async def hash_password(password: str) -> str:
    return await asyncio.to_thread(hash_password_sync, password)


async def verify_password(password_hash: str | None, password: str) -> bool:
    return await asyncio.to_thread(verify_password_sync, password_hash, password)


COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "123456789",
        "1234567890",
        "qwertyuiop",
        "iloveyou",
        "letmein123",
        "welcome123",
        "admin12345",
        "passw0rd!",
        "qwerty1234",
        "1q2w3e4r5t",
        "abc123456",
    }
)


def password_problems(password: str, min_length: int) -> list[str]:
    problems = []
    if len(password) < min_length:
        problems.append(f"Password must be at least {min_length} characters.")
    if len(password) > 256:
        problems.append("Password must be at most 256 characters.")
    if password.lower() in COMMON_PASSWORDS:
        problems.append("This password is too common.")
    return problems
