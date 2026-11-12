"""Language detection: script-based for Georgian (not in langdetect's profiles), langdetect otherwise."""

from __future__ import annotations


def _script_share(text: str, lo: int, hi: int) -> float:
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for c in letters if lo <= ord(c) <= hi) / len(letters)


def detect_language(text: str) -> tuple[str | None, float]:
    sample = text[:20000]
    if not sample.strip():
        return None, 0.0
    ka = _script_share(sample, 0x10A0, 0x10FF) + _script_share(sample, 0x1C90, 0x1CBF)
    if ka > 0.3:
        return "ka", round(min(1.0, ka), 2)
    try:
        from langdetect import DetectorFactory, detect_langs

        DetectorFactory.seed = 0
        best = detect_langs(sample)[0]
        return best.lang, round(best.prob, 2)
    except Exception:  # noqa: BLE001
        return None, 0.0
