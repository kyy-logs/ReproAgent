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
real lines that the tool can display whole are citable: a line the SDK's Read truncates
is not a full line of the original, so it never becomes a citation.
"""
from __future__ import annotations

from ...core.models import EvidenceRef, FileEntry, ProjectView
from ...paths import relative_name, workspace_path
from ...store import TaskStore
from .snapshot_backend import FrozenSnapshot, SnapshotRefusal, UnverifiedContent

#: ``Read.__init__``'s default: a longer line is displayed with a "[truncated]" suffix,
#: so it was never shown whole and cannot be cited as a complete original line.
MAX_LINE_CHARACTERS = 2000


def original_lines(data: bytes) -> list[str]:
    """The lines of verified file bytes, split the way the SDK's Read splits them."""
    return data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").splitlines()


class EvidenceLedger:
    """The original lines that were shown, and only those."""

    def __init__(self, project: ProjectView, store: TaskStore, *, max_line_characters: int = MAX_LINE_CHARACTERS) -> None:
        """Bind the ledger to the same frozen snapshot the tools are bound to.

        Args:
            project: the snapshot whose manifest decides what can be cited.
            store: the task store a citation's path is relative to.
            max_line_characters (`int`, optional): the widest line the phase's Read tool
                displays whole; a longer line is refused rather than cited.
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
            if len(lines[number - 1]) > self.max_line_characters:
                raise UnverifiedContent(f"line {number} of {item.path!r} is longer than the tool displays whole, so it is not citable")
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
