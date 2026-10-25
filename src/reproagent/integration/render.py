"""Render the host Skill, Codex policy and thin platform wrappers.

The rendered files are data, not executable Python: every path is encoded once,
and wrappers only carry the four path fields the launcher needs.  Model
configuration content, base URLs, environment dictionaries and credentials
never enter a rendered file.
"""
from __future__ import annotations

import base64
import json
import os
import shlex
from importlib.resources import files
from pathlib import Path
from typing import Mapping

from .hosts import HOSTS

BINDING_FIELDS = ("tool_python", "model_config", "repro_root", "env_file")
_RENDER_ONLY_FIELDS = ("skill_root", "model_config_status", "api_key_env", "base_url", "model")
_TEMPLATE_ROOT = "resources/integration"


def _absolute_or_none(value, name: str) -> str | None:
    if value is None:
        return None
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{name} must be a non-empty absolute path or null")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{name} must be an absolute path or null")
    return str(Path(os.path.abspath(candidate)))


def encode_binding(binding: Mapping) -> str:
    """Encode exactly the four launcher path fields as base64 JSON.

    Render-only fields such as ``skill_root`` are accepted but not encoded.
    Any other key is refused so a caller cannot smuggle configuration or a
    credential into a generated wrapper.
    """
    unknown = set(binding) - set(BINDING_FIELDS) - set(_RENDER_ONLY_FIELDS)
    if unknown:
        raise ValueError("binding contains fields that must not be encoded")
    if "tool_python" not in binding:
        raise ValueError("binding requires tool_python")
    payload = {
        "tool_python": _absolute_or_none(binding.get("tool_python"), "tool_python"),
        "model_config": _absolute_or_none(binding.get("model_config"), "model_config"),
        "repro_root": _absolute_or_none(binding.get("repro_root"), "repro_root"),
        "env_file": _absolute_or_none(binding.get("env_file"), "env_file"),
    }
    if payload["tool_python"] is None:
        raise ValueError("binding requires tool_python")
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def decode_binding(encoded: str) -> dict:
    """Decode and validate a wrapper binding; unknown fields are refused."""
    try:
        text = base64.b64decode(encoded.encode("ascii"), validate=True).decode("utf-8")
        payload = json.loads(text)
    except (ValueError, UnicodeError) as exc:
        raise ValueError("invalid integration binding") from exc
    if not isinstance(payload, dict) or set(payload) != set(BINDING_FIELDS):
        raise ValueError("invalid integration binding")
    for name in BINDING_FIELDS:
        _absolute_or_none(payload[name], name)
    return payload


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _script_relative_name(platform_name: str) -> str:
    windows = platform_name in ("nt", "windows", "win32")
    return "scripts/reproagent.ps1" if windows else "scripts/reproagent.sh"


def _command_block(script: Path, platform_name: str) -> str:
    windows = platform_name in ("nt", "windows", "win32")
    if windows:
        quoted = _ps_quote(str(script))
        return "\n".join((
            f"powershell.exe -NoProfile -File {quoted} -Check",
            f'powershell.exe -NoProfile -File {quoted} -Inspect "<task-directory>"',
            f'powershell.exe -NoProfile -File {quoted} -TaskConfig "<task.json>"',
        ))
    quoted = shlex.quote(str(script))
    return "\n".join((
        f"{quoted} check",
        f"{quoted} inspect <task-directory>",
        f"{quoted} run <task.json>",
    ))


def _template(relative: str) -> str:
    return files("reproagent").joinpath(_TEMPLATE_ROOT).joinpath(relative).read_text(encoding="utf-8")


def _render(text: str, replacements: Mapping[str, str], *, bom: bool = False) -> bytes:
    for placeholder, value in replacements.items():
        text = text.replace(placeholder, value)
    # Windows PowerShell 5.1 reads .ps1 files without a BOM as the ANSI code page.
    # A UTF-8 BOM keeps non-ASCII checkout paths (for example Chinese directories)
    # intact when the generated launcher is parsed.
    return text.encode("utf-8-sig" if bom else "utf-8")


def render_files(host: str, *, platform_name: str, binding: Mapping) -> dict[str, bytes]:
    """Render the files one host needs, for the current platform only."""
    if host not in HOSTS:
        raise ValueError("unknown integration host")
    if "skill_root" not in binding:
        raise ValueError("rendering requires skill_root")
    skill_root = Path(os.path.abspath(os.fspath(binding["skill_root"])))
    script = skill_root / _script_relative_name(platform_name)
    encoded = encode_binding(binding)
    common = {
        "@@BINDING_B64@@": _ps_quote(encoded),
        "@@TOOL_PYTHON_PS@@": _ps_quote(os.fspath(binding["tool_python"])),
        "@@TOOL_PYTHON_SH@@": shlex.quote(os.fspath(binding["tool_python"])),
        "@@COMMAND_BLOCK@@": _command_block(script, platform_name),
        "@@SCRIPT_PATH@@": (_ps_quote(str(script))
                            if platform_name in ("nt", "windows", "win32")
                            else shlex.quote(str(script))),
        "@@OPTIONAL_RUN_ARGUMENTS@@": (
            '`-ModelConfig "<model.json>"`, `-FixedRepo "<path>"`, '
            '`-FixedPython "<path>"`, `-ToolPython "<path>"`.'
            if platform_name in ("nt", "windows", "win32") else
            '`--model-config <model.json>`, `--fixed-repo <path>`, '
            '`--fixed-python <path>`, `--tool-python <path>`.'),
    }
    rendered: dict[str, bytes] = {}
    skill_template = "claude/SKILL.md.template" if host == "claude-code" else "codex/SKILL.md.template"
    rendered["SKILL.md"] = _render(_template(skill_template), common)
    if host == "codex":
        rendered["agents/openai.yaml"] = _render(
            _template("codex/agents/openai.yaml.template"), common)
    wrapper = "reproagent.ps1.template" if platform_name in ("nt", "windows", "win32") else "reproagent.sh.template"
    script_name = _script_relative_name(platform_name)
    rendered[script_name] = _render(_template(wrapper), common,
                                    bom=script_name.endswith(".ps1"))
    return rendered


__all__ = ["BINDING_FIELDS", "decode_binding", "encode_binding", "render_files"]
