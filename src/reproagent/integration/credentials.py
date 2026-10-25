"""Configuration binding and credential-source rules shared by check and run.

The installer and the launcher must select the same source, so both call this
module.  Nothing here returns, prints or persists a credential value: discovery
returns a source descriptor, and only ``load_child_environment`` resolves a value
for the child process.

A high-priority source that exists but cannot be loaded is a controlled failure.
It never falls back to another variable or account.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ is required
    tomllib = None

DEFAULT_KEY_ENV = "REPROAGENT_API_KEY"
KEY_ENV_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROJECT_NAME = "reproagent-local"
_ENV_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")
_PLATFORM_WINDOWS = ("nt", "windows", "win32")

SOURCE_FIELDS = ("kind", "name", "path", "present", "loadability", "authenticated")


class CredentialError(Exception):
    """A controlled credential failure with a stable code and no secret text."""

    def __init__(self, code: str, reason: str = "", message: str = "credential source unavailable"):
        super().__init__(message)
        self.code = code
        self.reason = reason


def _absolute(value, name: str) -> str:
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw:
        raise ValueError(f"{name} must be a non-empty absolute path")
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return str(Path(os.path.abspath(candidate)))


def _absolute_or_none(value, name: str) -> str | None:
    if value is None:
        return None
    return _absolute(value, name)


def default_repro_root(module_file: Path) -> Path | None:
    """The source checkout root, or ``None`` for an installed wheel.

    Only a checkout with ``src/reproagent/cli.py`` and a pyproject naming this
    product is accepted.  A site-packages directory is never treated as a
    project root, so ``.local`` and ``.env`` are not guessed next to it.
    """
    try:
        module_file = Path(module_file)
        package = module_file.parent
        if package.name != "reproagent":
            return None
        root = package.parent.parent
        if not (root / "src" / "reproagent" / "cli.py").is_file():
            return None
        pyproject = root / "pyproject.toml"
        if not pyproject.is_file() or tomllib is None:
            return None
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        if data.get("project", {}).get("name") != PROJECT_NAME:
            return None
        return root
    except (OSError, UnicodeError, ValueError):
        return None


def load_binding(*, tool_python: Path, model_config: Path | None,
                 repro_root: Path | None, env_file: Path | None) -> dict:
    """Bind the immutable paths and the safe model fields used by a run.

    The model configuration is parsed for ``base_url``, ``model`` and
    ``api_key_env`` only.  No configuration body, credential or environment
    mapping is returned.
    """
    tool = _absolute(tool_python, "tool_python")
    model_path = _absolute_or_none(model_config, "model_config")
    root = _absolute_or_none(repro_root, "repro_root")
    env_path = _absolute_or_none(env_file, "env_file")
    status = "missing"
    api_key_env = None
    base_url = None
    model = None
    if model_path is not None:
        if not Path(model_path).is_file():
            raise ValueError("model_config must be an existing file")
        try:
            data = json.loads(Path(model_path).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            status = "invalid"
        else:
            if isinstance(data, dict):
                candidate_base = data.get("base_url")
                candidate_model = data.get("model")
                candidate_env = data.get("api_key_env", DEFAULT_KEY_ENV)
                if (isinstance(candidate_base, str) and candidate_base
                        and isinstance(candidate_model, str) and candidate_model
                        and isinstance(candidate_env, str)
                        and KEY_ENV_PATTERN.match(candidate_env)):
                    status = "ready"
                    api_key_env, base_url, model = candidate_env, candidate_base, candidate_model
            else:
                status = "invalid"
    return {
        "tool_python": tool,
        "model_config": model_path,
        "repro_root": root,
        "env_file": env_path,
        "model_config_status": status,
        "api_key_env": api_key_env,
        "base_url": base_url,
        "model": model,
    }


def _source(kind: str, name: str | None, *, path: str | None = None,
            present: bool = True, reason: str | None = None) -> dict:
    value = {"kind": kind, "name": name, "path": path, "present": present,
             "loadability": "unknown", "authenticated": False}
    if reason is not None:
        value["reason"] = reason
    return value


def _dpapi_allowed(binding: Mapping, name: str, platform_name: str) -> bool:
    if platform_name not in _PLATFORM_WINDOWS:
        return False
    if name != "DEEPSEEK_API_KEY":
        return False
    base = binding.get("base_url")
    if not isinstance(base, str) or not base:
        return False
    try:
        parsed = urlsplit(base)
    except ValueError:
        return False
    return parsed.scheme == "https" and (parsed.hostname or "").lower() == "api.deepseek.com"


def _parse_dotenv_value(raw: str) -> str:
    if raw[:1] in ("'", '"'):
        quote = raw[0]
        if len(raw) < 2 or raw[-1] != quote:
            raise CredentialError("invalid_credential_file", "unclosed_quote")
        return raw[1:-1]
    return raw


def _read_dotenv(path: Path, name: str) -> str | None:
    """Read one exact key from a dotenv file without sourcing or interpolating it."""
    path = Path(path)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise CredentialError("invalid_credential_file", "unreadable") from exc
    found = None
    seen = False
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ENV_LINE.match(line)
        if match is None:
            continue
        key, raw = match.groups()
        if key != name:
            continue
        if seen:
            raise CredentialError("invalid_credential_file", "duplicate_key")
        seen = True
        value = _parse_dotenv_value(raw)
        if value:
            found = value
    return found


def discover_source(binding: Mapping, *, environ: Mapping[str, str],
                    platform_name: str) -> dict:
    """Describe the source check would use, without reading its value for output."""
    name = binding.get("api_key_env")
    if binding.get("model_config_status") != "ready" or not name:
        return _source("missing", name, present=False, reason="model_config_required")
    value = environ.get(name)
    if isinstance(value, str) and value:
        return _source("environment", name)
    if _dpapi_allowed(binding, name, platform_name):
        root = binding.get("repro_root")
        if root:
            key_path = Path(root) / ".local" / "deepseek.key"
            if key_path.is_file():
                # Existence is all setup/check may claim; decryption is a run-time
                # operation and may still fail for another user or a broken file.
                return _source("dpapi-file", name, path=str(key_path))
    candidates = []
    if binding.get("env_file"):
        candidates.append(Path(binding["env_file"]))
    if binding.get("repro_root"):
        candidates.append(Path(binding["repro_root"]) / ".env")
    unique = []
    seen_paths = set()
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key not in seen_paths:
            seen_paths.add(key)
            unique.append(path)
    for path in unique:
        try:
            value = _read_dotenv(path, name)
        except CredentialError as exc:
            return _source("invalid_credential_file", name, path=str(path), reason=exc.reason)
        if value:
            return _source("dotenv", name, path=str(path))
    return _source("missing", name, present=False, reason="no_source")


def load_child_environment(binding: Mapping, source: Mapping, *, environ: Mapping[str, str],
                           platform_name: str) -> dict[str, str]:
    """Return a child environment with only the selected variable injected.

    The caller's mapping is not modified.  A selected source that cannot be
    loaded raises :class:`CredentialError`; no lower-priority source is tried.
    """
    name = source.get("name") or binding.get("api_key_env")
    if not name:
        raise CredentialError("credential_missing", "model_config_required")
    kind = source.get("kind")
    value = None
    if kind == "environment":
        candidate = environ.get(name)
        if isinstance(candidate, str) and candidate:
            value = candidate
    elif kind == "dpapi-file":
        path = source.get("path")
        if not path:
            raise CredentialError("credential_load_failed", "missing_path")
        value = decrypt_dpapi_file(Path(path))
    elif kind == "dotenv":
        path = source.get("path")
        if path:
            value = _read_dotenv(Path(path), name)
    elif kind == "invalid_credential_file":
        raise CredentialError("invalid_credential_file", source.get("reason", ""))
    else:
        raise CredentialError("credential_missing", source.get("reason", "no_source"))
    if not value:
        raise CredentialError("credential_load_failed", "empty_value")
    child = dict(environ)
    child[name] = value
    return child


def decrypt_dpapi_file(path: Path) -> str:
    """Decrypt the CurrentUser DPAPI hex produced by ``ConvertFrom-SecureString``.

    Only the default SecureString format is supported.  Any other format or a
    failure is a controlled error; the file content never appears in the error.
    """
    if os.name != "nt":
        raise CredentialError("dpapi_unavailable", "windows_only")
    try:
        text = Path(path).read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        raise CredentialError("credential_load_failed", "unreadable") from exc
    try:
        blob = bytes.fromhex(text)
    except ValueError as exc:
        raise CredentialError("credential_load_failed", "unsupported_format") from exc
    if not blob:
        raise CredentialError("credential_load_failed", "empty_file")

    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD),
                    ("pbData", ctypes.POINTER(ctypes.c_char))]

    buffer = ctypes.create_string_buffer(blob, len(blob))
    input_blob = DATA_BLOB(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    output_blob = DATA_BLOB()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DATA_BLOB), ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(DATA_BLOB)]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    if not crypt32.CryptUnprotectData(ctypes.byref(input_blob), None, None, None,
                                      None, 0, ctypes.byref(output_blob)):
        raise CredentialError("credential_load_failed", "dpapi_failed")
    try:
        raw = ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)
    try:
        value = raw.decode("utf-16-le").rstrip("\x00")
    except UnicodeDecodeError as exc:
        raise CredentialError("credential_load_failed", "invalid_text") from exc
    if not value:
        raise CredentialError("credential_load_failed", "empty_value")
    return value


__all__ = ["CredentialError", "DEFAULT_KEY_ENV", "SOURCE_FIELDS", "decrypt_dpapi_file",
           "default_repro_root", "discover_source", "load_binding", "load_child_environment"]
