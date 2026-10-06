import asyncio
import ctypes
import importlib
import json
import os
import signal
import sys
import time
from pathlib import Path

import pytest

from reproagent.core.budget import Budget
from reproagent.core.models import BudgetLimits, ExecutionSpec, RunContext

FIXTURES = Path(__file__).parents[1] / "fixtures" / "processes"


def pid_alive(pid):
    if os.name == "nt":
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.OpenProcess.argtypes = (ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong)
        kernel.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_ulong)
        kernel.CloseHandle.argtypes = (ctypes.c_void_p,)
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            return False
        try:
            return kernel.WaitForSingleObject(handle, 0) == 258
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def fallback_cleanup(path):
    if path.exists():
        for pid in json.loads(path.read_text()):
            if pid_alive(pid):
                if os.name == "nt":
                    import subprocess
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
                else:
                    os.kill(pid, signal.SIGKILL)


@pytest.mark.parametrize("mode", ["timeout", "cancel"])
def test_timeout_and_cancel_reap_parent_and_child(tmp_path, mode):
    module = importlib.import_module("reproagent.adapters.runtimes.local")
    pid_file = tmp_path / "pids.json"
    async def run():
        context = RunContext(Budget(BudgetLimits(command_timeout_seconds=2)))
        spec = ExecutionSpec("spec-1", (sys.executable, str(FIXTURES / "spawn_child.py"), str(pid_file)), tmp_path)
        task = asyncio.create_task(module.LocalBackend().execute(spec, context))
        if mode == "cancel":
            end = time.monotonic() + 5
            while not pid_file.exists() and time.monotonic() < end:
                await asyncio.sleep(0.02)
            context.cancel_event.set()
        return await task
    try:
        result = asyncio.run(run())
        assert result.cleanup_ok
        assert result.stop_reason == ("CANCELLED" if mode == "cancel" else "COMMAND_TIMEOUT")
        assert pid_file.exists()
        assert not any(pid_alive(pid) for pid in json.loads(pid_file.read_text()))
    finally:
        fallback_cleanup(pid_file)


def test_child_is_reaped_after_parent_exits(tmp_path):
    module = importlib.import_module("reproagent.adapters.runtimes.local")
    pid_file = tmp_path / "pids.json"
    try:
        spec = ExecutionSpec("spec-1", (sys.executable, str(FIXTURES / "spawn_child.py"), str(pid_file), "exit"), tmp_path)
        result = asyncio.run(module.LocalBackend().execute(spec, RunContext(Budget(BudgetLimits()))))
        assert result.cleanup_ok and result.exit_code == 0
        assert not any(pid_alive(pid) for pid in json.loads(pid_file.read_text()))
    finally:
        fallback_cleanup(pid_file)


def test_argv_preserves_unicode_space_path_without_shell(tmp_path):
    module = importlib.import_module("reproagent.adapters.runtimes.local")
    spec = ExecutionSpec("spec-1", (sys.executable, "-c", "import sys,json;print(json.dumps(sys.argv[1:]))", "中文 路径", "a;b"), tmp_path)
    result = asyncio.run(module.LocalBackend().execute(spec, RunContext(Budget(BudgetLimits()))))
    assert result.exit_code == 0 and result.cleanup_ok
    assert json.loads((tmp_path / "stdout.log").read_text()) == ["中文 路径", "a;b"]


def test_log_limit_marks_truncated_and_stops_process(tmp_path):
    module = importlib.import_module("reproagent.adapters.runtimes.local")
    spec = ExecutionSpec("spec-1", (sys.executable, str(FIXTURES / "emit_logs.py")), tmp_path)
    context = RunContext(Budget(BudgetLimits(log_bytes=1024)))
    result = asyncio.run(module.LocalBackend().execute(spec, context))
    assert result.log_truncated
    assert result.stop_reason == "LOG_LIMIT"
    assert (tmp_path / "stdout.log").stat().st_size + (tmp_path / "stderr.log").stat().st_size <= 1024
