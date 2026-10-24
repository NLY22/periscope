"""A timed-out collector must stop the browser processes it owns as well."""
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest


def _running(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        stat = Path("/proc") / str(pid) / "stat"
        return not stat.exists() or stat.read_text().split()[2] != "Z"
    except ProcessLookupError:
        return False


def test_timeout_stops_the_collectors_child_process(scratch_path):
    from periscope.collection.strategies.worker import run_worker
    pid_file = scratch_path / "child.pid"
    code = ("import subprocess,sys,time;from pathlib import Path;"
            "p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(20)'],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);"
            "Path(sys.argv[1]).write_text(str(p.pid));time.sleep(20)")
    child_pid = None
    try:
        with pytest.raises(subprocess.TimeoutExpired):
            run_worker([sys.executable, "-c", code, str(pid_file)], timeout=2, cwd=str(scratch_path), env=os.environ.copy())
        assert pid_file.exists(), "The fixture must create a descendant before timing out"
        child_pid = int(pid_file.read_text())
        for _ in range(10):
            if not _running(child_pid):
                break
            time.sleep(.05)
        assert not _running(child_pid), "The collector died but its browser/driver analogue survived"
    finally:
        if child_pid is None and pid_file.exists():
            child_pid = int(pid_file.read_text())
        if child_pid and _running(child_pid):
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/PID", str(child_pid), "/T", "/F"], capture_output=True, timeout=5)
            else:
                import signal
                os.kill(child_pid, signal.SIGKILL)


def test_successful_worker_returns_its_output_without_process_cleanup(scratch_path):
    from periscope.collection.strategies.worker import run_worker
    result = run_worker([sys.executable, "-c", "print('checkpoint completed')"], timeout=5,
                        cwd=str(scratch_path), env=os.environ.copy())
    assert result.returncode == 0 and b"checkpoint completed" in result.stdout
