"""The ``reproagent setup`` command: inspect, plan, install and uninstall.

This module is a pure assembly layer over hosts, render, credentials and
manifest.  It never imports the reproduction core, never calls a model and never
prints a credential value.  ``--check`` and ``--dry-run`` are read-only for the
integration tree.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Mapping

from .credentials import (
    CredentialError,
    default_repro_root,
    discover_source,
    load_binding,
)
from .hosts import CODEX_LAYOUTS, HOST_CHOICES, allowed_roots, resolve_hosts
from .launcher import probe_tool_python
from .manifest import (
    TRANSACTION_NAME,
    IncompleteTransaction,
    InstallBusy,
    InstallFailed,
    ManifestError,
    PlanConflict,
    RollbackIncomplete,
    apply_plan,
    classify_file,
    preflight,
    read_manifest,
)
from .render import BINDING_FIELDS, render_files

_OPTION_KEYS = ("host", "codex_layout", "check", "dry_run", "uninstall", "force",
                "tool_python", "model_config", "repro_root", "env_file")


def _platform_name() -> str:
    return "nt" if os.name == "nt" else "posix"


def _emit(payload: dict) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _absolute(path, name: str) -> Path:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{name} must be a non-empty absolute path")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return Path(os.path.abspath(candidate))


def _home(environ: Mapping[str, str], platform_name: str) -> Path:
    names = ("USERPROFILE", "HOME") if platform_name in ("nt", "windows", "win32") else ("HOME", "USERPROFILE")
    for name in names:
        value = environ.get(name)
        if value:
            return _absolute(value, name)
    raise ValueError("cannot determine the user home directory")


def _options_dict(args) -> dict:
    return {key: getattr(args, key, None) for key in _OPTION_KEYS}


def _validate_options(options: Mapping) -> None:
    host = options.get("host", "all")
    layout = options.get("codex_layout", "standard")
    if host not in HOST_CHOICES:
        raise ValueError("host must be all, claude-code or codex")
    if layout not in CODEX_LAYOUTS:
        raise ValueError("codex_layout must be standard or legacy")
    if options.get("check") and options.get("uninstall"):
        raise ValueError("--check and --uninstall are mutually exclusive")
    if options.get("check") and options.get("dry_run"):
        raise ValueError("--dry-run cannot be combined with --check")
    if options.get("force") and (options.get("check") or options.get("uninstall")):
        raise ValueError("--force is only valid for installation")
    if options.get("uninstall") and layout == "legacy":
        raise ValueError("--codex-layout is only valid for installation")
    if host == "claude-code" and layout == "legacy":
        raise ValueError("--codex-layout legacy requires the codex host")


def _tool_python(options: Mapping) -> Path:
    path = _absolute(options.get("tool_python") or sys.executable, "tool_python")
    if not path.is_file():
        raise ValueError("tool_python must be an existing file")
    return path


def _repro_root(options: Mapping) -> Path | None:
    explicit = options.get("repro_root")
    if explicit is not None:
        path = _absolute(explicit, "repro_root")
        if not path.is_dir():
            raise ValueError("repro_root must be an existing directory")
        return path
    import reproagent
    return default_repro_root(Path(reproagent.__file__))


def _model_config(options: Mapping, repro_root: Path | None) -> Path | None:
    explicit = options.get("model_config")
    if explicit is not None:
        path = _absolute(explicit, "model_config")
        if not path.is_file():
            raise ValueError("model_config must be an existing file")
        return path
    if repro_root is not None:
        candidate = repro_root / "examples" / "model.deepseek.json"
        if candidate.is_file():
            return candidate
    return None


def _env_file(options: Mapping) -> Path | None:
    explicit = options.get("env_file")
    if explicit is None:
        return None
    path = _absolute(explicit, "env_file")
    if not path.is_file():
        raise ValueError("env_file must be an existing file")
    return path


def _manifest_path(home: Path) -> Path:
    return home / ".reproagent" / "integrations.json"


def _binding(options: Mapping, repro_root: Path | None) -> dict:
    return load_binding(tool_python=_tool_python(options),
                        model_config=_model_config(options, repro_root),
                        repro_root=repro_root,
                        env_file=_env_file(options))


def _warnings(binding: Mapping, source: Mapping, hosts: list[dict]) -> list[str]:
    values = []
    if binding.get("model_config_status") == "missing":
        values.append("model_config_missing")
    elif binding.get("model_config_status") == "invalid":
        values.append("model_config_invalid")
    if source.get("kind") == "missing":
        values.append("credential_missing")
    elif source.get("kind") == "invalid_credential_file":
        values.append("credential_invalid")
    for host in hosts:
        if not host.get("host_detected"):
            values.append("host_not_detected:" + host["host"])
    return values


def _record_for(manifest: Mapping, host: str, root: str, relative: str) -> dict | None:
    for entry in manifest["roots"]:
        if entry["host"] != host or os.path.normcase(entry["root"]) != os.path.normcase(root):
            continue
        for record in entry["files"]:
            if record["path"] == relative:
                return record
    return None


def build_plan(options: Mapping, *, environ: Mapping[str, str], platform_name: str) -> dict:
    """Build an install or uninstall plan without writing anything."""
    _validate_options(options)
    home = _home(environ, platform_name)
    tool = _tool_python(options)
    probe = probe_tool_python(tool)
    if not probe.get("available"):
        raise ValueError("tool_python cannot import ReproAgent")
    repro_root = _repro_root(options)
    binding = _binding(options, repro_root)
    hosts = resolve_hosts(options.get("host", "all"), home=home, environ=environ,
                          codex_layout=options.get("codex_layout", "standard"))
    allowed = allowed_roots(home=home, environ=environ)
    manifest_path = _manifest_path(home)
    manifest = read_manifest(manifest_path, allowed_roots=allowed)
    source = discover_source(binding, environ=environ, platform_name=platform_name)
    warnings = _warnings(binding, source, hosts)
    action = "uninstall" if options.get("uninstall") else "install"
    operations = []
    if action == "uninstall":
        selected = {item["host"] for item in hosts}
        for entry in manifest["roots"]:
            if entry["host"] not in selected:
                continue
            for record in entry["files"]:
                operations.append({
                    "op": "remove", "host": entry["host"], "layout": entry["layout"],
                    "root": entry["root"], "relative_path": record["path"],
                    "path": record["absolute"], "expected_sha256": record["sha256"],
                    "desired_sha256": None, "content": None,
                })
    else:
        for host in hosts:
            rendered = render_files(host["host"], platform_name=platform_name,
                                    binding={**binding, "skill_root": str(host["root"])})
            for relative, content in sorted(rendered.items()):
                operations.append({
                    "op": "create", "host": host["host"], "layout": host["layout"],
                    "root": str(host["root"]), "relative_path": relative,
                    "path": str(host["root"] / relative), "expected_sha256": None,
                    "desired_sha256": hashlib.sha256(content).hexdigest(),
                    "content": content,
                })
    safe_hosts = [{"host": item["host"], "layout": item["layout"],
                   "root": str(item["root"]), "host_detected": item["host_detected"],
                   "detection_sources": item["detection_sources"]}
                  for item in hosts]
    plan = {"action": action, "force": bool(options.get("force")),
            "hosts": safe_hosts, "operations": operations, "warnings": warnings,
            "_manifest_path": manifest_path, "_allowed_roots": allowed,
            "_binding": {name: binding[name] for name in BINDING_FIELDS}}
    decisions = preflight(plan, manifest_path=manifest_path, allowed_roots=allowed)
    plan["preflight"] = decisions
    plan["conflicts"] = [{"path": item["path"], "state": item["state"]}
                         for item in decisions if item["decision"] == "conflict"]
    return plan


def _entry_status(statuses: list[str]) -> str:
    for value in ("conflict", "modified", "missing", "unmanaged_matching"):
        if value in statuses:
            return value
    return "installed"


def _check_binding(options: Mapping, entry: Mapping | None) -> dict:
    """Inspect installed paths without requiring callers to repeat setup flags."""
    if not entry or "binding" not in entry:
        return _binding(options, _repro_root(options))
    paths = {name: (options.get(name) if options.get(name) is not None else entry["binding"][name])
             for name in BINDING_FIELDS}
    model = paths["model_config"]
    missing = model is not None and not Path(model).is_file()
    binding = load_binding(tool_python=Path(paths["tool_python"]),
                           model_config=None if missing or model is None else Path(model),
                           repro_root=Path(paths["repro_root"]) if paths["repro_root"] else None,
                           env_file=Path(paths["env_file"]) if paths["env_file"] else None)
    if missing:
        # Preserve the installed path for template comparison; readiness is separate.
        binding["model_config"] = str(_absolute(model, "model_config"))
        binding["model_config_status"] = "missing"
    return binding


def check_setup(options: Mapping, *, environ: Mapping[str, str], platform_name: str) -> dict:
    """Report entry, interpreter, configuration and credential readiness."""
    _validate_options(options)
    home = _home(environ, platform_name)
    hosts = resolve_hosts(options.get("host", "all"), home=home, environ=environ,
                          codex_layout=options.get("codex_layout", "standard"))
    allowed = allowed_roots(home=home, environ=environ)
    manifest_path = _manifest_path(home)
    manifest = read_manifest(manifest_path, allowed_roots=allowed)
    transaction = manifest_path.with_name(TRANSACTION_NAME).exists()
    result = {"action": "check", "ready": False, "home": str(home),
              "transaction_incomplete": transaction,
              "warnings": [], "hosts": {}}
    all_ready = bool(hosts)
    for host in hosts:
        entry = next((item for item in manifest["roots"]
                      if item["host"] == host["host"]
                      and os.path.normcase(item["root"]) == os.path.normcase(str(host["root"]))), None)
        binding = _check_binding(options, entry)
        probe = probe_tool_python(Path(binding["tool_python"]))
        source = discover_source(binding, environ=environ, platform_name=platform_name)
        run_configured = (binding.get("model_config_status") == "ready"
                          and source.get("kind") not in ("missing", "invalid_credential_file"))
        result["warnings"].extend(_warnings(binding, source, [host]))
        rendered = render_files(host["host"], platform_name=platform_name,
                                binding={**binding, "skill_root": str(host["root"])})
        statuses = []
        files = []
        permission_ready = True
        windows = platform_name in ("nt", "windows", "win32")
        for relative, content in sorted(rendered.items()):
            path = host["root"] / relative
            record = _record_for(manifest, host["host"], str(host["root"]), relative)
            state = classify_file(path, content, record)
            template_current = None
            if state == "owned_matching":
                template_current = record["sha256"] == hashlib.sha256(content).hexdigest()
                state = "installed"
            elif state == "conflict" and record is not None:
                state = "modified"
            executable = None
            if relative.endswith(".sh") and not windows and path.exists():
                executable = os.access(path, os.X_OK)
                permission_ready = permission_ready and bool(executable)
            statuses.append(state)
            files.append({"path": relative, "status": state, "executable": executable,
                          "template_current": template_current})
        entry_status = _entry_status(statuses)
        registered = [entry["root"] for entry in manifest["roots"]
                      if entry["host"] == host["host"]]
        ready = (entry_status in ("installed", "unmanaged_matching") and permission_ready
                 and bool(probe.get("available")) and run_configured and not transaction)
        all_ready = all_ready and ready
        result["hosts"][host["host"]] = {
            "host_detected": host["host_detected"],
            "detection_sources": host["detection_sources"],
            "layout": host["layout"], "root": str(host["root"]),
            "registered_roots": registered, "entry_status": entry_status,
            "permission_ready": permission_ready,
            "files": files, "launcher_importable": bool(probe.get("available")),
            "model_config_status": binding.get("model_config_status"),
            "credential_source": source, "run_configured": run_configured,
            "ready": ready,
        }
    result["ready"] = all_ready
    values = list(result["hosts"].values())
    result["launcher_importable"] = bool(values) and all(item["launcher_importable"] for item in values)
    result["run_configured"] = bool(values) and all(item["run_configured"] for item in values)
    statuses = {item["model_config_status"] for item in values}
    result["model_config_status"] = next(iter(statuses)) if len(statuses) == 1 else "mixed"
    sources = [item["credential_source"] for item in values]
    result["credential_source"] = (sources[0] if sources and all(item == sources[0] for item in sources)
                                   else {"kind": "mixed"})
    result["warnings"] = list(dict.fromkeys(result["warnings"]))
    return result


def _safe_plan(plan: Mapping) -> dict:
    operations = []
    for operation in plan["operations"]:
        operations.append({key: value for key, value in operation.items()
                           if key not in ("content",) and not key.startswith("_")})
    decisions = [{key: value for key, value in item.items()
                  if key not in ("content", "record") and not key.startswith("_")}
                 for item in plan.get("preflight", [])]
    return {"action": plan["action"], "force": plan["force"],
            "hosts": plan["hosts"], "operations": operations,
            "preflight": decisions, "conflicts": plan.get("conflicts", []),
            "warnings": plan["warnings"]}


def _failure(action: str, code: str, **fields) -> int:
    payload = {"action": action, "code": code, "ready": False}
    payload.update(fields)
    _emit(payload)
    return 2


def run_setup(args) -> int:
    """Entry point for ``reproagent setup``; stdout is one safe JSON line."""
    options = _options_dict(args)
    action = "uninstall" if options.get("uninstall") else "install"
    try:
        if options.get("check"):
            result = check_setup(options, environ=os.environ, platform_name=_platform_name())
            _emit(result)
            return 0 if result["ready"] else 1
        plan = build_plan(options, environ=os.environ, platform_name=_platform_name())
        if options.get("dry_run"):
            _emit(_safe_plan(plan))
            return 0
        result = apply_plan(plan, manifest_path=plan["_manifest_path"],
                            allowed_roots=plan["_allowed_roots"])
        result["warnings"] = plan["warnings"]
        _emit(result)
        return 0
    except PlanConflict as exc:
        return _failure(action, "conflict", conflicts=exc.conflicts)
    except IncompleteTransaction as exc:
        return _failure(action, "incomplete_transaction", transaction=str(exc.path))
    except InstallBusy:
        return _failure(action, "install_busy")
    except RollbackIncomplete as exc:
        return _failure(action, "rollback_incomplete", transaction=str(exc.path))
    except InstallFailed as exc:
        return _failure(action, exc.code)
    except ManifestError as exc:
        return _failure(action, "manifest_invalid", reason=exc.code)
    except (ValueError, TypeError, OSError, CredentialError):
        return _failure(action, "invalid_arguments")


__all__ = ["build_plan", "check_setup", "run_setup"]
