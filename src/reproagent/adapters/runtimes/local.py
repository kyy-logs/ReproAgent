from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

from ...core.budget import BudgetStopped
from ...core.models import EvidenceRef, ProbeArtifacts, RawExecution
from ...core.serialization import bytes_hash
from .process_tree import ManagedProcess

SYSTEM_ENV = {"SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE", "LANG"}


class LocalBackend:
    async def execute(self, spec, context):
        context.budget.check()
        if context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")
        started = time.monotonic()
        timeout = context.budget.command_timeout()
        spec.cwd.mkdir(parents=True, exist_ok=True)
        log_root = spec.probe_path.parent if spec.probe_path is not None else spec.cwd
        log_root.mkdir(parents=True, exist_ok=True)
        out_path, err_path = log_root / "stdout.log", log_root / "stderr.log"
        env = {key: value for key, value in os.environ.items() if key.upper() in SYSTEM_ENV or key in spec.env_names}
        env.update(context.private_env)
        env.update(spec.env_overrides)
        env["PYTHONUTF8"] = "1"
        env['REPROAGENT_PROBE_LIMIT'] = str(min(context.budget.limits.log_bytes, 33554432))
        process = ManagedProcess.spawn(spec.argv, spec.cwd, env)
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
