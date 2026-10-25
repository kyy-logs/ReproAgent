# Design notes — refined ReproAgent landing page

## Brief

ReproAgent is a local Python/pytest bug reproduction tool for developers and
maintainers. The page's job is to explain, in order:

1. what it actually does (differential reproduction, not just test generation);
2. what evidence the user gets and why `DONE` is not `DIFFERENTIAL_VALIDATED`;
3. how to install it and run it through Claude Code / Codex;
4. what it is deliberately not.

## Direction

The page is an **evidence desk**: the hero is the live object under
investigation, and everything below it is the report around that object. The
hero keeps the framed Mac terminal, streaming log, report PiP and scripted
pointer. Below it, the page becomes cooler and more editorial: a left rail
marks each section, and evidence sits in ruled blocks on a cool paper field
rather than inside white cards. A cobalt structural line ties the report
together. Serif headlines give the sections the authority of a written report
without turning the page into a broadsheet. The product is still a tool for
working developers; the page stays light, readable and direct.

## Tokens

- Paper: `#f0f1ec`
- Paper deep: `#e7e9e1`
- Surface: `#ffffff`
- Ink: `#151a1d`
- Muted: `#5c656b`
- Rule: `#d5dad2` / `#b8c0b6`
- Signal (structure, links, final band): `#2447d8` (soft `#e9edfb`)
- Pass: `#0d7a4f` (soft `#e7f3ec`)
- Fail: `#b42318` (soft `#fbeae8`)

Cobalt is structural, not decorative: section rail indices, pipeline nodes,
install step numbers, FAQ toggles, and the closing CTA band. Evidence blocks use
a 1px ink top rule and a hairline bottom rule, never a filled card or a cobalt
frame. Green and red remain reserved for real test status. The hero keeps its
pass green accent and warm pastel frame.

## Type

- **IBM Plex Sans** for UI and body, with `Microsoft YaHei UI` / `PingFang SC`
  fallback for Chinese. Local WOFF2 files, no external font request.
- **IBM Plex Mono** for code, run/status data, file names, rail labels and
  navigation metadata.
- **Newsreader** for Latin display type, paired with a subsetted **Noto Serif
  SC** for Chinese section headlines and the boundary quote. Both are local
  WOFF2 files with OFL licenses in `fonts/`.
- The brand mark is a Newsreader serif **R** with a small cobalt evidence dot,
  paired with the IBM Plex Mono `reproagent` wordmark. The page uses the same
  mark for the nav, footer and favicon.
- One display scale (h1 max 3.5rem, h2 max 3rem), body 16px/1.75, line lengths
  under 80 characters. No all-caps eyebrows, no middle-dot meta strings, no
  arrows on every link.

## Layout

```text
nav
hero: proposition + framed Mac terminal demo + report PiP
rail 01 / output: evidence-package file tree
rail 02 / proof: three-run differential matrix + five-item ledger
rail 03 / pipeline: seven-stage report board with exit codes
rail 04 / levels: evidence level meter + ladder + code specimen
rail 05 / install: Windows/macOS platform tabs + dark command blocks + usage table
rail 06 / macOS: host entry paths + terminal card
rail 07 / limits: fit table + serif pull quote
rail 08 / questions: FAQ intro + expandable sheet
final: cobalt CTA band + command panel
```

Left-aligned throughout, with a 128px sticky rail and a single content column.
The framed demo is the only element with a large shadow; the report PiP is the
only floating window. Evidence blocks are ruled paper, not filled cards: an ink
top rule, a hairline bottom rule, and internal table rules. The reference
(aider.chat) uses a 64-second recorded MP4 with a Safari/game window opening
over the terminal; this page uses a DOM equivalent so it stays responsive,
switchable and small (no 11 MB video).

Below the hero the page uses two registers. Evidence is ruled paper: the
package file tree, the three-run matrix, the pipeline board, the status ladder,
the code specimen, the boundary pair, and the FAQ list. Commands are near black,
echoing the Mac terminal: the Windows quickstart, the Claude Code / Codex /
check blocks, the macOS terminal, and the final command. Cobalt appears as rail
indices, pipeline nodes, install step numbers, FAQ toggles and the closing band;
green and red stay reserved for test status. The final CTA is a cobalt band
rather than a second pastel frame, so the page closes with the report's own
color instead of repeating the hero.

The hero demo is a light Mac terminal (`~/work/my-project — zsh`, traffic
lights). It defaults to **Claude Code** and has a small `Claude Code / Codex /
CLI` switcher. In Claude Code mode it types `~ % claude`, then
`> /reproagent ...`, runs the reproduction steps, prints the verdict and file
names. Codex uses `codex` + `$reproagent`; CLI uses
`reproagent run --config task.json`.

A separate macOS section is install-only: it lists the manual commands
(`python3 -m venv`, `.venv/bin/python -m pip install -e .`,
`.venv/bin/reproagent setup`) and states clearly that the Windows one-click
installer does not apply to macOS.

Motion is deliberate, not scattered. The hero terminal is a looping live run:
the shell command types, the Skill command types, then a real-looking terminal
log streams in — active steps spin, the candidate test diff is added, pytest
FAILED / passed summaries appear in red and green, and the log auto-scrolls.
The verdict and file names appear, then the report window opens
picture-in-picture over the dimmed terminal. It holds for a few seconds, closes,
and replays. A replay button restarts it. It starts the loop while the terminal
is in view. On a fine pointer, the demo frame also tilts a few degrees toward
the cursor, with a soft specular highlight following it; the tilt eases back to
flat when the pointer leaves. The report PiP has its own parallax: it shifts and
tilts a little more than the frame, so it reads as a window floating above the
terminal rather than a flat overlay. During the run a small macOS pointer
appears over the terminal; when the report opens, it travels to the title bar,
grabs it, nudges the report a few pixels left and up, releases, and fades out.
The pipeline draws from left to right when it enters the viewport, with its dots
appearing in sequence. Hover only changes a row/button background. All content
is visible without JavaScript; touch devices leave the frame flat, and
`prefers-reduced-motion` removes the tilt and the scripted pointer as well as
leaving the terminal and report in their final static state.

## What was removed from the earlier versions

- the Aider-derived flat paper page with no section rail or type contrast;
- the first evidence-desk pass with repeated white cards and cobalt top borders;
- warm cream + serif + terracotta palette (generated-page tell);
- all-caps eyebrow labels, middle-dot meta strings, and arrows on every link;
- generic stat numbers and identical rounded feature cards;
- the dotted pipeline board and the pastel final frame that repeated the hero;
- the dark terminal hero and gradient hero wash.

The page still follows the Aider order — light background, short hero, proof,
install, final CTA — but the evidence desk, cobalt system and serif display move
it away from a direct visual copy.
