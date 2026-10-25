"""Shared runtime entry point used by both generated wrappers.

The wrappers only translate their platform's arguments and select a tool Python.
Everything else -- model-config binding, credential-source selection, readiness
checking and forwarding to the existing CLI -- happens here, so both hosts follow
the same rules.  The actual CLI process uses this module's interpreter, never a
second lookup on PATH.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from .credentials import CredentialError, discover_source, load_binding, load_child_environment
from .render import decode_binding


def _platform_name() -> str:
    return "nt" if os.name == "nt" else "posix"


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _error(action: str, code: str, message: str) -> int:
    _emit({"launcher": True, "action": action, "ready": False,
           "code": code, "error": message})
    return 2


def probe_tool_python(tool_python: Path) -> dict:
    """Check that one interpreter can import this module, without writing bytecode."""
    path = Path(tool_python)
    if not path.is_file():
        return {"available": False, "error": "tool_python_missing"}
    environment = dict(os.environ)
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    code = ("import json,platform; import reproagent.integration.launcher; "
            "print(json.dumps({'version': platform.python_version()}))")
    try:
        result = subprocess.run([str(path), "-c", code], capture_output=True,
                                text=True, env=environment, timeout=30, shell=False)
    except (OSError, subprocess.TimeoutExpired):
        return {"available": False, "error": "tool_python_failed"}
    if result.returncode != 0:
        return {"available": False, "error": "tool_python_import_failed"}
    try:
        payload = json.loads(result.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"available": False, "error": "tool_python_probe_invalid"}
    if not isinstance(payload, dict) or not isinstance(payload.get("version"), str):
        return {"available": False, "error": "tool_python_probe_invalid"}
    return {"available": True, "version": payload["version"]}


def _cli_argv(action: str, target: Path, binding: Mapping,
              fixed_repo: Path | None, fixed_python: str | None) -> list[str]:
    if action == "inspect":
        return [sys.executable, "-m", "reproagent", "inspect", str(target)]
    argv = [sys.executable, "-m", "reproagent", "run", "--config", str(target),
            "--model-config", binding["model_config"]]
    if fixed_repo is not None:
        argv += ["--fixed-repo", str(fixed_repo)]
    if fixed_python is not None:
        argv += ["--fixed-python", str(fixed_python)]
    return argv


def launch(action: str, target: Path | None, binding: Mapping, *,
           fixed_repo: Path | None = None, fixed_python: str | None = None,
           environ: Mapping[str, str] | None = None,
           platform_name: str | None = None) -> int:
    """Run check/inspect/run for one host entry point and return its exit code."""
    platform_name = platform_name or _platform_name()
    parent = dict(os.environ) if environ is None else dict(environ)
    if action == "check":
        source = discover_source(binding, environ=parent, platform_name=platform_name)
        ready = (binding.get("model_config_status") == "ready"
                 and source.get("kind") not in ("missing", "invalid_credential_file"))
        _emit({"launcher": True, "action": "check", "ready": ready,
               "model_config_status": binding.get("model_config_status"),
               "api_key_env": binding.get("api_key_env"),
               "credential_source": source, "run_configured": ready})
        if source.get("kind") == "invalid_credential_file":
            return 2
        return 0 if ready else 1
    if action == "inspect":
        if target is None:
            return _error("inspect", "target_required", "inspect requires a task directory")
        # No model config and no credential lookup: inspect only reads records.
        try:
            result = subprocess.run(_cli_argv("inspect", target, binding, None, None),
                                    shell=False, env=None)
        except OSError:
            return _error("inspect", "process_launch_failed", "could not start the ReproAgent CLI")
        return result.returncode
    if action != "run":
        return _error(str(action), "action_invalid", "unsupported launcher action")
    if target is None:
        return _error("run", "target_required", "run requires a task configuration")
    if not binding.get("model_config"):
        return _error("run", "model_config_required",
                      "a model configuration is required to run")
    source = discover_source(binding, environ=parent, platform_name=platform_name)
    if source.get("kind") in ("missing", "invalid_credential_file"):
        if source.get("kind") == "invalid_credential_file":
            code = "invalid_credential_file"
        elif source.get("reason") == "model_config_required":
            code = "model_config_required"
        else:
            code = "credential_missing"
        return _error("run", code, "the configured credential source is unavailable")
    try:
        child = load_child_environment(binding, source, environ=parent,
                                       platform_name=platform_name)
    except CredentialError as exc:
        return _error("run", exc.code, "the configured credential source could not be loaded")
    try:
        result = subprocess.run(_cli_argv("run", target, binding, fixed_repo, fixed_python),
                                shell=False, env=child)
    except OSError:
        return _error("run", "process_launch_failed", "could not start the ReproAgent CLI")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reproagent-integration-launcher")
    parser.add_argument("--binding-b64", required=True)
    parser.add_argument("action", choices=("check", "inspect", "run"))
    parser.add_argument("target", nargs="?")
    parser.add_argument("--model-config")
    parser.add_argument("--repro-root")
    parser.add_argument("--env-file")
    parser.add_argument("--fixed-repo")
    parser.add_argument("--fixed-python")
    # Accepted for forward compatibility; the wrapper already chose the interpreter.
    parser.add_argument("--tool-python")
    args = parser.parse_args(argv)
    try:
        base = decode_binding(args.binding_b64)
        if args.action == "inspect":
            # Viewing recorded output must survive a moved/deleted model config.
            return launch("inspect", Path(args.target) if args.target else None, base,
                          environ=os.environ, platform_name=_platform_name())
        binding = load_binding(
            tool_python=Path(base["tool_python"]),
            model_config=(Path(args.model_config) if args.model_config else
                          (Path(base["model_config"]) if base["model_config"] else None)),
            repro_root=(Path(args.repro_root) if args.repro_root else
                        (Path(base["repro_root"]) if base["repro_root"] else None)),
            env_file=(Path(args.env_file) if args.env_file else
                      (Path(base["env_file"]) if base["env_file"] else None)))
    except (ValueError, TypeError, OSError):
        return _error(args.action, "invalid_binding", "the integration binding is invalid")
    target = Path(args.target) if args.target else None
    return launch(args.action, target, binding,
                  fixed_repo=Path(args.fixed_repo) if args.fixed_repo else None,
                  fixed_python=args.fixed_python,
                  environ=os.environ, platform_name=_platform_name())


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["launch", "main", "probe_tool_python"]
