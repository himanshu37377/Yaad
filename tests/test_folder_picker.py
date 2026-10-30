import os
import subprocess
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

os.environ.setdefault("YAAD_DB_PATH", str(Path(tempfile.gettempdir()) / "yaad-picker-tests.sqlite3"))

from yaad.api import main
from yaad.schemas import FolderPickerRequest


def test_picker_adds_selected_directory_and_preserves_consent(monkeypatch, tmp_path):
    calls = []

    def choose(*args, **kwargs):
        return subprocess.CompletedProcess(args[0], 0, stdout=f"{tmp_path}\n", stderr="")

    monkeypatch.setattr(main.subprocess, "run", choose)
    monkeypatch.setattr(main.indexer, "add_folder", lambda path, consent: calls.append((path, consent)) or 41)

    result = main.pick_folder(FolderPickerRequest(cloud_consent=True))

    assert result == {
        "cancelled": False,
        "already_added": False,
        "folder_id": 41,
        "path": str(tmp_path.resolve()),
        "cloud_consent": True,
    }
    assert calls == [(str(tmp_path.resolve()), True)]


def test_picker_cancel_does_not_add_a_folder(monkeypatch):
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args[0], 1, stdout="", stderr="execution error: User canceled. (-128)"
        ),
    )
    monkeypatch.setattr(main.indexer, "add_folder", lambda *_: pytest.fail("canceled picker must not add a folder"))

    assert main.pick_folder(FolderPickerRequest()) == {"cancelled": True}


def test_picker_reports_native_chooser_failure(monkeypatch):
    monkeypatch.setattr(
        main.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 1, stdout="", stderr="unexpected failure"),
    )

    with pytest.raises(HTTPException) as error:
        main.pick_folder(FolderPickerRequest())

    assert error.value.status_code == 500
    assert "folder chooser" in error.value.detail
