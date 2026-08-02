"""Offline tests for the dashboard's .env handling and validation."""
from visabot import dashboard


def test_read_env_skips_comments_and_blanks(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\n\nAIS_EMAIL=a@b.com\nTEST_MODE = true\nBROKEN LINE\n")
    vals = dashboard.read_env(p)
    assert vals == {"AIS_EMAIL": "a@b.com", "TEST_MODE": "true"}


def test_write_env_preserves_comments_and_order(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# keep me\nAIS_EMAIL=a@b.com\nTEST_MODE=true\n")
    dashboard.write_env({"TEST_MODE": "false", "POLL_BASE_SECONDS": "240"}, p)
    text = p.read_text()
    assert "# keep me" in text
    assert text.count("TEST_MODE=") == 1
    assert "TEST_MODE=false" in text
    assert text.strip().endswith("POLL_BASE_SECONDS=240")  # new keys appended
    assert text.index("AIS_EMAIL") < text.index("TEST_MODE")  # order kept
    assert (p.stat().st_mode & 0o777) == 0o600


def test_write_env_roundtrip(tmp_path):
    p = tmp_path / ".env"
    p.write_text("A=1\n")
    dashboard.write_env({"A": "2", "B": "x y"}, p)
    assert dashboard.read_env(p) == {"A": "2", "B": "x y"}


def test_secret_detection_and_masking(tmp_path, monkeypatch):
    monkeypatch.setattr(dashboard, "ENV_PATH", tmp_path / ".env")
    dashboard.ENV_PATH.write_text("AIS_PASSWORD=hunter2\nAIS_EMAIL=a@b.com\nEXTRA_TOKEN=t\n")
    masked = {k["key"]: k for k in dashboard.masked_config()}
    assert masked["AIS_PASSWORD"]["value"] == dashboard.MASK
    assert masked["AIS_PASSWORD"]["present"] is True
    assert masked["AIS_EMAIL"]["value"] == "a@b.com"          # not a secret
    assert masked["EXTRA_TOKEN"]["value"] == dashboard.MASK   # unknown key, secret-like name
    revealed = {k["key"]: k for k in dashboard.masked_config(reveal=True)}
    assert revealed["AIS_PASSWORD"]["value"] == "hunter2"
    # unset secrets show as empty, not masked, so the form reads as "not set"
    assert masked["PUSHOVER_APP_TOKEN"]["value"] == ""


def test_validate():
    ok = dashboard.validate
    assert ok("POLL_BASE_SECONDS", "180") is None
    assert ok("POLL_BASE_SECONDS", "10") is not None       # too fast
    assert ok("POLL_BASE_SECONDS", "abc") is not None
    assert ok("NIGHT_MULTIPLIER", "1") is None
    assert ok("NIGHT_MULTIPLIER", "0.5") is not None       # below 1
    assert ok("LATEST_DATE", "2026-08-21") is None
    assert ok("LATEST_DATE", "21/08/2026") is not None
    assert ok("TEST_MODE", "false") is None
    assert ok("TEST_MODE", "maybe") is not None
    assert ok("AIS_EMAIL", "not-an-email") is not None
    assert ok("AIS_PASSWORD", "") is not None
    assert ok("UNKNOWN_FREE_TEXT", "anything") is None
