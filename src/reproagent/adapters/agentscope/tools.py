"""The six tools one exploration phase may call, and the evidence each of them really showed.

The SDK's own ``Read``/``Grep``/``Glob`` are kept — their search, line formatting and caching
are the SDK's job — and are bound to the frozen snapshot's backend.  Their default
descriptions promise the whole machine, which is exactly what this phase must not do, so the
three tools are subclassed and re-described as views of the registered snapshot; the SDK
itself is untouched.

Every response is capped at the task's tool budget and its lines are recorded as citable
only when the tool displayed them whole: a line Read truncated, a line ripgrep cut at 500
bytes, a filename listing and a Glob hit cite nothing, and each citation is issued at the
width of the tool that produced it (Read shows wider lines than Grep).  The citations travel
back to the model in a small ``<evidence>`` sidecar, and only lines the response still
carries are cited, so a trimmed response can never cite a line the model did not see.

The three domain tools end the phase through a :class:`PhaseGate`: they publish through the
product's own :class:`Workspace`, they cite only lines an evidence ledger issued, and they
return controlled ids and summaries rather than free text.  Nothing here decides business
policy — the controller reads the phase result and continues.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from agentscope.message import TextBlock, ToolResultState
from agentscope.permission import PermissionBehavior, PermissionDecision
from agentscope.tool import Glob, Grep, ParamsBase, Read, ToolBase, ToolChunk, ToolMiddlewareBase, Toolkit
from pydantic import ConfigDict, Field

from .dependency import require_agentscope
from .evidence import READ_LINE_CHARACTERS, EvidenceLedger, original_lines
from .snapshot_backend import SnapshotRefusal, UnverifiedContent
from ...core.budget import BudgetStopped
from ...core.candidate_service import CandidateService
from ...core.models import CandidateDraft, DraftFile, EvidenceRef
from ...core.phase import PhaseGate, PhaseResult
from ...paths import relative_name

#: The phase's whole surface, in the order the toolkit registers it.  Nothing else is
#: registered: no shell, no editor, and no run/submit decision for the model to make.
FILE_TOOL_NAMES = ("Read", "Grep", "Glob")
DOMAIN_TOOL_NAMES = ("write_candidate", "revise_contract", "request_information")
TOOL_NAMES = FILE_TOOL_NAMES + DOMAIN_TOOL_NAMES

#: Appended when a response had to be cut to fit the tool budget.  Marked in the response
#: itself, because a response that looks complete is read as complete.
RESPONSE_TRUNCATION_MARKER = "\n[... response truncated; only whole displayed lines are citable]"

#: The model-facing sidecar of the lines a file tool displayed whole.
SIDECAR_OPEN = "\n<evidence>"
SIDECAR_CLOSE = "</evidence>"
#: The sidecar's own bytes, so that every citation is charged for the bytes it adds.
SIDECAR_FRAME_BYTES = len(SIDECAR_OPEN) + len(SIDECAR_CLOSE) + len('{"refs":[]}')

READ_DESCRIPTION = """Reads a file from the registered original snapshot.

Only the files of the frozen snapshot can be read: a path outside it is refused, not read
from the machine, and a file whose bytes changed since the freeze is refused too. Give an
absolute path, or a path relative to the snapshot root.

Usage:
- The file_path parameter is a path to a file in the snapshot
- By default, it reads up to 2000 lines starting from the beginning of the file
- You can optionally specify a line offset and limit
- Results are returned using cat -n format, with line numbers starting at 1
- Lines that a response shows whole appear in an `<evidence>` block; only those lines may be
  cited when asking for a contract revision, and a line marked "[truncated]" is never one
  of them"""

GREP_DESCRIPTION = """Searches the registered original snapshot with ripgrep.

Only the files of the frozen snapshot are searched; a path outside it is refused, and a
search that could not run says so instead of reporting no matches.

Usage:
- Supports full regex syntax; the pattern is a regular expression, not a plain string
- Filter files with the glob or type parameter
- Output modes: "content" shows matching lines, "files_with_matches" shows only paths
  (default), "count" shows match counts per file
- Only "content" displays lines, and a response lists the lines it displayed whole in an
  `<evidence>` block; a line ripgrep cut short is not one of them"""

GLOB_DESCRIPTION = """Fast file pattern matching over the registered original snapshot.

Only the files recorded in the frozen snapshot are matched; anything written into the
snapshot afterwards is invisible, and a directory outside the snapshot is refused.

Usage:
- Supports glob patterns like "**/*.py" or "src/**/*.ts"
- The path parameter is the base directory, relative to the snapshot root or absolute
- Results are file names only: they authorise no line citation"""

WRITE_CANDIDATE_DESCRIPTION = """Publishes one reproduction candidate and ends this phase.

The candidate is written immutably into the task's candidate area and executed by the
controller afterwards; this tool never runs it and its result is not a verdict. Give the
complete content of every file, the test area's install path, the role of each file, and the
hypothesis the candidate tests. The contract version to publish against is the one bound to
this phase; you cannot choose it here. A refused publication leaves the phase open."""

REVISE_CONTRACT_DESCRIPTION = """Requests a contract revision, citing lines this phase displayed.

Cite one of the line ranges an `<evidence>` block of a previous Read or Grep response listed,
copying its path, content hash and both line numbers exactly as they appear there. To cite
fewer lines than a range covers, Read exactly those lines first — a citation is exactly a
range a tool displayed whole. A citation the phase never displayed, or one whose hash is not
the frozen file's, is refused and leaves the phase open. The controller decides the revision
itself."""

REQUEST_INFORMATION_DESCRIPTION = """Ends this phase by reporting the information it is missing.

Use it when the issue cannot be understood from the frozen snapshot and the supplied issue:
say exactly what is missing, in one question. The controller stops the task and reports that
the information is needed; no candidate is published."""


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _capped(context, text: str) -> str:
    """*text* cut to the task's tool budget with the truncation marker, or *text*."""
    cap = context.budget.limits.tool_response_bytes
    encoded = text.encode("utf-8")
    if len(encoded) <= cap:
        return text
    marker = RESPONSE_TRUNCATION_MARKER.encode("utf-8")
    return encoded[: max(0, cap - len(marker))].decode("utf-8", errors="ignore") + RESPONSE_TRUNCATION_MARKER


def canonical_ref(ledger: EvidenceLedger, path: str, start: int, end: int) -> EvidenceRef:
    """The store-relative citation for lines *start*-*end* of registered *path*.

    The citation is built from the frozen manifest — the path, hash and line count the
    snapshot holds — rather than from the strings a caller supplied, so a forged path, hash
    or range has nothing to attach to.  Whether those lines were ever *displayed* is a
    separate question, answered by the ledger's membership check.

    Raises:
        SnapshotRefusal: the path is not registered, or the file is not verified text.
        UnverifiedContent: the range is not a real range of the file's lines.
    """
    item = ledger.snapshot.validate_registered(path)
    lines = original_lines(ledger.snapshot.read_verified(item))
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
        raise UnverifiedContent(f"{start}-{end} is not a real line range of {item.path!r}")
    return EvidenceRef(relative_name(ledger.snapshot.path_of(item), ledger.store.root), item.content_hash, start, end)


class EvidenceViews:
    """What each file tool displayed, at that tool's own width.

    Read shows a line whole up to its own limit; Grep shows at most the 500 bytes ripgrep is
    told to print.  A line one tool truncated is not the line the other displayed, so each
    has its own ledger, and a citation counts as displayed when either of them issued it.
    """

    def __init__(self, search: EvidenceLedger) -> None:
        self.search = search
        self.read = EvidenceLedger(search.project, search.store, max_line_characters=READ_LINE_CHARACTERS)

    def contains(self, ref: EvidenceRef) -> bool:
        """Whether a Read view or a search view displayed exactly this reference."""
        return self.search.contains(ref) or self.read.contains(ref)


@dataclass(frozen=True, slots=True)
class _Shown:
    """One line a file tool displayed whole, ready to be cited."""

    index: int        #: the body line it was displayed on
    path: str         #: the snapshot-relative path of the registered file
    content_hash: str  #: the hash of the frozen original
    number: int       #: the 1-based line number of the original


@dataclass(frozen=True, slots=True)
class _Range:
    """A contiguous citation: the displayed lines *start*-*end* of one frozen file."""

    first: int        #: the first body line of the range
    last: int         #: the last body line of the range
    path: str
    content_hash: str
    start: int
    end: int

    @property
    def payment(self) -> int:
        """The bytes this citation costs the response's evidence sidecar."""
        return len(_dump(self.entry).encode("utf-8")) + 1        # plus the JSON separator

    @property
    def entry(self) -> dict:
        """The citation as the model sees it: the path, the frozen hash and the line range."""
        return {"path": self.path, "content_hash": self.content_hash, "start_line": self.start, "end_line": self.end}

    def clipped_to(self, keep: int) -> "_Range":
        """This range cut to the body lines that survived the response budget."""
        last = min(self.last, keep - 1)
        return _Range(self.first, last, self.path, self.content_hash, self.start, self.start + last - self.first)


class SnapshotResponseMiddleware(ToolMiddlewareBase):
    """Caps one file-tool response and records exactly the lines it displayed whole."""

    #: How the tool's body names the lines it showed.
    LINE_PATTERNS = {
        "read": re.compile(r" *(?P<number>\d+)\t(?P<content>.*)\Z"),
        "search": re.compile(r"(?P<path>.+?)(?P<sep>[:-])(?P<number>\d+)(?P=sep)(?P<content>.*)\Z"),
        "none": None,
    }
    #: A search of a single file prints no file name; the requested path is the file.
    SINGLE_FILE = re.compile(r"(?P<number>\d+)[:-](?P<content>.*)\Z")

    def __init__(self, backend, ledger: EvidenceLedger, context, *, view: str) -> None:
        """Bind the middleware to the backend it reads through and the view it records."""
        if view not in self.LINE_PATTERNS:
            raise ValueError(f"unknown file tool view: {view!r}")
        self.backend, self.ledger, self.context, self.view = backend, ledger, context, view

    async def on_tool_call(self, tool, input_kwargs, next_handler):
        """Keep the tool's own result, capped, with the evidence of what it displayed."""
        chunks = [chunk async for chunk in next_handler(**input_kwargs)]
        if not chunks:
            return
        if any(not isinstance(block, TextBlock) for chunk in chunks for block in chunk.content):
            # A non-text view (an image, a PDF) is passed through as it is: nothing in it is a
            # citable original line, and nothing here may rewrite what the model receives.
            for chunk in chunks:
                yield chunk
            return
        body = "".join(block.text for chunk in chunks for block in chunk.content)
        last = chunks[-1]
        text = await self._capped(input_kwargs, body)
        yield ToolChunk(content=[TextBlock(text=text)], state=last.state, is_last=True, metadata=dict(last.metadata))

    async def _capped(self, input_kwargs: dict, body: str) -> str:
        """*body* inside the tool budget, with a sidecar that names only displayed lines."""
        cap = self.context.budget.limits.tool_response_bytes
        lines = body.split("\n")
        runs = self._runs(await self._shown(input_kwargs, lines))
        costs = {run.first: run.payment for run in runs}
        used, keep = 0, len(lines)
        for index, line in enumerate(lines):
            cost = len(line.encode("utf-8")) + 1 + costs.get(index, 0)
            if used + cost + SIDECAR_FRAME_BYTES + len(RESPONSE_TRUNCATION_MARKER.encode("utf-8")) > cap:
                keep = index
                break
            used += cost
        kept = lines[:keep]
        truncated = keep < len(lines)
        citations = [run.clipped_to(keep) for run in runs if run.first < keep]
        text = self._compose(kept, citations, truncated)
        while citations and len(text.encode("utf-8")) > cap:
            # The estimate above is deliberately generous; this can only ever drop the last
            # citation, never a line the response is still showing.
            citations = citations[:-1]
            text = self._compose(kept, citations, truncated)
        if len(text.encode("utf-8")) > cap:
            # The configured budget cannot hold even the truncation marker: the body is cut
            # hard and nothing is cited, because no line is certainly still visible.
            return text.encode("utf-8")[:cap].decode("utf-8", errors="ignore")
        citations = [run for run in citations if self._issue(run)]
        return self._compose(kept, citations, truncated)

    async def _shown(self, input_kwargs: dict, lines: list[str]) -> list[_Shown]:
        """The lines of *lines* this tool displayed whole, in body order."""
        pattern = self.LINE_PATTERNS[self.view]
        if pattern is None:
            return []
        default_path = self._single_file(input_kwargs) if self.view == "search" else input_kwargs.get("file_path")
        originals: dict[str, list[str] | None] = {}
        shown: list[_Shown] = []
        for index, line in enumerate(lines):
            match = pattern.match(line)
            path = match.group("path") if match and "path" in match.groupdict() else default_path
            if match is None and default_path is not None:
                match = self.SINGLE_FILE.match(line)
            if match is None:
                continue
            item = self.ledger.snapshot.registered(path) if isinstance(path, str) else None
            if item is None:
                continue
            original = await self._original(item.path, originals)
            number = int(match.group("number"))
            if original is None or not 1 <= number <= len(original):
                continue
            text = original[number - 1]
            if match.group("content") != text:
                # The tool showed something other than this whole original line: a truncated
                # line, a reformatted one, or a line of another file entirely.
                continue
            if len(text.encode("utf-8")) > self.ledger.max_line_characters:
                continue
            shown.append(_Shown(index, item.path, item.content_hash, number))
        return shown

    def _single_file(self, input_kwargs: dict) -> str | None:
        """The one registered file a search was pointed at, or None for a directory search."""
        path = input_kwargs.get("path")
        if not isinstance(path, str) or self.ledger.snapshot.registered(path) is None:
            return None
        return path

    async def _original(self, path: str, cache: dict) -> list[str] | None:
        """The verified original lines of a registered file, or None if it is not citable."""
        if path not in cache:
            try:
                cache[path] = original_lines(await self.backend.read_file(path))
            except SnapshotRefusal:
                cache[path] = None
        return cache[path]

    @staticmethod
    def _runs(shown: list[_Shown]) -> list[_Range]:
        """*shown* as the contiguous citations a reader can cite in one go."""
        runs: list[_Range] = []
        for item in shown:
            if runs:
                run = runs[-1]
                if item.path == run.path and item.index == run.last + 1 and item.number == run.end + 1:
                    runs[-1] = _Range(run.first, item.index, run.path, run.content_hash, run.start, item.number)
                    continue
            runs.append(_Range(item.index, item.index, item.path, item.content_hash, item.number, item.number))
        return runs

    def _issue(self, run: _Range) -> bool:
        """Record one citation with the ledger, and say whether it is still evidence."""
        try:
            self.ledger.record_visible(run.path, run.start, run.end)
        except SnapshotRefusal:
            return False
        return True

    @staticmethod
    def _compose(kept: list[str], citations: list[_Range], truncated: bool) -> str:
        text = "\n".join(kept)
        if citations:
            text += SIDECAR_OPEN + _dump({"refs": [run.entry for run in citations]}) + SIDECAR_CLOSE
        if truncated:
            text += RESPONSE_TRUNCATION_MARKER
        return text


class SnapshotRead(Read):
    """The SDK's Read, described as a view of the registered snapshot."""

    @property
    def description(self) -> str:  # type: ignore[override]
        return READ_DESCRIPTION


class SnapshotGrep(Grep):
    """The SDK's Grep, described as a view of the registered snapshot."""

    description: str = GREP_DESCRIPTION


class SnapshotGlob(Glob):
    """The SDK's Glob, described as a view of the registered snapshot."""

    description: str = GLOB_DESCRIPTION


class _CandidateFileParams(ParamsBase):
    """One file of a candidate."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="Install path inside the test area, for example tests/test_repro.py.")
    content: str = Field(description="The whole UTF-8 content of the file.")
    role: str = Field(description="'test' for a test module, 'data' for any other file.")


class _WriteCandidateParams(ParamsBase):
    """The arguments of ``write_candidate``."""

    model_config = ConfigDict(extra="forbid")

    files: list[_CandidateFileParams] = Field(min_length=1, description="The complete candidate files, at least one test module.")
    hypothesis: str = Field(min_length=1, description="What the candidate tests and which contract fact it reproduces.")


class _SourceRefParams(ParamsBase):
    """One line range a revision cites."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="The path of an evidence entry: a snapshot-relative path such as example/parser.py.")
    content_hash: str = Field(description="The content hash from the same evidence entry.")
    start_line: int = Field(ge=1, description="The first displayed line of the range.")
    end_line: int = Field(ge=1, description="The last displayed line of the range.")


class _ReviseContractParams(ParamsBase):
    """The arguments of ``revise_contract``."""

    model_config = ConfigDict(extra="forbid")

    source_refs: list[_SourceRefParams] = Field(min_length=1, description="The displayed line ranges the revision rests on.")
    reason: str = Field(min_length=1, description="Why the contract has to change.")


class _RequestInformationParams(ParamsBase):
    """The arguments of ``request_information``."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, description="The one thing the task cannot proceed without.")


class _DomainTool(ToolBase):
    """A domain tool: it owns one phase result and never runs the candidate."""

    is_mcp = False
    is_read_only = False
    is_concurrency_safe = False
    is_external_tool = False
    is_state_injected = False

    def __init__(self, gate: PhaseGate, context) -> None:
        super().__init__()
        self.gate, self.context = gate, context

    def _open(self) -> None:
        """Refuse to act for a phase that has already ended.

        Raises:
            BudgetStopped: the task is cancelled, over its deadline or over budget.
            ValueError: this phase already produced its result.
        """
        self.context.budget.check()
        if self.context.cancel_event.is_set():
            raise BudgetStopped("CANCELLED")
        if self.gate.finished:
            raise ValueError("this phase has already ended; the controller decides what happens next")

    async def check_permissions(self, tool_input, context) -> PermissionDecision:
        """Allow the call: a domain tool is how the phase ends, so it must actually run.

        ``ALLOW`` is required here, not ``PASSTHROUGH``.  In AgentScope 2.0.9 a passthrough
        means "let the engine continue with rule matching", and a non-read-only tool then
        falls through to the mode fallback: DEFAULT asks a user who is not there, DONT_ASK
        (the unattended mode) denies outright, and only BYPASS would run it.  Either way no
        phase result would ever be produced.  Every check that matters is still applied —
        deny rules are evaluated before this method, and the tool itself re-checks the
        budget, the cancellation flag, the bound contract and the evidence ledger.

        Note that EXPLORE denies every non-read-only tool without consulting it at all, so
        the exploration phase must not run in EXPLORE mode; an SDK agent that may publish
        candidates runs in DEFAULT or DONT_ASK.
        """
        return PermissionDecision(behavior=PermissionBehavior.ALLOW,
                                  message=f"{self.name} is a phase tool; the phase's own checks already gate it")

    def _result(self, payload: dict) -> ToolChunk:
        return ToolChunk(content=[TextBlock(text=_capped(self.context, _dump(payload)))],
                         state=ToolResultState.SUCCESS, is_last=True)


class WriteCandidate(_DomainTool):
    """Publishes one candidate through the product's own Workspace."""

    name: str = "write_candidate"
    description: str = WRITE_CANDIDATE_DESCRIPTION
    input_schema: dict = _WriteCandidateParams.model_json_schema()

    def __init__(self, candidates: CandidateService, gate: PhaseGate, context) -> None:
        super().__init__(gate, context)
        self.candidates = candidates

    async def call(self, **kwargs) -> ToolChunk:
        """Publish the draft the contract and snapshot of this phase imply."""
        self._open()
        params = _WriteCandidateParams(**kwargs)
        contract = self.candidates.contract
        if contract is None:
            raise ValueError("no contract is bound to this phase, so no candidate can be published")
        draft = CandidateDraft(
            tuple(DraftFile(item.path, item.content.encode("utf-8"), item.role) for item in params.files),
            self.candidates.snapshot.snapshot_id, contract.contract_id, contract.version, params.hypothesis,
            expectation_sources=contract.sources)
        candidate = self.candidates.publish(draft)
        self.gate.finish(PhaseResult("candidate", candidate_id=candidate.candidate_id))
        return self._result({"status": "candidate_published", "candidate_id": candidate.candidate_id,
                             "contract_id": candidate.contract_id, "contract_version": candidate.contract_version,
                             "snapshot_id": candidate.snapshot_id, "manifest_hash": candidate.manifest_hash,
                             "files": [{"path": entry.path, "content_hash": entry.content_hash, "role": entry.role}
                                       for entry in candidate.files],
                             "note": "The candidate is registered immutably and the controller executes it next. This is not a verdict: no test has run."})


class ReviseContract(_DomainTool):
    """Requests a contract revision, citing only lines a tool displayed."""

    name: str = "revise_contract"
    description: str = REVISE_CONTRACT_DESCRIPTION
    input_schema: dict = _ReviseContractParams.model_json_schema()

    def __init__(self, views: EvidenceViews, gate: PhaseGate, context) -> None:
        super().__init__(gate, context)
        self.views = views

    async def call(self, **kwargs) -> ToolChunk:
        """Accept the revision request if every citation is a displayed original range."""
        self._open()
        params = _ReviseContractParams(**kwargs)
        refs = tuple(self._cite(item) for item in params.source_refs)
        self.gate.finish(PhaseResult("revise_contract", source_refs=refs, reason=params.reason))
        return self._result({"status": "revision_requested", "reason": params.reason,
                             "source_refs": [{"path": ref.path, "content_hash": ref.content_hash,
                                              "start_line": ref.start_line, "end_line": ref.end_line} for ref in refs],
                             "note": "The controller decides the revision; this tool did not change the contract."})

    def _cite(self, item: _SourceRefParams) -> EvidenceRef:
        """The canonical citation for *item*, or a refusal.

        Raises:
            UnverifiedContent: the hash is not the frozen file's, or the range was never
                displayed by a tool whose view includes it.
        """
        canonical = canonical_ref(self.views.search, item.path, item.start_line, item.end_line)
        if canonical.content_hash != item.content_hash:
            raise UnverifiedContent(f"the cited hash is not the frozen hash of {item.path!r}; copy the hash an evidence block listed")
        if not self.views.contains(canonical):
            raise UnverifiedContent(f"lines {item.start_line}-{item.end_line} of {item.path!r} were not displayed whole by Read or Grep; "
                                    "cite a range an evidence block listed exactly, and Read the exact lines first if you need a narrower one")
        return canonical


class RequestInformation(_DomainTool):
    """Ends the phase by reporting the information it is missing."""

    name: str = "request_information"
    description: str = REQUEST_INFORMATION_DESCRIPTION
    input_schema: dict = _RequestInformationParams.model_json_schema()

    async def call(self, **kwargs) -> ToolChunk:
        """End the phase with the missing information, and publish nothing."""
        self._open()
        params = _RequestInformationParams(**kwargs)
        self.gate.finish(PhaseResult("request_information", question=params.question))
        return self._result({"status": "information_requested", "question": params.question,
                             "note": "The controller stops the task and reports the missing information."})


def build_toolkit(backend, ledger: EvidenceLedger, candidates: CandidateService, gate: PhaseGate, context) -> Toolkit:
    """The toolkit one exploration phase runs with: exactly :data:`TOOL_NAMES`.

    Args:
        backend: the read-only backend of the phase's frozen snapshot.
        ledger: the ledger of search-sourced views (Grep); Read gets its own, wider view.
        candidates: the phase's candidate service, bound to the contract and snapshot.
        gate: the phase's result gate, which one domain tool ends the phase through.
        context: budget and cancellation for every tool.

    Returns:
        `Toolkit`: the registered tools, and nothing else.
    """
    require_agentscope()
    views = EvidenceViews(ledger)
    tools = [
        SnapshotRead(backend=backend, max_line_characters=READ_LINE_CHARACTERS,
                     middlewares=[SnapshotResponseMiddleware(backend, views.read, context, view="read")]),
        SnapshotGrep(backend=backend, middlewares=[SnapshotResponseMiddleware(backend, views.search, context, view="search")]),
        SnapshotGlob(backend=backend, middlewares=[SnapshotResponseMiddleware(backend, views.search, context, view="none")]),
        WriteCandidate(candidates, gate, context),
        ReviseContract(views, gate, context),
        RequestInformation(gate, context),
    ]
    toolkit = Toolkit(tools=tools)
    registered = tuple(tool.name for tool in toolkit.tool_groups[0].tools)
    if registered != TOOL_NAMES:
        raise ValueError(f"the phase toolkit must register exactly {TOOL_NAMES}; got {registered}")
    return toolkit


__all__ = ["DOMAIN_TOOL_NAMES", "FILE_TOOL_NAMES", "TOOL_NAMES", "EvidenceViews", "RESPONSE_TRUNCATION_MARKER",
           "build_toolkit", "canonical_ref"]
