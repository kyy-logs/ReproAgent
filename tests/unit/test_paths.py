"""The internal workspace path representation (Task 6).

Ordinary paths must keep the form they had before long-path support existed --
the records, the comparisons and the exported report all depend on it -- and only
a path that reaches the legacy Windows limit takes the longer form.  Windows
itself is exercised end to end in tests/integration/test_windows_long_paths.py.
"""
import importlib.util
import os
from pathlib import Path

import pytest

from reproagent.paths import (MAX_PATH, RESERVED_NAME, directory_path, display_path, identity_key,
                              is_within, relative_name, shared_path_form, workspace_path)

WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="the long-path form is Windows-only")


def at_length(base: Path, length: int) -> Path:
    """A path of exactly ``length`` characters below ``base`` (never created)."""
    path = base
    while len(str(path)) < length:
        step = min(length - len(str(path)) - 1, 100)
        if step <= 0:
            break
        path = path / ("x" * step)
    assert len(str(path)) == length, (len(str(path)), length)
    return path


def test_ordinary_paths_keep_their_form(tmp_path):
    ordinary = tmp_path / "repo"
    assert workspace_path(ordinary) == Path(os.path.abspath(ordinary))
    assert display_path(ordinary) == str(ordinary)
    assert relative_name(ordinary / "issue.md", ordinary) == "issue.md"


def test_display_path_removes_only_the_platform_prefix():
    assert display_path("\\\\?\\C:\\tasks\\one") == "C:\\tasks\\one"
    assert display_path("\\\\?\\UNC\\server\\share\\one") == "\\\\server\\share\\one"
    assert display_path("/home/tasks/one") == "/home/tasks/one"


@WINDOWS_ONLY
def test_a_path_past_the_limit_takes_the_long_form(tmp_path):
    long_path = at_length(tmp_path, 300)
    long_form = workspace_path(long_path)
    assert str(long_form) == "\\\\?\\" + str(long_path)
    assert workspace_path(long_form) == long_form                       # idempotent
    assert workspace_path(at_length(tmp_path, MAX_PATH - 1)) == at_length(tmp_path, MAX_PATH - 1)


@WINDOWS_ONLY
def test_a_directory_past_the_reserved_name_length_takes_the_long_form(tmp_path):
    """A directory stops working before a file does, and is made through its own form.

    ``workspace_path`` promotes at the file limit, because an ordinary file name still
    opens there.  A directory stops twelve characters earlier -- the name that will live
    inside it has to fit -- so a directory the tool has to create can already be too long
    while the same name would still work as a file.
    """
    boundary = at_length(tmp_path, MAX_PATH - RESERVED_NAME)
    assert str(directory_path(boundary)).startswith("\\\\?\\")
    shorter = at_length(tmp_path, MAX_PATH - RESERVED_NAME - 1)
    assert directory_path(shorter) == shorter
    # The file form is untouched: this is a second rule, not a tighter one.
    assert workspace_path(boundary) == boundary
    assert workspace_path(shorter) == shorter
    assert directory_path("\\\\?\\C:\\already\\prefixed") == Path("\\\\?\\C:\\already\\prefixed")


@WINDOWS_ONLY
def test_an_input_that_already_carries_the_prefix_is_kept(tmp_path):
    prefixed = Path("\\\\?\\C:\\already\\prefixed")
    assert workspace_path(prefixed) == prefixed
    # A UNC share is not a drive path, so it takes the UNC spelling of the prefix.
    assert str(workspace_path(Path("\\\\server\\share\\" + "x" * 300))) == "\\\\?\\UNC\\server\\share\\" + "x" * 300


@WINDOWS_ONLY
def test_relative_name_bridges_both_forms(tmp_path):
    root = tmp_path / "out"
    deep = at_length(root, 300)
    assert str(workspace_path(deep)).startswith("\\\\?\\")
    assert relative_name(workspace_path(deep) / "f.txt", root) == deep.relative_to(root).as_posix() + "/f.txt"
    with pytest.raises(ValueError):
        relative_name(workspace_path(deep) / "f.txt", tmp_path / "elsewhere")


@WINDOWS_ONLY
def test_shared_path_form_promotes_a_pair_together(tmp_path):
    plain, longer = at_length(tmp_path, 250), at_length(tmp_path, 300)
    assert len(str(plain)) < MAX_PATH <= len(str(longer))
    promoted, sibling = shared_path_form(plain, longer)
    assert str(promoted).startswith("\\\\?\\") and str(sibling).startswith("\\\\?\\")
    short, other = shared_path_form(tmp_path / "a", tmp_path / "b")
    assert (short, other) == (tmp_path / "a", tmp_path / "b")


@WINDOWS_ONLY
def test_containment_compares_both_forms_but_still_refuses_the_outside(tmp_path):
    root = tmp_path / "out"
    deep = workspace_path(at_length(root, 300))
    assert is_within(deep / "f.txt", root)
    assert not is_within(tmp_path / "outside.txt", root)
    assert not is_within(deep, tmp_path / "another-root")


def replay_module():
    """The replay tool that ships inside an export package, loaded standalone."""
    source = Path(__file__).resolve().parents[2] / "src/reproagent/resources/replay.py"
    spec = importlib.util.spec_from_file_location("replay_under_test", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_standalone_replay_applies_the_same_rule(tmp_path):
    """replay.py cannot import reproagent.paths, so its copies are checked here."""
    module = replay_module()
    for path in (tmp_path / "short", at_length(tmp_path, 300), Path("\\\\?\\C:\\already\\prefixed")):
        assert module.long_path(path) == workspace_path(path)
        assert module.location_key(path) == identity_key(path)


def test_the_standalone_replay_still_refuses_a_link_out_of_the_package(tmp_path):
    """A link inside the package must not let a package file reach outside it."""
    module = replay_module()
    package, outside = tmp_path / "package", tmp_path / "outside"
    (package / "candidate").mkdir(parents=True)
    outside.mkdir()
    (outside / "x.py").write_text("secret", encoding="utf-8")
    try:
        (package / "candidate" / "escape").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("link creation needs privilege on Windows")
    with pytest.raises(ValueError, match="escapes"):
        module.child(package / "candidate", "escape/x.py")
    (package / "candidate" / "x.py").write_text("installed", encoding="utf-8")
    assert module.child(package / "candidate", "x.py").read_text(encoding="utf-8") == "installed"
