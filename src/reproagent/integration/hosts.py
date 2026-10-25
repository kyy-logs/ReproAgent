"""Resolve the allowed user-level install roots for each supported host.

The resolver is read-only.  Detection uses only directory and executable clues;
it never opens an authentication file, and it never creates a directory.  A
directory that a caller is about to create is therefore not evidence that the
host is installed.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Mapping

HOSTS = ("claude-code", "codex")
HOST_CHOICES = ("all",) + HOSTS
CODEX_LAYOUTS = ("standard", "legacy")

_CLAUDE_EXE = "claude"
_CODEX_EXE = "codex"


def _absolute(path: Path | str, name: str) -> Path:
    raw = os.fspath(path)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{name} must be a non-empty absolute path")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return Path(os.path.abspath(candidate))


def _home(home: Path | str) -> Path:
    return _absolute(home, "home")


def _env_directory(environ: Mapping[str, str], name: str) -> Path | None:
    if name not in environ:
        return None
    value = environ[name]
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty absolute path")
    return _absolute(value, name)


def _which(name: str, environ: Mapping[str, str]) -> str | None:
    """Find an executable in the caller's PATH, without reading any file."""
    path = environ.get("PATH", "")
    if not path:
        return None
    if os.name == "nt":
        extensions = (".exe", ".cmd", ".bat", ".com", "")
    else:
        extensions = ("",)
    for directory in path.split(os.pathsep):
        if not directory:
            continue
        base = Path(directory)
        for extension in extensions:
            candidate = base / (name + extension)
            try:
                if candidate.is_file() and os.access(candidate, os.X_OK):
                    return str(candidate)
            except OSError:
                continue
    return None


def _claude_root(home: Path, environ: Mapping[str, str]) -> Path:
    configured = _env_directory(environ, "CLAUDE_CONFIG_DIR")
    return (configured if configured is not None else home / ".claude") / "skills" / "reproagent"


def _codex_standard_root(home: Path) -> Path:
    return home / ".agents" / "skills" / "reproagent"


def _codex_legacy_root(home: Path, environ: Mapping[str, str]) -> Path:
    configured = _env_directory(environ, "CODEX_HOME")
    return (configured if configured is not None else home / ".codex") / "skills" / "reproagent"


def _claude_detection(home: Path, environ: Mapping[str, str]) -> tuple[bool, list[str]]:
    sources = []
    if "CLAUDE_CONFIG_DIR" in environ:
        sources.append("config_dir_env")
    if (home / ".claude").is_dir():
        sources.append("config_dir")
    if _which(_CLAUDE_EXE, environ):
        sources.append("executable")
    return bool(sources), sources


def _codex_detection(home: Path, environ: Mapping[str, str]) -> tuple[bool, list[str]]:
    sources = []
    if "CODEX_HOME" in environ:
        sources.append("config_dir_env")
    if (home / ".codex").is_dir():
        sources.append("config_dir")
    if _which(_CODEX_EXE, environ):
        sources.append("executable")
    return bool(sources), sources


def resolve_hosts(host: str = "all", *, home: Path, environ: Mapping[str, str],
                  codex_layout: str = "standard") -> list[dict]:
    """Return the selected hosts and their exactly resolved skill roots.

    Args:
        host: ``all``, ``claude-code`` or ``codex``.
        home: the user home to resolve against; it need not exist.
        environ: the caller's environment mapping (read only).
        codex_layout: ``standard`` installs ``~/.agents/skills/reproagent``;
            ``legacy`` installs ``$CODEX_HOME/skills/reproagent`` instead.

    Raises:
        ValueError: an unknown host/layout, or a relative/empty host directory
            variable.  No path is created or modified.
    """
    if host not in HOST_CHOICES:
        raise ValueError("host must be one of all, claude-code, codex")
    if codex_layout not in CODEX_LAYOUTS:
        raise ValueError("codex_layout must be standard or legacy")
    home = _home(home)
    selected: list[dict] = []
    if host in ("all", "claude-code"):
        detected, sources = _claude_detection(home, environ)
        selected.append({"host": "claude-code", "layout": "user",
                         "root": _claude_root(home, environ),
                         "host_detected": detected, "detection_sources": sources})
    if host in ("all", "codex"):
        detected, sources = _codex_detection(home, environ)
        root = (_codex_standard_root(home) if codex_layout == "standard"
                else _codex_legacy_root(home, environ))
        selected.append({"host": "codex", "layout": codex_layout,
                         "root": root, "host_detected": detected,
                         "detection_sources": sources})
    unique = []
    seen = set()
    for item in selected:
        key = (item["host"], item["root"])
        if key not in seen:
            seen.add(key)
            unique.append(item)
    return unique


def allowed_roots(*, home: Path, environ: Mapping[str, str]) -> dict[str, tuple[Path, ...]]:
    """The only roots a manifest may name, for the current home and environment."""
    home = _home(home)
    claude = _claude_root(home, environ)
    codex = tuple(dict.fromkeys((_codex_standard_root(home), _codex_legacy_root(home, environ))))
    return {"claude-code": (claude,), "codex": codex}


__all__ = ["HOSTS", "HOST_CHOICES", "CODEX_LAYOUTS", "allowed_roots", "resolve_hosts"]
