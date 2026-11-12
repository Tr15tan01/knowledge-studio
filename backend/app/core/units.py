"""Binary byte units. Integer bytes are the source of truth; conversion is display-only."""

from __future__ import annotations

KIB = 1024
MIB = 1024 * 1024  # 1,048,576 bytes
GIB = 1024 * 1024 * 1024  # 1,073,741,824 bytes


def format_bytes(n: int, *, precision: int = 1) -> str:
    """Format bytes using binary units with explicit IEC labels (KiB, MiB, GiB)."""
    if n < 0:
        return "-" + format_bytes(-n, precision=precision)
    if n < KIB:
        return f"{n} B"
    for unit, size in (("GiB", GIB), ("MiB", MIB), ("KiB", KIB)):
        if n >= size:
            value = n / size
            text = f"{value:.{precision}f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
    return f"{n} B"  # pragma: no cover


def mib(n: float) -> int:
    return int(n * MIB)


def gib(n: float) -> int:
    return int(n * GIB)
