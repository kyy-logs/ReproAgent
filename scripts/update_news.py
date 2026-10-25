#!/usr/bin/env python3
"""Append main-branch commits to ``docs/NEWS.md``.

Run from the repository root. The first run only records the current HEAD in
the ``<!-- news-auto: ... -->`` marker so existing history is not backfilled.
Later runs turn each first-parent commit into one News entry.

Entry text comes from the commit subject. For merge commits the subject of the
merged branch tip is used instead, so entries read like the feature rather than
"Merge pull request #12 ...". Put a bilingual line in the merge or squash
commit body (or the PR description, for squash merges) to fill the Chinese part:

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


def read_entry(sha: str, cwd: Path) -> Entry | None:
    message = run_git("log", "-1", "--format=%B", sha, cwd=cwd)
    lines = message.splitlines()
    subject = lines[0].strip() if lines else ""
    if not subject or "[skip news]" in message.lower():
        return None
    if subject.lower().startswith(SKIP_SUBJECT_PREFIXES):
        return None

    parents = run_git("rev-list", "--parents", "-n", "1", sha, cwd=cwd).split()[1:]
    source_message = message
    source = subject
    if len(parents) > 1:
        try:
            source = (
                run_git(
                    "log", "--no-merges", "-1", "--format=%s", f"{sha}^2", cwd=cwd
                )
                or subject
            )
            source_message = (
                run_git(
                    "log", "--no-merges", "-1", "--format=%B", f"{sha}^2", cwd=cwd
                )
                or message
            )
        except RuntimeError:
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
    for candidate in (message, source_message):
        for line in candidate.splitlines():
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
