from __future__ import annotations

from pathlib import Path

from app.core.config import Settings

ROOT = Path(__file__).resolve().parents[2]


def test_env_example_loads_with_blank_values(monkeypatch):
    """A fresh `.env` copied from `.env.example` (many `KEY=` lines) must load."""
    for key in ("COOKIE_SECURE", "COOKIE_DOMAIN", "PDF_FONT_PATH", "APP_ENV"):
        monkeypatch.delenv(key, raising=False)
    s = Settings(_env_file=ROOT / ".env.example")  # type: ignore[call-arg]
    assert s.cookie_secure is False  # development default
    assert s.pdf_font_path == ""


def test_blank_bool_in_env_falls_back_to_default(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("APP_ENV=production\nCOOKIE_SECURE=\n", encoding="utf-8")
    monkeypatch.delenv("COOKIE_SECURE", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    s = Settings(_env_file=env)  # type: ignore[call-arg]
    assert s.cookie_secure is True


def test_production_refuses_development_search_fallbacks(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("APP_ENV=production\nVECTOR_SEARCH_BACKEND=exact\nKEYWORD_SEARCH_BACKEND=text_index\n", encoding="utf-8")
    for key in ("APP_ENV", "VECTOR_SEARCH_BACKEND", "KEYWORD_SEARCH_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    problems = " ".join(Settings(_env_file=env).validate_for_startup())  # type: ignore[call-arg]
    assert "VECTOR_SEARCH_BACKEND=exact" in problems
    assert "KEYWORD_SEARCH_BACKEND=text_index" in problems


def test_production_needs_no_object_store(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    base = "APP_ENV=production\nMONGODB_URI=mongodb://example\nSESSION_SECRET=" + "s" * 48 + "\n"
    for key in (
        "APP_ENV",
        "STORAGE_BACKEND",
        "KEEP_ORIGINAL_FILES",
        "MONGODB_URI",
        "SESSION_SECRET",
        "PADDLE_ENABLED",
        "VECTOR_SEARCH_BACKEND",
        "KEYWORD_SEARCH_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    for backend in ("local", "mongodb"):
        env.write_text(base + f"STORAGE_BACKEND={backend}\n", encoding="utf-8")
        s = Settings(_env_file=env)  # type: ignore[call-arg]
        assert s.validate_for_startup() == [] and s.keep_original_files is False
    env.write_text(base + "STORAGE_BACKEND=s3\n", encoding="utf-8")
    assert "STORAGE_BACKEND=s3 requires S3_BUCKET." in Settings(_env_file=env).validate_for_startup()  # type: ignore[call-arg]
