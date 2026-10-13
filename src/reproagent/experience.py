"""Bounded, advisory experience records. Only system lifecycle code may write the library."""
from __future__ import annotations

import os
import re
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path

from .core.budget import BudgetStopped
from .core.models import EvidenceRef
from .core.serialization import canonical_bytes, canonical_hash, parse_json, bytes_hash, ObjectRootError
from .observability import span
from .paths import directory_path, workspace_path, is_within
from .store import atomic_write
from .experience_triggers import ExperienceTrigger, checked_triggers, evidence_triggers, normative_tags

LIBRARY_BYTES = 1024 * 1024

#: Experience is on by default, and a library is a file, so "by default" needs a path to
#: name.  A task config that sets its own ``experience_file`` replaces this one, and one
#: that sets ``learn_experience: false`` still reads it without writing.  The environment
#: override exists so an experiment can keep its library out of the user's home; the
#: evaluation harness deliberately composes its own requests and is not affected here,
#: because a round requires the library it measured against to stay unchanged.
EXPERIENCE_ENV = "REPROAGENT_EXPERIENCE"
DEFAULT_EXPERIENCE_PATH = Path.home() / ".reproagent" / "experiences"
LEGACY_EXPERIENCE_PATH = Path.home() / '.reproagent' / 'experiences.json'


def default_experience_path() -> Path:
    """The library a run uses when its own configuration names none."""
    override = os.environ.get(EXPERIENCE_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()
    default = DEFAULT_EXPERIENCE_PATH
    if not workspace_path(default).exists() and workspace_path(LEGACY_EXPERIENCE_PATH).is_file():
        default = LEGACY_EXPERIENCE_PATH
    return default.resolve()
CARD_BYTES = 4096
VISIBLE_BYTES = 2048
INPUT_BYTES = 8192
LEARNING_SECONDS = 30
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
    triggers: tuple[ExperienceTrigger, ...] = ()
    name: str = ''
    description: str = ''


def _card_record(card):
    """Persist optional metadata only when present; legacy size/shape stays unchanged."""
    data = asdict(card)
    if not card.triggers:
        data.pop("triggers")
    if not card.name and not card.description:
        data.pop('name')
        data.pop('description')
    return data


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
    event_recorded: bool = True
    unknown_usage_attempts: int = 0
    missing_fields: tuple[str, ...] = ()
    needed_bytes: int | None = None
    limit_bytes: int | None = None
    exceeded_bytes: int | None = None


PROPOSAL_FIELDS = frozenset({'category', 'tags', 'name', 'description', 'summary', 'detail', 'evidence_ids'})
LEARNING_VALIDATION_CODES = frozenset({'invalid_json', 'invalid_structure', 'invalid_missing_fields',
    'invalid_extra_fields', 'invalid_evidence_ids', 'invalid_evidence_changed', 'invalid_fields', 'invalid_card_size'})


class LearningValidationError(ValueError):
    """A bounded validation reason; never carries raw model fields or text."""
    def __init__(self, code, *, missing_fields=(), needed_bytes=None, limit_bytes=None):
        if code not in LEARNING_VALIDATION_CODES:
            raise ValueError('unknown learning validation code')
        self.code = code
        self.missing_fields = tuple(sorted(set(missing_fields) & PROPOSAL_FIELDS))
        self.needed_bytes = needed_bytes
        self.limit_bytes = limit_bytes
        self.exceeded_bytes = needed_bytes - limit_bytes if needed_bytes is not None and limit_bytes is not None else None
        super().__init__(code + (': ' + ', '.join(self.missing_fields) if self.missing_fields else ''))


class _CardTooLarge(ValueError):
    """Internal size failure, still a ValueError for existing library readers."""
    def __init__(self, needed_bytes, limit_bytes):
        self.needed_bytes, self.limit_bytes = needed_bytes, limit_bytes
        super().__init__('experience card exceeds byte limit')


def _normalized(text):
    return " ".join(text.casefold().split())


def _identifier(category, tags, summary, triggers=()):
    data = {"category": category, "tags": sorted(set(_normalized(tag) for tag in tags)),
        "summary": _normalized(summary)}
    if triggers:
        data["triggers"] = [asdict(t) for t in checked_triggers(triggers)]
    return "exp_" + canonical_hash(data)


def _card(data, *, check_size=True):
    fields = {"id", "category", "tags", "summary", "detail", "source_task_id", "evidence_refs"}
    if not isinstance(data, dict) or set(data) not in (fields, fields | {"triggers"}, fields | {'name', 'description'}, fields | {'triggers', 'name', 'description'}):
        raise ValueError("invalid experience fields")
    catalog_name, description = data.get('name', ''), data.get('description', '')
    if 'name' in data and (catalog_name, description) != ('', '') and any(type(value) is not str or not value.strip() or len(value) > limit
                             for value, limit in ((catalog_name, 64), (description, 160))):
        raise ValueError('invalid catalog metadata')
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
    triggers = checked_triggers(data.get("triggers", ()))
    if data["id"] != _identifier(data["category"], tags, data["summary"], triggers):
        raise ValueError("experience id does not match normalized content")
    card = ExperienceCard(data["id"], data["category"], tuple(sorted(set(_normalized(t) for t in tags))),
        data["summary"], data["detail"], data["source_task_id"], tuple(checked), triggers, catalog_name, description)
    if check_size:
        needed_bytes = len(canonical_bytes(_card_record(card)))
        if needed_bytes > CARD_BYTES:
            raise _CardTooLarge(needed_bytes, CARD_BYTES)
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
    try:
        root = parse_json(content.decode("utf-8"))
    except RecursionError as exc:
        raise ValueError("invalid experience library nesting") from exc
    if set(root) != {"schema_version", "items"} or type(root["schema_version"]) is not int or root["schema_version"] not in (1, 2) or not isinstance(root["items"], list):
        raise ValueError("invalid experience library schema")
    cards = tuple(_card(item) for item in root["items"])
    if len({card.id for card in cards}) != len(cards):
        raise ValueError("duplicate experience identifiers")
    return content, cards


def load_experience_snapshot(path: Path, *, secrets=(), diagnostic=None) -> ExperienceSnapshot:
    try:
        from .experience_storage import directory_mode, load_directory_snapshot, approved_location
        path = approved_location(Path(path))
        if directory_mode(Path(path)):
            return load_directory_snapshot(path, secrets=secrets, diagnostic=diagnostic)
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
    from .experience_storage import directory_mode, _paths
    candidates = [path, lock]
    if directory_mode(path):
        _paths(path)
        candidates += [(path / c / name).resolve() for c in CATEGORIES for name in ('experiences.json', 'index.json')]
    if any(is_within(candidate, root) for candidate in candidates for root in protected):
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


def append_experience(path: Path, card: ExperienceCard, context, *, diagnostic=None) -> str:
    from .experience_storage import directory_mode, append_directory_experience, approved_location
    try:
        path = approved_location(Path(path))
        if directory_mode(Path(path)):
            return append_directory_experience(path, card, context, diagnostic=diagnostic)
    except (ValueError, OSError):
        return 'store_error'
    try:
        checked = _card(_card_record(card))
    except (ValueError, TypeError):
        return "invalid"
    try:
        _check(context)
        with _library_lock(Path(path)):
            _, cards = _read_library(path)
            if any(old.id == checked.id for old in cards):
                return "duplicate"
            content = canonical_bytes({"schema_version": 2, "items": [_card_record(c) for c in (*cards, checked)]})
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
        self._pending = ()
        self._detail_bytes = 0
        self.last_match_code = "no_match"

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
            if card.triggers:
                continue
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

    def _visible_bytes(self, cards):
        return len(canonical_bytes([{"id": c.id, "summary": self._safe(c.summary)} for c in cards])) + self._detail_bytes

    def _safe(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text

    def match_event(self, trigger):
        from .core.models import ExperienceSummary
        from .experience_triggers import trigger_from_fact
        self.last_match_code = "no_match"
        if not isinstance(trigger, ExperienceTrigger) or trigger_from_fact(asdict(trigger)) is None:
            return ()
        selected = self._selected or ()
        pending = list(self._pending)
        seen = {c.id for c in (*selected, *pending)}
        new = []
        for card in sorted(self.snapshot.cards, key=lambda c: c.id):
            if trigger not in card.triggers:
                continue
            if card.id in seen:
                self.last_match_code = "already_displayed"
                continue
            if len(selected) + len(pending) >= 3:
                self.last_match_code = "count_limit"
                continue
            if self._visible_bytes((*selected, *pending, card)) > VISIBLE_BYTES:
                self.last_match_code = "visible_budget"
                continue
            pending.append(card)
            new.append(card)
            seen.add(card.id)
        self._pending = tuple(pending)
        if new:
            self.last_match_code = "matched"
        return tuple(ExperienceSummary(c.id, self._safe(c.summary)) for c in new)

    def admit_summaries(self, ids):
        # Only actual model input grants access; all unadmitted reservations are released.
        admitted = tuple(self._selected or ()) + tuple(c for c in self._pending if c.id in ids)
        if len(admitted) > 3 or self._visible_bytes(admitted) > VISIBLE_BYTES:
            raise ValueError("experience admission exceeds shared visible budget")
        self._selected = admitted
        self._pending = ()

    def read(self, identifier, *, max_bytes=VISIBLE_BYTES):
        from .experience_catalog import DETAIL_READS
        if len(self._read_ids) >= DETAIL_READS:
            raise ValueError("only two successful experience detail reads are allowed per task")
        card = next((c for c in (self._selected or ()) if c.id == identifier), None)
        if card is None:
            raise ValueError("experience id was not displayed in this task")
        data = {"id": card.id, "summary": card.summary, "detail": card.detail}
        for key in ("summary", "detail"):
            for secret in self.secrets:
                data[key] = data[key].replace(secret, "[REDACTED]")
        summaries = [{"id": c.id, "summary": self._safe(c.summary)} for c in (*self._selected, *self._pending)]
        if len(canonical_bytes(data)) > max_bytes or len(canonical_bytes(summaries)) + len(canonical_bytes(data)) > VISIBLE_BYTES:
            raise ValueError("experience detail exceeds shared visible budget")
        self._read_ids = (*self._read_ids, identifier)
        self._detail_bytes = len(canonical_bytes(data))
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


def build_learning_input(store, result, context, *, secrets=(), exploration=None):
    """Compatibility entry point for the independent pitfall material projection."""
    from .learning_material import prepare_learning_input
    return prepare_learning_input(store, result, context, secrets=secrets, exploration=exploration).material


def validate_experience(response, material, store, *, secrets=()):
    from .store import safe_child
    if not isinstance(response, dict) or set(response) != {"experience"}:
        raise LearningValidationError('invalid_structure')
    proposed = response["experience"]
    if proposed is None:
        return None
    if not isinstance(proposed, dict):
        raise LearningValidationError('invalid_structure')
    missing = PROPOSAL_FIELDS - set(proposed)
    if missing:
        raise LearningValidationError('invalid_missing_fields', missing_fields=missing)
    if set(proposed) - PROPOSAL_FIELDS:
        raise LearningValidationError('invalid_extra_fields')
    ids = proposed["evidence_ids"]
    if not isinstance(ids, list) or not 1 <= len(ids) <= 3 or any(
        type(i) is not str or i not in material.evidence_map for i in ids) or len(set(ids)) != len(ids):
        raise LearningValidationError('invalid_evidence_ids')
    refs, entries = [], []
    for identity in ids:
        ref = material.evidence_map[identity]
        try:
            content = safe_child(store.root, ref.path).read_bytes()
            lines = content.splitlines()
            if bytes_hash(content) != ref.content_hash or not 1 <= ref.start_line == ref.end_line <= len(lines):
                raise ValueError('changed evidence')
            entry = parse_json(lines[ref.start_line - 1].decode("utf-8"))
            if entry.get("id") != identity:
                raise ValueError('changed evidence identity')
        except (OSError, ValueError, TypeError, UnicodeError):
            raise LearningValidationError('invalid_evidence_changed') from None
        refs.append(ref)
        entries.append(entry)
    summary = proposed["summary"]
    detail = proposed["detail"]
    tags = proposed["tags"]
    if type(summary) is not str or type(detail) is not str or not isinstance(tags, list) or any(type(t) is not str for t in tags):
        raise LearningValidationError('invalid_fields')
    summary, detail = _redact(summary, secrets), _redact(detail, secrets)
    tags = [_redact(tag, secrets) for tag in tags]
    category = proposed["category"]
    if any(type(proposed[key]) is not str or not proposed[key].strip() for key in ('name', 'description')):
        raise LearningValidationError('invalid_fields')
    try:
        # Validate the proposal before adding program metadata.
        data = {"id": _identifier(category, tags, summary), "category": category, "tags": tags,
            "summary": summary, "detail": detail, "source_task_id": material.source_task_id,
            "evidence_refs": [asdict(ref) for ref in refs],
            'name': _redact(proposed['name'], secrets) if type(proposed['name']) is str else proposed['name'],
            'description': _redact(proposed['description'], secrets) if type(proposed['description']) is str else proposed['description']}
        _card(data, check_size=False)
        triggers = tuple(sorted({t for entry in entries for t in evidence_triggers(entry)}))[:3] if category in ("model", "workflow") else ()
        tags = normative_tags(tags, triggers)
        data.update(id=_identifier(category, tags, summary, triggers), tags=tags,
            triggers=[asdict(t) for t in triggers])
        return _card(data)
    except _CardTooLarge as exc:
        raise LearningValidationError('invalid_card_size', needed_bytes=exc.needed_bytes, limit_bytes=exc.limit_bytes) from None
    except (ValueError, TypeError):
        raise LearningValidationError('invalid_fields') from None


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
    try:
        proposed = parse_json(response.text)
    except ObjectRootError:
        raise LearningValidationError('invalid_structure') from None
    except (ValueError, TypeError, RecursionError):
        raise LearningValidationError('invalid_json') from None
    return validate_experience(proposed, material, store, secrets=secrets)

class ExperienceService:
    """System-owned task lifecycle; the explorer only receives its advisory view."""
    def __init__(self, request, store, learning_gateway_factory, *, secrets=()):
        self.request, self.store = request, store
        self.learning_gateway_factory = learning_gateway_factory
        self.secrets = tuple(s for s in secrets if s)
        self.path = None
        self.view = None
        self.snapshot = None
        self._learning_started = False
        self._learning_result = None
        self.exploration = {}

    def retain_exploration(self, context):
        """Receive bounded current-task facts only after explorer resources close."""
        self.exploration = dict(context or {})

    def prepare(self, fixed, context):
        _check(context)
        if self.request.experience_file is None:
            return
        self.path = validate_experience_path(self.request.experience_file, self.request, fixed)
        self.snapshot = load_experience_snapshot(self.path, secrets=self.secrets, diagnostic=self._index_diagnostic)
        if self.snapshot.state != "store_error":
            from .experience_catalog import ExperienceCatalogView
            self.view = ExperienceCatalogView(self.snapshot, secrets=self.secrets)
        self.store.append_event("experience.loaded", (), {"state": self.snapshot.state,
            "content_hash": self.snapshot.content_hash, "count": len(self.snapshot.cards)})

    def summaries(self, issue):
        return ()  # Compatibility entry point: catalog loading is exclusively agent-driven.

    def _index_diagnostic(self, category, code):
        try: self.store.append_event('experience.index', (), {'category': category, 'code': code})
        except (OSError, ValueError, RuntimeError): pass

    async def learn(self, result, main_context):
        # Learning runs inside the task's own trace scope, on its own budget: the span
        # shows it without its seconds ever being folded into the task's duration.
        with span('learn') as handle:
            outcome = await self._learn(result, main_context)
            handle.annotate(result_code=outcome.code)
            if outcome.needed_bytes is not None:
                handle.annotate(budget={'needed_bytes':outcome.needed_bytes,
                    'limit_bytes':outcome.limit_bytes, 'exceeded_bytes':outcome.exceeded_bytes})
            return outcome

    async def _learn(self, result, main_context):
        import asyncio
        import time
        from .core.budget import Budget
        from .core.models import BudgetLimits, CallContext, TaskState
        from .core.protocol import ProviderRequestError, ModelOutputError
        if self._learning_started:
            return self._learning_result or LearningResult("busy")
        self._learning_started = True
        started = time.monotonic()
        gateway, card, code = None, None, "skipped_no_evidence"
        missing_fields = ()
        size_diagnostics = {}
        context = CallContext(Budget(BudgetLimits(task_timeout_seconds=LEARNING_SECONDS)), cancel_event=main_context.cancel_event)
        try:
            if self.request.experience_file is None:
                code = "skipped_disabled"
            elif result.status == TaskState.CANCELLED or main_context.cancel_event.is_set():
                code = "skipped_cancelled"
            elif not self.request.learn_experience:
                code = "skipped_read_only"
            elif self.snapshot is not None and self.snapshot.state == "store_error":
                code = "store_error"
            elif self.path is not None and self.learning_gateway_factory is not None:
                from .learning_material import prepare_learning_input
                built = prepare_learning_input(self.store, result, context,
                    secrets=self.secrets, exploration=self.exploration)
                material, code = built.material, built.code
                try:
                    diagnostics = {'code':code}
                    if material is not None:
                        diagnostics.update({'input_bytes':len(canonical_bytes(material.payload)),
                            'evidence_count':len(material.evidence_map),
                            'attempt_count':len(material.payload['attempts']),
                            'omissions':material.payload['omissions']})
                    self.store.append_event('experience.material', (), diagnostics)
                except (OSError, ValueError, TypeError):
                    pass
                if material is not None:
                    _check(context)
                    gateway = self.learning_gateway_factory(self.store)
                    card = await extract_experience(material, gateway, context, self.store, secrets=self.secrets)
                    code = append_experience(self.path, card, context, diagnostic=self._index_diagnostic) if card is not None else "empty"
        except BudgetStopped as exc:
            code = "skipped_cancelled" if exc.reason == "CANCELLED" else "timeout"
        except asyncio.CancelledError:
            main_context.cancel_event.set()
            code = "skipped_cancelled"
        except ProviderRequestError:
            code = "provider_error"
        except LearningValidationError as exc:
            code, missing_fields = exc.code, exc.missing_fields
            size_diagnostics = {'needed_bytes':exc.needed_bytes,
                'limit_bytes':exc.limit_bytes, 'exceeded_bytes':exc.exceeded_bytes}
        except ModelOutputError:
            code = "invalid_model_output"
        except OSError:
            code = "store_error"
        except Exception:
            code = "internal_error"
        finally:
            if gateway is not None:
                close = getattr(gateway, "aclose", None)
                if close is not None:
                    try:
                        await asyncio.wait_for(close(), timeout=1)
                    except (Exception, asyncio.CancelledError):
                        # Main evidence is sealed. Preserve the outcome, report cleanup failure.
                        try:
                            self.store.append_event("experience.cleanup_failed", (), {"code": "CLEANUP_FAILED"})
                        except (OSError, ValueError):
                            pass
        try:
            events, errors = self.store.read_events()
            attempts = [e.payload for e in events if e.kind == "model.attempt" and e.payload.get("response_kind") == "learning"]
            usage = {key: sum(item.get("usage", {}).get(key, 0) for item in attempts)
                     for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
            outcome = LearningResult(code, card.id if card is not None and code in ("written", "duplicate") else "",
                time.monotonic() - started, len(attempts), usage,
                sum(item["cost_value"] for item in attempts if item.get("cost_value") is not None),
                sum(item.get("cost_value") is None for item in attempts),
                unknown_usage_attempts=sum(any(key not in item.get("usage", {}) for key in
                    ("prompt_tokens", "completion_tokens", "total_tokens")) for item in attempts),
                missing_fields=missing_fields, **size_diagnostics)
        except (OSError, ValueError, TypeError):
            outcome = LearningResult(code, card.id if card is not None and code in ("written", "duplicate") else "",
                duration=time.monotonic() - started, missing_fields=missing_fields, **size_diagnostics)
        try:
            self.store.append_event("experience.learning", (), asdict(outcome))
        except (OSError, ValueError, TypeError):
            # The accounting and write outcome are known even if their final event cannot be saved.
            outcome = replace(outcome, event_recorded=False)
        self._learning_result = outcome
        return outcome
