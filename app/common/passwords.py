from __future__ import annotations

import re


_COMMON_PASSWORDS = {
    "password",
    "password1",
    "password123",
    "12345678",
    "123456789",
    "qwerty123",
    "admin123",
    "admin@123",
    "ventas@123",
    "produccion@123",
    "cliente@123",
    "softbakery",
    "softbakery123",
}


def is_password_insecure(password: str) -> bool:
    pwd = (password or "").strip()
    if not pwd:
        return True

    lowered = pwd.lower()
    if lowered in _COMMON_PASSWORDS:
        return True

    # Block very repetitive passwords like "aaaaaaaa", "11111111"
    if re.fullmatch(r"(.)\1{7,}", pwd):
        return True

    # Block sequential numeric passwords of length >= 8
    if re.fullmatch(r"\d{8,}", pwd):
        # allow non-trivial numeric? Business rules discourage common patterns; keep strict.
        return True

    return False

