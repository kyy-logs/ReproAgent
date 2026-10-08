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

@dataclass(frozen=True, slots=True)
class LearningInput:
    payload: dict
    evidence_map: dict[str, EvidenceRef]
    source_task_id: str
    event_cutoff: int


def _redact(text, secrets):
    import json
    for secret in secrets:
        if secret:
            forms = {secret, json.dumps(secret, ensure_ascii=False)[1:-1], json.dumps(secret, ensure_ascii=True)[1:-1]}
            for form in sorted(forms, key=len, reverse=True):
                text = text.replace(form, "[REDACTED]")
    return text


def build_learning_input(store, result, context, *, secrets=()):
    """Freeze explicitly selected original evidence, never a recursive task/report summary."""
    from .core.models import TaskState
    from .core.review_context import build_review_context, ReviewContextTooLarge
    from .store import safe_child
    from .workspace import candidate_hash
    try:
        _check(context)
        if result.status == TaskState.CANCELLED:
            return None
        events, errors = store.read_events()
        if errors:
            return None
        entries = []
        def add(kind, text, origins=()):
            entries.append({"id": "E" + str(len(entries) + 1), "kind": kind,
                "text": _redact(text, secrets), "origin_refs": [asdict(ref) for ref in origins]})
        issue = safe_child(store.root, "input/issue.md")
        if issue.is_file():
            content = issue.read_bytes()
            if len(content) > INPUT_BYTES:
                return None
            add("issue", content.decode("utf-8"), (EvidenceRef("input/issue.md", bytes_hash(content),
                1, max(1, len(content.splitlines()))),))
        # Order executions by the immutable run records referenced by original run events.
        run_ids = []
        for event in events:
            if event.kind == "run.completed":
                for ref in event.refs:
                    parts = ref.split("/")
                    if len(parts) == 3 and parts[0] == "runs" and parts[-1] == "execution.json":
                        run_ids.append(parts[1])
        original = []
        for identity in run_ids:
            run = store.load_record("runs", identity)
            if run.execution_role == "original":
                original.append(run)
        if original:
            run = original[-1]
            candidate = store.load_record("candidates", run.candidate_id)
            contract = store.load_contract(candidate.contract_id, candidate.contract_version)
            snapshot = store.load_record("snapshots", run.snapshot_id)
            if (candidate.snapshot_id != run.snapshot_id or candidate.manifest_hash != run.manifest_hash
                or candidate_hash(candidate) != candidate.manifest_hash
                or run.contract_id != contract.contract_id or run.contract_version != contract.version
                or not is_within(snapshot.root, store.root)):
                return None
            registered = {str(workspace_path(snapshot.root / f.path).resolve()): f.content_hash for f in snapshot.files}
            failure_prefix = "runs/" + run.run_id + "/"
            for source in contract.sources:
                source_path = safe_child(store.root, source.path)
                if source.path != "input/issue.md" and registered.get(str(source_path.resolve())) != source.content_hash:
                    return None
                if any(secret and secret in source.path for secret in secrets):
                    return None
            def read_ref(ref):
                path = safe_child(store.root, ref.path)
                allowed = (ref.path == "input/issue.md" or ref.path.startswith(failure_prefix)
                           or registered.get(str(path.resolve())) == ref.content_hash)
                data = path.read_bytes()
                if not allowed or bytes_hash(data) != ref.content_hash:
                    raise ValueError("learning source is unavailable or not original")
                return data.decode("utf-8")
            def read_file(entry):
                path = safe_child(candidate.storage_root, entry.path)
                if not is_within(path, store.root):
                    raise ValueError("candidate source escapes task")
                data = path.read_bytes()
                if bytes_hash(data) != entry.content_hash:
                    raise ValueError("candidate source changed")
                return data
            review = build_review_context(contract, candidate, run, read_ref=read_ref,
                read_file=read_file, max_bytes=INPUT_BYTES)
            execution_path = store.record_path("runs", run.run_id)
            execution_ref = EvidenceRef("runs/" + run.run_id + "/execution.json",
                bytes_hash(execution_path.read_bytes()))
            add("execution", canonical_bytes(review.payload["observation"]).decode("utf-8"), (execution_ref,))
            add("candidate", canonical_bytes(review.payload["candidate"]).decode("utf-8"))
            add("expectation", canonical_bytes({"contract": review.payload["contract"],
                "sources": review.payload["expectation_sources"]}).decode("utf-8"), contract.sources)
            add("failure", canonical_bytes(review.payload["failure_evidence"]).decode("utf-8"), run.observation.failure_refs)
        controlled = {"UNKNOWN_TOOL", "MULTIPLE_TOOL_CALLS", "INVALID_TOOL_INPUT", "OUTPUT_TRUNCATED",
            "OUTPUT_FILTERED", "EMPTY_OUTPUT", "RESPONSE_TOO_LARGE", "INVALID_PROTOCOL",
            "MODEL_OUTPUT_ERROR", "MODEL_PROTOCOL_ERROR", "EXHAUSTED", "DENIED", "RESERVED_FOR_PUBLISHING"}
        notes = [{"seq": event.seq, "kind": event.kind, "code": event.payload.get("result_code")}
            for event in events if event.kind in ("exploration.protocol_error", "exploration.action", "exploration.phase_error")
            and event.payload.get("result_code") in controlled]
        if notes:
            add("tool_error", canonical_bytes(notes).decode("utf-8"))
        steps = sum(e.kind == "exploration.step" for e in events)
        reads = sum(e.kind == "exploration.action" and e.payload.get("action") in ("Read", "Grep", "Glob")
                    and e.payload.get("result_code") == "ALLOWED" for e in events)
        if steps:
            add("workflow", canonical_bytes({"steps": steps, "read_calls": reads,
                "original_executions": len(original), "task_status": result.status.value}).decode("utf-8"))
        if not original and not notes and not steps:
            return None
        from .core.protocol import learning_schema
        payload = {"task_status": result.status.value, "evidence": [
            {k: e[k] for k in ("id", "kind", "text")} for e in entries], "response_schema": learning_schema()}
        if len(canonical_bytes(payload)) > INPUT_BYTES:
            return None
        path = safe_child(store.root, "learning/evidence.jsonl")
        if path.exists():
            return None
        _check(context)
        content = b"\n".join(canonical_bytes(entry) for entry in entries) + b"\n"
        atomic_write(path, content)
        digest = bytes_hash(content)
        refs = {e["id"]: EvidenceRef("learning/evidence.jsonl", digest, i + 1, i + 1)
                for i, e in enumerate(entries)}
        source_id = result.task_id + "@" + bytes_hash(str(store.root.resolve()).encode("utf-8"))
        return LearningInput(payload, refs, source_id, events[-1].seq if events else -1)
    except (OSError, ValueError, TypeError, UnicodeError, ReviewContextTooLarge):
        return None


def validate_experience(response, material, store, *, secrets=()):
    from .store import safe_child
    if not isinstance(response, dict) or set(response) != {"experience"}:
        raise ValueError("invalid learning response fields")
    proposed = response["experience"]
    if proposed is None:
        return None
    fields = {"category", "tags", "summary", "detail", "evidence_ids"}
    if not isinstance(proposed, dict) or set(proposed) != fields:
        raise ValueError("invalid experience proposal fields")
    ids = proposed["evidence_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(
        type(i) is not str or i not in material.evidence_map for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("invalid learning evidence ids")
    refs = []
    for identity in ids:
        ref = material.evidence_map[identity]
        content = safe_child(store.root, ref.path).read_bytes()
        lines = content.splitlines()
        if bytes_hash(content) != ref.content_hash or not 1 <= ref.start_line == ref.end_line <= len(lines):
            raise ValueError("learning evidence hash or lines changed")
        if parse_json(lines[ref.start_line - 1].decode("utf-8")).get("id") != identity:
            raise ValueError("learning evidence identity changed")
        refs.append(ref)
    summary = proposed["summary"]
    detail = proposed["detail"]
    tags = proposed["tags"]
    if type(summary) is not str or type(detail) is not str or not isinstance(tags, list) or any(type(t) is not str for t in tags):
        raise ValueError("invalid learning text")
    summary, detail = _redact(summary, secrets), _redact(detail, secrets)
    tags = [_redact(tag, secrets) for tag in tags]
    category = proposed["category"]
    data = {"id": _identifier(category, tags, summary), "category": category, "tags": tags,
        "summary": summary, "detail": detail, "source_task_id": material.source_task_id,
        "evidence_refs": [asdict(ref) for ref in refs]}
    return _card(data)


async def extract_experience(material, gateway, context, store, *, secrets=()):
    from importlib.resources import files
    from .core.models import ModelRequest
    _check(context)
    text = canonical_bytes(material.payload).decode("utf-8")
    if len(text.encode("utf-8")) > INPUT_BYTES:
        raise ValueError("learning input exceeds byte limit")
    prompt = files("reproagent").joinpath("prompts/extract_experience.md").read_text(encoding="utf-8")
    response = await gateway.complete(ModelRequest(({"role": "system", "content": prompt},
        {"role": "user", "content": _redact(text, secrets)}), response_kind="learning"), context)
    _check(context)
    return validate_experience(parse_json(response.text), material, store, secrets=secrets)
