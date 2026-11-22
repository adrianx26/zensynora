"""Release-local .env loading for traditional installations."""

from __future__ import annotations

from pathlib import Path


def test_load_env_file_keeps_process_values_and_parses_quotes(tmp_path, monkeypatch):
    from myclaw import load_env_file

    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "MYCLAW_TEST_NEW='quoted value'\n"
        'MYCLAW_TEST_DOUBLE="double value"\n'
        "MYCLAW_TEST_COMMENT=value # ignored\n"
        "MYCLAW_TEST_HASH=token#kept\n"
        "export MYCLAW_TEST_EXISTING=from-file\n"
        "MALFORMED\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MYCLAW_TEST_EXISTING", "from-process")
    monkeypatch.delenv("MYCLAW_TEST_NEW", raising=False)
    monkeypatch.delenv("MYCLAW_TEST_DOUBLE", raising=False)

    monkeypatch.delenv("MYCLAW_TEST_COMMENT", raising=False)
    monkeypatch.delenv("MYCLAW_TEST_HASH", raising=False)

    assert load_env_file(env) == 4
    assert __import__("os").environ["MYCLAW_TEST_NEW"] == "quoted value"
    assert __import__("os").environ["MYCLAW_TEST_DOUBLE"] == "double value"
    assert __import__("os").environ["MYCLAW_TEST_COMMENT"] == "value"
    assert __import__("os").environ["MYCLAW_TEST_HASH"] == "token#kept"
    assert __import__("os").environ["MYCLAW_TEST_EXISTING"] == "from-process"


def test_systemd_service_reads_release_env_file():
    script = Path("install.sh").read_text(encoding="utf-8")
    assert 'EnvironmentFile=-$SCRIPT_DIR/.env' in script
