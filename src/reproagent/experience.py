"""Bounded, advisory experience records. Only system lifecycle code may write the library."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .core.budget import BudgetStopped
from .core.models import EvidenceRef
from .core.serialization import canonical_bytes, canonical_hash, parse_json, bytes_hash
from .paths import directory_path, workspace_path, is_within
from .store import atomic_write

LIBRARY_BYTES = 1024 * 1024
CARD_BYTES = 2048
VISIBLE_BYTES = 2048
INPUT_BYTES = 8192
CATEGORIES = ("framework", "model", "workflow")


@dataclass(frozen=True, slots=True)
class ExperienceCard:
    id: str
    category: str
    tags: tuple[str, ...]
    summary: str
    detail: str
    source_task_id: str
    evidence_refs: tuple[EvidenceRef, ...]


@dataclass(frozen=True, slots=True)
class ExperienceSnapshot:
    state: str
    content_hash: str
    cards: tuple[ExperienceCard, ...] = ()


@dataclass(frozen=True, slots=True)
class LearningResult:
    code: str
    experience_id: str = ""
    duration: float = 0
    http_attempts: int = 0
    usage: dict = field(default_factory=dict)
    known_cost_subtotal: float = 0
    unknown_cost_attempts: int = 0


def _normalized(text):
    return " ".join(text.casefold().split())


def _identifier(category, tags, summary):
    return "exp_" + canonical_hash({"category": category,
        "tags": sorted(set(_normalized(tag) for tag in tags)), "summary": _normalized(summary)})


def _card(data):
    fields = {"id", "category", "tags", "summary", "detail", "source_task_id", "evidence_refs"}
    if not isinstance(data, dict) or set(data) != fields:
        raise ValueError("invalid experience fields")
    for name in ("id", "category", "summary", "detail", "source_task_id"):
        if type(data[name]) is not str or not data[name].strip():
            raise ValueError("invalid experience text")
    if data["category"] not in CATEGORIES:
        raise ValueError("unknown experience category")
    tags = data["tags"]
    if not isinstance(tags, (list, tuple)) or len(tags) > 5 or any(
        type(tag) is not str or not tag.strip() or len(tag) > 64 for tag in tags):
        raise ValueError("invalid experience tags")
    refs = data["evidence_refs"]
    if not isinstance(refs, (list, tuple)) or not 1 <= len(refs) <= 3:
        raise ValueError("invalid experience references")
    checked = []
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"path", "content_hash", "start_line", "end_line"}:
            raise ValueError("invalid experience reference fields")
        if ref["path"] != "learning/evidence.jsonl" or type(ref["content_hash"]) is not str or not re.fullmatch(r"[0-9a-f]{64}", ref["content_hash"]):
            raise ValueError("invalid experience reference")
        if type(ref["start_line"]) is not int or type(ref["end_line"]) is not int or not 1 <= ref["start_line"] <= ref["end_line"]:
            raise ValueError("invalid experience line range")
        checked.append(EvidenceRef(**ref))
    if len(set(checked)) != len(checked):
        raise ValueError("duplicate experience references")
    if data["id"] != _identifier(data["category"], tags, data["summary"]):
        raise ValueError("experience id does not match normalized content")
    card = ExperienceCard(data["id"], data["category"], tuple(sorted(set(_normalized(t) for t in tags))),
        data["summary"], data["detail"], data["source_task_id"], tuple(checked))
    if len(canonical_bytes(asdict(card))) > CARD_BYTES:
        raise ValueError("experience card exceeds byte limit")
    return card


def _read_library(path):
    path = workspace_path(path)
    try:
        with path.open("rb") as handle:
            content = handle.read(LIBRARY_BYTES + 1)
    except FileNotFoundError:
        return b"", ()
    if len(content) > LIBRARY_BYTES:
        raise ValueError("experience library exceeds byte limit")
    root = parse_json(content.decode("utf-8"))
    if set(root) != {"schema_version", "items"} or type(root["schema_version"]) is not int or root["schema_version"] != 1 or not isinstance(root["items"], list):
        raise ValueError("invalid experience library schema")
    cards = tuple(_card(item) for item in root["items"])
    if len({card.id for card in cards}) != len(cards):
        raise ValueError("duplicate experience identifiers")
    return content, cards


def load_experience_snapshot(path: Path, *, secrets=()) -> ExperienceSnapshot:
    try:
        content, cards = _read_library(path)
        if any(secret and secret in canonical_bytes(asdict(card)).decode("utf-8")
               for card in cards for secret in secrets):
            raise ValueError("library contains a known credential")
        return ExperienceSnapshot("ready" if content else "missing", bytes_hash(content), cards)
    except (OSError, ValueError, UnicodeError, TypeError):
        return ExperienceSnapshot("store_error", "")


def validate_experience_path(path, request, fixed):
    path = Path(path).resolve()
    lock = path.with_name(path.name + ".lock").resolve()
    protected = [request.repo, request.output_dir]
    if fixed is not None:
        protected.append(fixed.repo)
    if any(is_within(candidate, root) for candidate in (path, lock) for root in protected):
        raise ValueError("experience library must be outside repository, task output and fixed repository")
    return path


class _Busy(Exception):
    pass


@contextmanager
def _library_lock(path):
    lock = workspace_path(path.with_name(path.name + ".lock"))
    directory_path(lock.parent).mkdir(parents=True, exist_ok=True)
    with lock.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise _Busy() from exc
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def _check(context):
    if context is not None:
        context.budget.check()
        if context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")


def append_experience(path: Path, card: ExperienceCard, context) -> str:
    try:
        checked = _card(asdict(card))
    except (ValueError, TypeError):
        return "invalid"
    try:
        _check(context)
        with _library_lock(Path(path)):
            _, cards = _read_library(path)
            if any(old.id == checked.id for old in cards):
                return "duplicate"
            content = canonical_bytes({"schema_version": 1, "items": [asdict(c) for c in (*cards, checked)]})
            if len(content) > LIBRARY_BYTES:
                return "store_error"
            _check(context)
            atomic_write(path, content)
        return "written"
    except _Busy:
        return "busy"
    except BudgetStopped as exc:
        return "skipped_cancelled" if exc.reason == "CANCELLED" else "timeout"
    except (OSError, ValueError, UnicodeError, TypeError):
        return "store_error"

def _keywords(text):
    words = set(re.findall(r"[a-z0-9_.-]+", text.casefold()))
    for part in re.findall(r"[\u4e00-\u9fff]+", text):
        words.update(part[i:i + 2] for i in range(len(part) - 1))
    return words


class ExperienceView:
    """One immutable library snapshot and one task's bounded, advisory read allowance."""
    def __init__(self, snapshot: ExperienceSnapshot, *, secrets=()):
        self.snapshot = snapshot
        self.secrets = tuple(s for s in secrets if s)
        self._selected = None
        self._read_ids = ()

    @property
    def read_ids(self):
        return self._read_ids

    def select(self, issue_text, target_modules):
        from .core.models import ExperienceSummary
        if self._selected is not None:
            return tuple(ExperienceSummary(c.id, c.summary) for c in self._selected)
        query = _normalized(issue_text + " " + " ".join(target_modules))
        words = _keywords(query)
        scored = []
        for card in self.snapshot.cards:
            score = sum(_normalized(tag) in query for tag in card.tags) + len(_keywords(card.summary) & words)
            if score > 0:
                scored.append((-score, card.id, card))
        selected = []
        for _, _, card in sorted(scored):
            draft = [{"id": c.id, "summary": c.summary} for c in (*selected, card)]
            if len(canonical_bytes(draft)) <= VISIBLE_BYTES:
                selected.append(card)
            if len(selected) == 3:
                break
        self._selected = tuple(selected)
        return tuple(ExperienceSummary(c.id, c.summary) for c in selected)

    def restrict_summaries(self, ids):
        self._selected = tuple(c for c in (self._selected or ()) if c.id in ids)

    def read(self, identifier, *, max_bytes=VISIBLE_BYTES):
        if self._read_ids:
            raise ValueError("only one successful experience detail read is allowed per task")
        card = next((c for c in (self._selected or ()) if c.id == identifier), None)
        if card is None:
            raise ValueError("experience id was not displayed in this task")
        data = {"id": card.id, "summary": card.summary, "detail": card.detail}
        for key in ("summary", "detail"):
            for secret in self.secrets:
                data[key] = data[key].replace(secret, "[REDACTED]")
        summaries = [{"id": c.id, "summary": c.summary} for c in self._selected]
        if len(canonical_bytes(data)) > max_bytes or len(canonical_bytes(summaries)) + len(canonical_bytes(data)) > VISIBLE_BYTES:
            raise ValueError("experience detail exceeds shared visible budget")
        self._read_ids = (identifier,)
        return data
