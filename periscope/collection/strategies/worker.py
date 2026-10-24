"""Run owned browser workers with bounded process-tree cleanup on timeout."""
from __future__ import annotations

import os
from pathlib import Path
import signal
import subprocess
import sys


def _stop_tree(process: subprocess.Popen) -> bool:
    if sys.platform == "win32":
        # Terminate descendants before the Python parent loses its tree identity.
        executable = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "taskkill.exe"
        try:
            result = subprocess.run([str(executable), "/PID", str(process.pid), "/T", "/F"],
                                    capture_output=True, timeout=5, creationflags=subprocess.CREATE_NO_WINDOW)
            stopped = result.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            stopped = False
        if process.poll() is None:
            process.kill()
        return stopped
    try:
        # start_new_session below makes this a group owned by this invocation.
        os.killpg(process.pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True


def run_worker(command, *, timeout: float, cwd: str, env: dict, capture_output: bool = True):
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {"start_new_session": True}
    with subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          cwd=cwd, env=env, **options) as process:
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as original:
            cleaned = _stop_tree(process)
            try:
                stdout, stderr = process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                stdout, stderr = original.output, original.stderr
                cleaned = False
            error = subprocess.TimeoutExpired(command, timeout, output=stdout, stderr=stderr)
            error.cleanup_failed = not cleaned
            raise error from None
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
