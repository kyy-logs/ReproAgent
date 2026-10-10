# Interactive offline trace viewer acceptance

Date: 2026-10-10. Baseline: main `9e12b97`. Implementation branch: `codex/interactive-trace-viewer`.
Scope: presentation, packaged resources, tests and documentation. Controller, AgentScope hooks, Verifier,
permissions, budgets, collector and model request behavior are unchanged. No live LLM calls during acceptance;
SDK integration uses a scripted HTTP transport. No Astra model is used.

## Verification

Run from the implementation checkout, with `REPROAGENT_RG_PATH` pointing to the existing rg executable.

| Command | Observed |
| --- | --- |
| `.venv/Scripts/python.exe -m pytest tests/unit/test_trace_view_model.py tests/unit/test_trace_rendering.py tests/unit/test_paths.py tests/integration/test_cli.py tests/integration/test_windows_long_paths.py -q` | 80 passed, 3 skipped, 22.80s |
| `.venv/Scripts/python.exe -m pytest tests/integration/test_installed_package.py tests/integration/test_observability_end_to_end.py -q` | 8 passed, 80.32s |
| `E:/node/nodejs/node.exe --test tests/frontend/trace_viewer.test.mjs` | 7 passed |
| `.venv/Scripts/python.exe -m pip check` | No broken requirements |
| `E:/node/nodejs/node.exe .local/interactive-trace-viewer-verification/browser-check.cjs` | 11 scenario checks passed, zero page errors / external requests |

Browser: Chrome 155.0.8059.39 through bundled Playwright, isolated temporary headless profile.
No browser installation, configured user profile mutation or remote backend. file:// at 1366x900 and 800x900.
Visual inspection used desktop.png and narrow.png; screenshots supplement assertions.

## Acceptance matrix

All evidence paths below are relative to `.local/interactive-trace-viewer-verification/` in the implementation checkout.

| ID | Expected / status | Evidence / command | Failure location and recheck |
| --- | --- | --- | --- |
| V01 | pass: schema1/2, true parents, permission/tool capture sharing; source immutable | regression.log, view-model tests | trace_view_model.py; rerun regression command |
| V02 | pass: finite geometry, real zero/unknown, partial labels, recorded retries and reserve hints | regression.log, browser-results.json | view-model / template; rerun unit + browser |
| V03 | pass: query, collapse restoration, filters, detail, acceptance tab | frontend.log, browser-results.json | trace_viewer.mjs; rerun Node + browser |
| V04 | pass: retained arguments/results/reasoning; controlled OTel ERROR excluded | browser-results.json, integration-recheck.log | projection / detail; rerun integration + browser |
| V05 | pass: data-island escaping, exact module/hash, one body, zero external requests and XSS marker absent | regression.log, browser-results.json | rendering / module; rerun unit + browser |
| V06 | pass: file://, JS disabled + init failure fallback, narrow layout, keyboard return focus, 1024-depth search | browser.log, desktop.png, narrow.png | template / module; rerun browser |
| V07 | pass: wheel includes/inlines module in independent env; no additional SDK/pytest work or task artifacts; old JSON retained; size and path guards | integration-recheck.log, regression.log | packaging / rendering; rerun both Python commands |
| V08 | pending independent GPT-6.1 review; commands and browser evidence preserved | progress.md, review-report.md (after review) | acceptance receipt |

The three platform/capability skips are explicit pytest skips in the regression log; they are not counted as passes.
The older 704-pass migration suite is not this viewer's acceptance. No new domain changes require repeating that full suite.

## Failures and corrections

- TDD: missing view-model interface; four renderer contracts; six missing frontend behaviors, then GREEN.
- Native timestamp seconds were incompatible with Date(milliseconds): formatStartTime test added RED -> GREEN.
- Browser measured axis.left=523.875 vs track.left=587.875: align axis with duration column; browser assertion now passes.
- New wheel harness printed Unicode through GBK stdout: explicitly use UTF-8; independent wheel render now passes.
- Existing isolated pytest setup hit an expired Tsinghua mirror certificate: installed the same pinned pytest from official PyPI
  in the test-only target environment. No TLS checks disabled or product dependency changes.
- Browser harness tried a default-collapsed node and read closed native details with innerText: corrected harness to search/open
  ancestors and inspect stored text. Product collapse and fallback remained correct.

## Decisions

- Equivalent PowerShell ledger replaces Bash-only skill helpers on Windows; task evidence and Git commits are preserved.
- Browser acceptance uses bundled Playwright for deterministic JS-off, viewport, console/request checks. The optional
  browser-act profile selection was not answered, so that configured profile was never opened or mutated.
- New integration assertions are regression gates after packaging implementation, rather than forcing artificial product RED.
  They exposed real harness failures which were corrected and rerun.

## Independent review

Pending. Reviewer must be GPT-6.1, read-only, no Astra, and cover all five Review Focus items from the implementation plan.
