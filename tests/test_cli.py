"""CLI entry points (previously zero coverage).

Uses typer's CliRunner against a temporary database via YAAD_DB_PATH;
no network, no model calls (cloud off, empty folder).
"""
import json

from typer.testing import CliRunner

from yaad.cli import app

runner = CliRunner()


def _env(tmp_path):
    return {"YAAD_DB_PATH": str(tmp_path / "cli.sqlite3"), "YAAD_CLOUD_ENABLED": "false"}


def test_cli_add_folder_index_and_rebuild(tmp_path):
    from yaad.config import get_settings

    get_settings.cache_clear()
    folder = tmp_path / "src"
    folder.mkdir()
    (folder / "note.txt").write_text("The venue fee is Rs 500.", encoding="utf-8")
    env = _env(tmp_path)
    added = runner.invoke(app, ["add-folder", str(folder)], env=env)
    assert added.exit_code == 0, added.output
    folder_id = added.output.strip()
    indexed = runner.invoke(app, ["index", "--folder-id", folder_id], env=env)
    assert indexed.exit_code == 0, indexed.output
    counts = json.loads(indexed.output)
    assert counts["indexed"] == 1
    rebuilt = runner.invoke(app, ["rebuild-fts"], env=env)
    assert rebuilt.exit_code == 0
    assert "rebuilt" in rebuilt.output.lower()
    get_settings.cache_clear()


def test_cli_add_folder_rejects_missing_path(tmp_path):
    from yaad.config import get_settings

    get_settings.cache_clear()
    result = runner.invoke(app, ["add-folder", str(tmp_path / "nope")], env=_env(tmp_path))
    assert result.exit_code != 0
    get_settings.cache_clear()


def test_cli_index_unknown_folder_errors(tmp_path):
    from yaad.config import get_settings

    get_settings.cache_clear()
    result = runner.invoke(app, ["index", "--folder-id", "999"], env=_env(tmp_path))
    assert result.exit_code == 1
    assert "Error" in result.output
    get_settings.cache_clear()
