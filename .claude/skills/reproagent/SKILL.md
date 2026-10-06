---
name: reproagent
description: Use when the user requests ReproAgent to reproduce a Python bug, generate and verify a pytest regression, or inspect a recorded reproduction.
argument-hint: "[check | inspect <task-directory> | <bug description or task-config.json>]"
disable-model-invocation: true
---

# ReproAgent

User request: $ARGUMENTS

Use this project's existing CLI for Python/pytest reproduction. ReproAgent's configured model (currently DeepSeek) generates and reviews tests; Claude Code invokes it and explains the evidence.

## Commands

On Windows, invoke the bundled launcher with a quoted absolute script path:

```text
powershell.exe -NoProfile -File "${CLAUDE_SKILL_DIR}/scripts/reproagent.ps1" -Check
powershell.exe -NoProfile -File "${CLAUDE_SKILL_DIR}/scripts/reproagent.ps1" -Inspect "<absolute task directory>"
powershell.exe -NoProfile -File "${CLAUDE_SKILL_DIR}/scripts/reproagent.ps1" -TaskConfig "<absolute task.json>"
```

Optional run arguments: `-ModelConfig "<model.json>"`, `-FixedRepo "<fixed repo>"`, `-FixedPython "<target python>"`. `-ToolPython` chooses the installed ReproAgent interpreter; it defaults to this project's `.venv/Scripts/python.exe`.
`-ModelBackend native|agentscope` and `-AgentBackend native|agentscope` select independently, both default to native. Use explicit user-selected backends; AgentScope requires the optional extra described in [AgentScope guide](../../../docs/agentscope.md).
On other systems use the installed `reproagent run --config ... --model-config ...` and `reproagent inspect ...` CLI directly. See [input template](../../../examples/task.json) and [integration guide](../../../docs/claude-code.md).

## Workflow

1. `check`: run only `-Check` and report installation readiness. No reproduction or model request. `inspect <directory>`: run only `-Inspect`; summarize existing evidence without rerunning anything.
2. **Existing task JSON: read that file, then immediately invoke the launcher with -TaskConfig.** Do not read target source/issue files, run extra environment probes, or repair/rewrite the supplied configuration before this invocation. ReproAgent performs its own preflight; target modules can be loaded from source_roots without being pip-installed, so a plain `python -c import ...` probe is misleading. For a natural-language bug, prepare an issue file and task JSON under a new ignored `.local/claude-code-tasks/<unique-id>/` directory. Inspect the target project to identify its supplied Python interpreter, target modules, source roots and appropriate pytest directory. Ask for genuinely missing expected behavior or environment; do not invent them.
3. Set schema_version=1; repo/issue_file/output_dir paths are relative to the task JSON directory (absolute paths also work). Use a fresh output directory under this project's `repro-results/claude-code/<unique-id>/`. Pass the target project's interpreter in language.python, not ReproAgent's tool interpreter. Preserve the project's conftest and give explicit target_modules/source_roots/candidate_parent.
4. Run the launcher once. Set the Bash tool timeout longer than the task limit plus cleanup, e.g. timeout=300000 for a 240-second task. Wait for completion; if it runs in the background, use task monitoring with a sensible wait instead of rapid repeated polls. It inherits the configured API key environment variable and can load this Windows user's existing encrypted `.local/deepseek.key` internally. Do not read, print, copy or ask the user to paste credentials. If credential loading fails, report the sanitized error and request that the configured variable be set in the launching terminal; do not inspect the key file. Do not run recursive reproagent skill calls. ReproAgent handles its own finite corrections and budgets; do not rerun failed tasks to hide failures.
5. Inspect the output after the process completes, including nonzero task exits. Report status, evidence_level, stop_reason/uncertainties and the artifact directory. DONE alone does not prove fixed-version validation; only DIFFERENTIAL_VALIDATED proves the supplied fixed version passed. Without a fixed version, repeated observation is the strongest available evidence. A failed regression on the buggy version is expected.

## Boundaries

ReproAgent uses the environment supplied by the user. Do not silently install target dependencies, start services, edit target business code, or expose fixed patches/tests to generation input. Generated tests and target code run with the current user's permissions. Distinguish BLOCKED/NEEDS_INFORMATION/EXHAUSTED/FAILED from successful reproduction and preserve their diagnostics.
