"""Atomic state writes tolerate brief Windows sharing locks without losing data."""
import os

import pytest

from src import _file_utils


def _sharing_error():
    error = PermissionError("simulated Windows file lock")
    error.winerror = 32
    return error


def test_atomic_write_recovers_from_a_temporary_windows_lock(scratch_path, monkeypatch):
    path = scratch_path / "state.json"
    path.write_text("previous state", encoding="utf-8")
    replace = os.replace
    calls = []
    def temporarily_locked(source, target):
        calls.append(target)
        if len(calls) == 1:
            assert path.read_text(encoding="utf-8") == "previous state"
            raise _sharing_error()
        return replace(source, target)
    monkeypatch.setattr(_file_utils.os, "replace", temporarily_locked)
    _file_utils._atomic_write_text(path, "updated state")
    assert path.read_text(encoding="utf-8") == "updated state"
    assert not list(scratch_path.glob("*.tmp"))


def test_atomic_write_keeps_old_state_when_lock_does_not_clear(scratch_path, monkeypatch):
    path = scratch_path / "state.json"
    path.write_text("previous state", encoding="utf-8")
    calls = []
    def locked(*args):
        calls.append(1)
        raise _sharing_error()
    monkeypatch.setattr(_file_utils.os, "replace", locked)
    with pytest.raises(PermissionError):
        _file_utils._atomic_write_text(path, "new state")
    assert path.read_text(encoding="utf-8") == "previous state"
    assert len(calls) <= 8
    assert not list(scratch_path.glob("*.tmp"))
