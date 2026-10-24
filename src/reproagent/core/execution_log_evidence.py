"""Small, hash-checked original-run log signals; diagnostic data, not source evidence."""
from dataclasses import dataclass
import re

from .serialization import canonical_bytes

LOG_EVIDENCE_BYTES = 2048
MAX_SIGNALS = 4
MAX_SIGNAL_BYTES = 1024
_ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_LEVEL = re.compile(r"\b(?:WARNING|ERROR|FATAL)\s*:", re.IGNORECASE)
_IMPORT = re.compile(r"^(?:E\s+)?(?:ModuleNotFoundError|ImportError|AttributeError):")


@dataclass(frozen=True)
class ExecutionLogEvidence:
    run_id: str
    signals: tuple
    unavailable_streams: tuple = ()
    omitted_signals: int = 0
    log_truncated: bool = False

    def payload(self):
        partial = self.unavailable_streams or self.omitted_signals or self.log_truncated
        return {"kind": "execution_log", "execution_role": "original", "run_id": self.run_id,
                "status": "partial" if partial else "captured",
                "signals": [dict(s) for s in self.signals],
                "unavailable_streams": list(self.unavailable_streams),
                "omitted_signals": self.omitted_signals, "log_truncated": self.log_truncated}


def _priority(line):
    text = _ANSI.sub("", line).strip()
    if _IMPORT.match(text):
        return 0
    level = _LEVEL.search(text)
    if level:
        prefix = text[:level.start()].rstrip()
        if prefix and (not prefix.endswith(":") or any(c in prefix for c in "\"'<>")):
            return None
        return 0 if "failed to import" in text.lower() else 1
    return None


def execution_log_evidence(run, *, read_ref, max_bytes=LOG_EVIDENCE_BYTES):
    """Read only this original run's registered stdout/stderr through a checked reader.

    Selection never establishes a causal blocker or target execution. Nonmatching logs
    add no context. Unreadable logs and whole-line omissions are explicitly distinguished
    from a successful capture; no shortened line pretends to be its complete source.
    """
    if run.execution_role != "original":
        return None
    if type(max_bytes) is not int or max_bytes < 256:
        raise ValueError("invalid execution log evidence budget")
    selected, unavailable, omitted, sequence = [], [], 0, 0
    for stream in ("stdout", "stderr"):
        ref = getattr(run.raw, stream + "_ref")
        if ref.path != f"runs/{run.run_id}/{stream}.log":
            unavailable.append(stream)
            continue
        try:
            text = read_ref(ref)
            if not isinstance(text, str):
                raise ValueError("log reader did not return text")
        except (ValueError, OSError, UnicodeError):
            unavailable.append(stream)
            continue
        for number, line in enumerate(text.splitlines(), 1):
            # RawExecution references historically default to line 1, but resolve()
            # verifies the entire file hash and returns its complete text. Derive the
            # selected line range from that checked body, not the default alias.
            priority = _priority(line)
            if priority is None or any(line == item[2]["text"] for item in selected):
                continue
            sequence += 1
            if len(line.encode("utf-8")) > MAX_SIGNAL_BYTES:
                omitted += 1
                continue
            selected.append((priority, sequence, {
                "path": ref.path, "content_hash": ref.content_hash,
                "start_line": number, "end_line": number, "text": line}))
            selected.sort(key=lambda item: item[:2])
            if len(selected) > MAX_SIGNALS:
                selected.pop()
                omitted += 1
    signals = []
    for _, _, signal in selected:
        proposed = ExecutionLogEvidence(run.run_id, tuple(signals + [signal]),
                                        tuple(unavailable), omitted, run.raw.log_truncated)
        # Reserve space for the eventual omission count even at decimal boundaries.
        size_probe = proposed.payload()
        size_probe["omitted_signals"] = omitted + len(selected)
        size_probe["status"] = "captured"
        if (len(signals) >= MAX_SIGNALS or len(signal["text"].encode("utf-8")) > MAX_SIGNAL_BYTES
                or len(canonical_bytes(size_probe)) > max_bytes):
            omitted += 1
        else:
            signals.append(signal)
    if not signals and not unavailable and not omitted and not run.raw.log_truncated:
        return None
    return ExecutionLogEvidence(run.run_id, tuple(signals), tuple(unavailable),
                                omitted, run.raw.log_truncated)


def log_payloads(evidence):
    result = []
    for item in evidence:
        if not isinstance(item, ExecutionLogEvidence):
            raise ValueError("invalid execution log evidence")
        result.append(item.payload())
    return result


def omit_log_signal(payloads):
    """Shed a whole optional signal, preserving its explicit omission marker."""
    for item in reversed(payloads):
        if item["signals"]:
            item["signals"].pop()
            item["omitted_signals"] += 1
            item["status"] = "partial"
            return True
    return False
