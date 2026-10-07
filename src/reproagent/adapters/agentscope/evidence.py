"""Line-level citations from the registered snapshot, re-verified on every use.

A tool result is a view: Read shows the lines the SDK chose to show, Grep shows the
matches it printed, and a model can claim anything about either.  The ledger is the only
thing that issues a citation, and each citation is built from a fresh read of the
registered file, so a hash, a line range or a path the model invented is not in the
ledger at all, and a file that changed after the first read cannot be cited from the
cached view the SDK kept.

A citation names its file the way the rest of the task does — relative to the task
store, carrying the raw-byte hash of the frozen original, which is what
``Verifier.resolve`` checks again before the range is ever treated as evidence.  Only
real lines that the tool can display whole are citable: a line either tool truncates is
not a full line of the original, so it never becomes a citation, and the default width
is the stricter of the two (Grep's 500-byte ``--max-columns``).  Line numbers are
counted the way the verifier counts them, so a range this ledger issues is a range the
verifier accepts.
"""
from __future__ import annotations

from ...core.models import EvidenceRef, FileEntry, ProjectView
from ...paths import relative_name, workspace_path
from ...store import TaskStore
from .snapshot_backend import FrozenSnapshot, SnapshotRefusal, UnverifiedContent

#: The widest line a citation covers, in bytes.  The SDK's Grep always runs ripgrep with
#: ``--max-columns 500``, so a wider line is never shown whole by Grep; Read shows a line
#: whole up to its own ``max_line_characters`` (``READ_LINE_CHARACTERS``).  The ledger
#: cannot tell which tool produced a view, so it defaults to the strictest of the two: a
#: caller that knows the view came from Read may pass ``READ_LINE_CHARACTERS``.
MAX_LINE_CHARACTERS = 500
#: ``Read.__init__``'s default line width, for a caller whose view came from Read.
READ_LINE_CHARACTERS = 2000


def original_lines(data: bytes) -> list[str]:
    """The lines of verified file bytes, exactly as ``Verifier.resolve`` will count them.

    ``Verifier.resolve`` splits the raw bytes (on CR, LF and CRLF only), while the SDK's
    Read splits the decoded text, which also breaks on a form feed, a vertical tab, ``U+2028``
    and friends.  When the two counts disagree the file's line numbers are ambiguous — a
    range the model saw is not the range the verifier would read — so no range from that
    file is citable at all.  When they agree, the views name the same lines.
    """
    lines = data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").splitlines()
    counted = len(data.splitlines())
    if len(lines) != counted:
        raise UnverifiedContent(f"the file's lines are counted differently by the SDK ({len(lines)}) and the verifier ({counted}), so no range in it is citable")
    return lines


class EvidenceLedger:
    """The original lines that were shown, and only those."""

    def __init__(self, project: ProjectView, store: TaskStore, *, max_line_characters: int = MAX_LINE_CHARACTERS) -> None:
        """Bind the ledger to the same frozen snapshot the tools are bound to.

        Args:
            project: the snapshot whose manifest decides what can be cited.
            store: the task store a citation's path is relative to.
            max_line_characters (`int`, optional): the widest line, in bytes, that the tool
                which produced the view displays whole.  The default is the stricter of the
                two the phase uses — ripgrep's ``--max-columns 500``, which the SDK's Grep
                always sets; pass ``READ_LINE_CHARACTERS`` for a view known to come from Read.
        """
        self.project, self.store = project, store
        self.snapshot = FrozenSnapshot(project, store)
        self.max_line_characters = max_line_characters
        self._issued: list[EvidenceRef] = []

    def record_visible(self, path: str, start: int, end: int) -> EvidenceRef:
        """The citation for lines *start*-*end* of the registered file *path*.

        Raises:
            SnapshotDenied: *path* is not part of the registered snapshot.
            UnverifiedContent: the file no longer matches the manifest, is not UTF-8
                text, or the range is not a displayable range of real lines.
        """
        item = self.snapshot.validate_registered(path)
        lines = self._lines(item)
        if type(start) is not int or type(end) is not int or not 1 <= start <= end <= len(lines):
            raise UnverifiedContent(f"{start}-{end} is not a real line range of {item.path!r}")
        for number in range(start, end + 1):
            if len(lines[number - 1].encode("utf-8")) > self.max_line_characters:
                raise UnverifiedContent(f"line {number} of {item.path!r} is wider than the tool displays whole, so it is not citable")
        cited = EvidenceRef(relative_name(self.snapshot.path_of(item), self.store.root), item.content_hash, start, end)
        if cited not in self._issued:
            self._issued.append(cited)
        return cited

    def contains(self, ref: EvidenceRef) -> bool:
        """Whether *ref* is a citation this ledger issued, and its file still matches it.

        The check is repeated here because a citation outlives the read that produced it:
        a snapshot file altered afterwards stops matching and the citation is no longer
        evidence, even though the ledger issued it earlier.
        """
        if not isinstance(ref, EvidenceRef) or ref not in self._issued:
            return False
        item = self.snapshot.registered(str(workspace_path(self.store.root.joinpath(*ref.path.split("/")))))
        if item is None or item.content_hash != ref.content_hash:
            return False
        try:
            lines = self._lines(item)
        except SnapshotRefusal:
            return False
        return 1 <= ref.start_line <= ref.end_line <= len(lines)

    def _lines(self, item: FileEntry) -> list[str]:
        """The original lines of a registered file, re-verified against the manifest now."""
        return original_lines(self.snapshot.read_verified(item))
