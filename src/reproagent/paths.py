"""How a workspace path is represented inside the tool.

ReproAgent nests a frozen copy, a per-run copy and probe files inside the task
directory, so an output path that is already deep can push individual file paths
past the legacy 260 character Windows limit.  The recorded SWT round only got
past that by having the operator shorten the output path by hand; this module
makes the representation the tool's own job, for ordinary path arguments.

Only workspace paths go through here.  An interpreter path never does: keeping a
venv's interpreter link-free is a separate requirement (``absolute_python``),
and canonicalising an interpreter path would run the base interpreter and drop
the venv's packages.
"""
from __future__ import annotations

import os
from pathlib import Path

#: The legacy Windows limit.  A *directory* path stops working twelve characters
#: earlier, because the name that will live inside it has to fit as well.
MAX_PATH = 260
LONG_PREFIX = "\\\\?\\"
UNC_LONG_PREFIX = "\\\\?\\UNC\\"


def _strip_prefix(text: str) -> str:
    if text.startswith(UNC_LONG_PREFIX):
        return "\\\\" + text[len(UNC_LONG_PREFIX):]
    if text.startswith(LONG_PREFIX):
        return text[len(LONG_PREFIX):]
    return text


def _add_prefix(text: str) -> str:
    if text.startswith(LONG_PREFIX):
        return text
    if text.startswith("\\\\"):
        return UNC_LONG_PREFIX + text[2:]
    return LONG_PREFIX + text


def workspace_path(path) -> Path:
    """Absolute long-path-safe form of a workspace path.

    Other platforms keep the path they were given.  On Windows an ordinary path
    stays ordinary -- the existing records, comparisons and report content do
    not change -- and only a path that reaches :data:`MAX_PATH` takes the
    ``\\\\?\\`` form that lifts the limit.
    """
    path = Path(path)
    if os.name != "nt":
        return path
    text = os.path.abspath(path)
    if text.startswith(LONG_PREFIX) or len(text) < MAX_PATH:
        return Path(text)
    return Path(_add_prefix(text))


def shared_path_form(*paths: Path) -> tuple[Path, ...]:
    """Workspace paths that are used together, in one shared representation.

    ``os.replace`` refuses to move a file between the plain and the ``\\\\?\\``
    form, and the temporary of an atomic write is longer than the file it
    replaces, so a group is promoted together as soon as one name needs it.
    """
    forms = tuple(workspace_path(path) for path in paths)
    if os.name == "nt" and any(str(form).startswith(LONG_PREFIX) for form in forms):
        return tuple(Path(_add_prefix(str(form))) for form in forms)
    return forms


def relative_name(path: Path, root: Path) -> str:
    """``path`` relative to ``root`` as a POSIX string, in either path form."""
    try:
        return Path(path).relative_to(Path(root)).as_posix()
    except ValueError:
        return Path(_strip_prefix(os.path.abspath(path))).relative_to(
            Path(_strip_prefix(os.path.abspath(root)))).as_posix()


def is_within(path: Path, root: Path) -> bool:
    """Whether the resolved ``path`` stays inside the resolved ``root``.

    The boundary check compares the two in one representation (case-folded, and
    without the platform prefix) so that a long child of a short root still
    matches.  Nothing is relaxed: a resolved path outside the root is outside it.
    """
    return Path(identity_key(path)).is_relative_to(Path(identity_key(root)))


def identity_key(path: Path) -> str:
    """A key for the location a path names, in either path form.

    Identity comparisons (is this the same directory we are already inside?)
    must not depend on which of the two forms the path was written in.
    """
    return os.path.normcase(_strip_prefix(str(Path(path).resolve())))


def display_path(path) -> str:
    """How a workspace path is shown to a reader: without the platform prefix.

    Presentation only.  It is not a path to open files through nor one to
    compare identities with.
    """
    return _strip_prefix(str(path))
