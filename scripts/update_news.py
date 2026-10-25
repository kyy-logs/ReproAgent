#!/usr/bin/env python3
"""Append main-branch commits to ``docs/NEWS.md``.

Run from the repository root. The first run only records the current HEAD in
the ``<!-- news-auto: ... -->`` marker so existing history is not backfilled.
Later runs turn each first-parent commit that is a user-visible change into one
News entry.

Only ``feat``, ``fix`` and ``perf`` commits are recorded. Prefixes such as
``docs``, ``test``, ``chore``, ``ci``, ``build`` and ``refactor`` are skipped so
News does not turn into a commit log. For a merge commit the highest-impact type
in the merged branch wins (feat > fix > perf), and the newest commit of that
type supplies the text, so a trailing ``docs:`` commit cannot hide a feature.
Put a bilingual line in the merge or squash commit body (or the PR description,
for squash merges) to fill the Chinese part:

    News-zh: Windows 一键安装接入 Claude Code / Codex

The marker is an HTML comment at the end of NEWS.md and is updated on every
run. Bot commits contain ``[skip news]`` so the workflow does not loop.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = os.environ.get("GITHUB_REPOSITORY", "kyy-logs/ReproAgent")
COMMIT_URL = f"https://github.com/{REPO}/commit"
MARKER_RE = re.compile(r"<!--\s*news-auto:\s*([0-9a-fA-F]{7,40})\s*-->")
MONTH_RE = re.compile(r"^##\s+(\d{4}-\d{2})\s*$")
PREFIX_RE = re.compile(
    r"^(feat|fix|docs|test|refactor|perf|chore|build|ci|merge)(\([^)]*\))?!?:\s*",
    re.IGNORECASE,
)
CHINESE_RE = re.compile(r"^\s*(?:News-zh|中文)\s*[:：]\s*(.+?)\s*$", re.IGNORECASE)
SKIP_SUBJECT_PREFIXES = (
    "docs: update news",
    "merge branch 'main' into",
    "merge branch 'master' into",
)
CATEGORY = {
    "feat": "FEAT",
    "fix": "FIX",
    "docs": "DOCS",
    "test": "TEST",
    "refactor": "REFACTOR",
    "perf": "PERF",
    "chore": "CHORE",
    "build": "BUILD",
    "ci": "CI",
    "merge": "MERGE",
}
ALLOWED_TYPES = ("feat", "fix", "perf")
TYPE_PRIORITY = {"feat": 0, "fix": 1, "perf": 2}


@dataclass
class Entry:
    sha: str
    date: str
    category: str
    text: str
    chinese: str | None = None


def run_git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def sentence_case(text: str) -> str:
    if text and text[0].islower():
        return text[0].upper() + text[1:]
    return text


def split_subject(subject: str) -> tuple[str, str]:
    match = PREFIX_RE.match(subject)
    if not match:
        return "CHORE", sentence_case(subject.strip())
    category = CATEGORY.get(match.group(1).lower(), "CHORE")
    text = subject[match.end() :].strip()
    return category, sentence_case(text or subject.strip())


def commit_type(subject: str) -> str | None:
    match = PREFIX_RE.match(subject)
    if not match:
        return None
    return match.group(1).lower()


def collect_merge_candidates(
    first_parent: str, parents: list[str], cwd: Path
) -> list[tuple[str, str, str]]:
    """Return (type, subject, message) for user-visible commits on a merged branch."""
    candidates: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for parent in parents:
        try:
            shas = run_git(
                "rev-list", "--no-merges", "--reverse", f"{first_parent}..{parent}", cwd=cwd
            ).splitlines()
        except RuntimeError:
            continue
        for candidate_sha in shas:
            if candidate_sha in seen:
                continue
            seen.add(candidate_sha)
            candidate_message = run_git(
                "log", "-1", "--format=%B", candidate_sha, cwd=cwd
            )
            candidate_lines = candidate_message.splitlines()
            candidate_subject = candidate_lines[0].strip() if candidate_lines else ""
            kind = commit_type(candidate_subject)
            if kind in ALLOWED_TYPES:
                candidates.append((kind, candidate_subject, candidate_message))
    return candidates


def read_entry(sha: str, cwd: Path) -> Entry | None:
    message = run_git("log", "-1", "--format=%B", sha, cwd=cwd)
    lines = message.splitlines()
    subject = lines[0].strip() if lines else ""
    if not subject or "[skip news]" in message.lower():
        return None
    if subject.lower().startswith(SKIP_SUBJECT_PREFIXES):
        return None

    parents = run_git("rev-list", "--parents", "-n", "1", sha, cwd=cwd).split()[1:]
    candidates: list[tuple[str, str, str]] = []
    if len(parents) > 1:
        candidates = collect_merge_candidates(parents[0], parents[1:], cwd)
        if not candidates:
            return None
        best_priority = min(TYPE_PRIORITY[kind] for kind, _, _ in candidates)
        _, source, source_message = [
            candidate
            for candidate in candidates
            if TYPE_PRIORITY[candidate[0]] == best_priority
        ][-1]
    else:
        kind = commit_type(subject)
        if kind not in ALLOWED_TYPES:
            return None
        source = subject
        source_message = message

    if "[skip news]" in source_message.lower():
        return None
    if source.lower().startswith(SKIP_SUBJECT_PREFIXES):
        return None

    category, text = split_subject(source)
    if not text:
        return None

    chinese = None
    for candidate_message in (message, source_message, *[c[2] for c in candidates]):
        for line in candidate_message.splitlines():
            match = CHINESE_RE.match(line)
            if match:
                chinese = match.group(1)
                break
        if chinese:
            break

    date = run_git("log", "-1", "--format=%cd", "--date=short", sha, cwd=cwd)
    return Entry(sha=sha, date=date, category=category, text=text, chinese=chinese)


def format_entry(entry: Entry) -> str:
    link = f"[{entry.sha[:7]}]({COMMIT_URL}/{entry.sha})"
    line = f"- **{entry.date} · `{entry.category}`** — {entry.text} {link}"
    if entry.chinese:
        line += f"  \n  **中文**：{entry.chinese}"
    return line


def insert_entry(text: str, entry_line: str, month: str) -> str:
    lines = text.splitlines()
    heading = f"## {month}"

    if heading in lines:
        index = lines.index(heading) + 1
        while index < len(lines) and not lines[index].strip():
            index += 1
        payload = [entry_line]
        if index >= len(lines) or lines[index].lstrip().startswith("## "):
            payload.append("")
        lines[index:index] = payload
        return "\n".join(lines) + ("\n" if text.endswith("\n") else "")

    section = [heading, "", entry_line, ""]
    insert_at = None
    for index, line in enumerate(lines):
        match = MONTH_RE.match(line)
        if match and match.group(1) < month:
            insert_at = index
            break
    if insert_at is None:
        marker_index = next(
            (i for i, line in enumerate(lines) if MARKER_RE.search(line)), None
        )
        if marker_index is not None:
            insert_at = marker_index
            if insert_at > 0 and lines[insert_at - 1].strip():
                section.insert(0, "")
        else:
            if lines and lines[-1].strip():
                lines.append("")
            lines.extend(section)
            return "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    lines[insert_at:insert_at] = section
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def set_marker(text: str, sha: str) -> str:
    marker = f"<!-- news-auto: {sha} -->"
    if MARKER_RE.search(text):
        return MARKER_RE.sub(marker, text, count=1)
    return text.rstrip() + f"\n\n{marker}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--news",
        type=Path,
        help="path to NEWS.md (default: <repo>/docs/NEWS.md)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the result instead of writing NEWS.md",
    )
    args = parser.parse_args()

    root = Path(run_git("rev-parse", "--show-toplevel", cwd=Path.cwd()))
    news_path = args.news or root / "docs" / "NEWS.md"
    if not news_path.exists():
        print(f"update_news: {news_path} not found", file=sys.stderr)
        return 1

    text = news_path.read_text(encoding="utf-8")
    head = run_git("rev-parse", "HEAD", cwd=root)
    marker_match = MARKER_RE.search(text)

    if not marker_match:
        result = set_marker(text, head)
        if args.dry_run:
            print(f"update_news: would initialize marker at {head[:7]}")
            return 0
        news_path.write_text(result, encoding="utf-8", newline="\n")
        print(f"update_news: initialized marker at {head[:7]}; no entries added")
        return 0

    marker = marker_match.group(1)
    try:
        marker_sha = run_git("rev-parse", "--verify", f"{marker}^{{commit}}", cwd=root)
    except RuntimeError:
        print(
            f"update_news: marker {marker} not found; re-initializing at {head[:7]}",
            file=sys.stderr,
        )
        if not args.dry_run:
            news_path.write_text(set_marker(text, head), encoding="utf-8", newline="\n")
        return 0

    if marker_sha == head:
        print("update_news: up to date")
        return 0

    shas = run_git(
        "rev-list", "--first-parent", "--reverse", f"{marker_sha}..{head}", cwd=root
    ).splitlines()

    result = text
    added = 0
    for sha in shas:
        if f"/commit/{sha}" in result:
            continue
        entry = read_entry(sha, root)
        if entry is None:
            continue
        result = insert_entry(result, format_entry(entry), entry.date[:7])
        added += 1

    result = set_marker(result, head)

    if args.dry_run:
        print(result)
        return 0
    if result == text:
        print("update_news: no changes")
        return 0

    news_path.write_text(result, encoding="utf-8", newline="\n")
    print(f"update_news: added {added} entr{'y' if added == 1 else 'ies'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
