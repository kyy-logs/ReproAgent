from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

from ...core.budget import BudgetStopped
from ...core.models import EvidenceRef, ProbeArtifacts, RawExecution
from ...core.serialization import bytes_hash
from ...observability import span
from ...paths import directory_path, workspace_path
from .process_tree import ManagedProcess

SYSTEM_ENV = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG"}

#: What a run's own signals say about the process, and nothing more.
#:
#: A candidate whose tests fail is a *successful* process: exit code 1 is the
#: reproduction being observed, not a fault, so the exit code deliberately does not
#: appear in this judgement.  Anything the signals do not cover stays unknown rather
#: than being reported as healthy.
PROCESS_HEALTH = {
    "EXITED": "passed",             # it started, ended by itself, and was reaped
    "COMMAND_TIMEOUT": "failed",
    "CANCELLED": "blocked",
    "LOG_LIMIT": "blocked",
    "EXHAUSTED": "blocked",         # the task's own budget stopped it
}


def process_health(stop_reason, cleanup_ok) -> str:
    """Reuse the signals the run already produced; never invent one."""
    if not cleanup_ok:
        # Whatever ended it, a process tree that did not come down is degraded.
        return "degraded"
    return PROCESS_HEALTH.get(stop_reason, "unknown")


class LocalBackend:
    async def execute(self, spec, context):
        with span("process") as handle:
            result = await self._execute(spec, context)
            handle.annotate(result_code=result.stop_reason, stop_reason=result.stop_reason,
                            exit_code=result.exit_code, cleanup_ok=result.cleanup_ok,
                            health=process_health(result.stop_reason, result.cleanup_ok))
            return result

    async def _execute(self, spec, context):
        context.budget.check()
        if context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED", dimension="cancelled")
        started = time.monotonic()
        timeout = context.budget.command_timeout()
        cwd = workspace_path(spec.cwd)
        # The run copy is a directory, and a directory stops working before a file
        # does, so it is created through the form that reaches it at any depth.
        directory_path(cwd).mkdir(parents=True, exist_ok=True)
        log_root = workspace_path(spec.probe_path).parent if spec.probe_path is not None else cwd
        directory_path(log_root).mkdir(parents=True, exist_ok=True)
        out_path, err_path = workspace_path(log_root / "stdout.log"), workspace_path(log_root / "stderr.log")
        env = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENV or key in spec.env_names}
        env.update(context.private_env)
        env.update(spec.env_overrides)
        env["PYTHONUTF8"] = "1"
        env['REPROAGENT_PROBE_LIMIT'] = str(min(context.budget.limits.log_bytes, 33554432))
        process = ManagedProcess.spawn(spec.argv, cwd, env)
        truncated = threading.Event()
        pump_errors = []
        lock = threading.Lock()
        written = [0]
        def pump(stream, path):
            try:
                with path.open("wb") as output:
                    while chunk := stream.read(8192):
                        with lock:
                            remaining = max(0, context.budget.limits.log_bytes - written[0])
                            kept = chunk[:remaining]
                            written[0] += len(kept)
                            if len(kept) != len(chunk):
                                truncated.set()
                        output.write(kept)
                        output.flush()
            except (OSError, ValueError) as exc:
                pump_errors.append(str(exc))
        threads = [threading.Thread(target=pump, args=(process.stdout, out_path), daemon=True),
                   threading.Thread(target=pump, args=(process.stderr, err_path), daemon=True)]
        for thread in threads:
            thread.start()
        stop_reason, code, cleanup_ok = "EXITED", None, False
        def check_probe_limit():
            if spec.probe_path and spec.probe_path.exists() and written[0] + spec.probe_path.stat().st_size > context.budget.limits.log_bytes:
                truncated.set()
        try:
            while True:
                if context.cancel_event.is_set():
                    stop_reason = "CANCELLED"
                    break
                try:
                    context.budget.check()
                except BudgetStopped as exc:
                    stop_reason = exc.reason
                    break
                check_probe_limit()
                if truncated.is_set():
                    stop_reason = "LOG_LIMIT"
                    break
                code = process.poll()
                if code is not None:
                    break
                if time.monotonic() - started >= timeout:
                    stop_reason = "COMMAND_TIMEOUT"
                    break
                await asyncio.sleep(0.02)
        except asyncio.CancelledError:
            stop_reason = "CANCELLED"
            context.cancel_event.set()
        finally:
            cleanup_ok = await asyncio.to_thread(process.terminate_tree, context.budget.limits.cleanup_timeout_seconds)
            for thread in threads:
                await asyncio.to_thread(thread.join, 0.5)
            cleanup_ok = cleanup_ok and not any(t.is_alive() for t in threads) and not pump_errors
            process.close()
        check_probe_limit()
        if code == 86 and spec.probe_path:
            truncated.set()
        if truncated.is_set():
            stop_reason = "LOG_LIMIT" if stop_reason == "EXITED" else stop_reason
        def reference(path):
            if not path.exists():
                path.write_bytes(b"")
            return EvidenceRef(str(path), bytes_hash(path.read_bytes()))
        return RawExecution(spec.spec_id, code, time.monotonic() - started, stop_reason,
                            reference(out_path), reference(err_path), cleanup_ok,
                            ProbeArtifacts(spec.probe_path) if spec.probe_path else None, truncated.is_set())
