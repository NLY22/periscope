import pytest


def test_backend_lock_prevents_two_owners_and_releases(scratch_path):
    from periscope.runtime.backend_lock import BackendLock

    path = scratch_path / "backend.lock"
    with BackendLock(path):
        with pytest.raises(RuntimeError):
            with BackendLock(path):
                pass
    with BackendLock(path):
        pass
