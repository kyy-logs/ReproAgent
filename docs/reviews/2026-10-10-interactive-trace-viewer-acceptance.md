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
| `E:/node/nodejs/node.exe .local/interactive-trace-viewer-verification/browser-check.cjs` | 12 scenario checks passed, zero page errors / external requests |

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
| V08 | pass: independent GPT-6.1 review plus one RED/GREEN correction pass; commands/browser evidence preserved | progress.md, review-report.md, review-red.log, review-green.log, final-regression.log | acceptance receipt |

The three skips are two Windows symlink-creation privilege checks and one interpreter-link test inapplicable to this interpreter.
They are explicit skips, not passes; regression-skip-reasons.log and final-regression.log preserve the reasons.
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

GPT-6.1 reviewed 9e12b97..15c7dec read-only, without Astra or subagents; no Critical findings.
Four Important findings were verified: static per-call associations lost, uncited retained captures hidden,
shared requests attributed only to the first branch, nonfinite start timestamps refusing the page.
One Minor numeric fallback inconsistency was regraded Important because the supported static view must not
present impossible counts/cost as measured data. All five findings entered one consolidated correction pass.

- Python regressions: 10 failures observed before the fixes, then 52 model/renderer tests passed.
- Frontend uncited-content helper: missing export RED, then 8 tests passed.
- Browser uncited-content access: missing entry point RED, then 12 scenarios passed; static references also checked.
- Final related Python suite: **98 passed, 3 skipped in 94.93s**, command below. Node: **8 passed**; pip check green.
- No second review was requested; corrections are verified by their regressions and the final related suite.
- No unresolved review findings or deferred minors. Initial reviewer verdict was "with fixes"; the author verified fixes.

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_trace_view_model.py tests/unit/test_trace_rendering.py tests/unit/test_paths.py tests/integration/test_cli.py tests/integration/test_windows_long_paths.py tests/integration/test_installed_package.py tests/integration/test_observability_end_to_end.py -q -rs
E:/node/nodejs/node.exe --test tests/frontend/trace_viewer.test.mjs
```

Final evidence: final-regression.log, frontend-final.log, browser-final.log, browser-results.json,
pip-check-final.log, review-report.md, review-red.log, review-browser-red.log, review-green.log.

Additional rulings from the review:
- Static malformed numbers are treated as Important, with three numeric validation regressions; cost if overgraded: small extra test/display work.
- Synthetic SDK/tool duplicate hints are not a current collector behavior; retain current rule. Cost if a future wrapper appears: duplicate hints may need suppression.
- Configured browser-act profiles and live providers/full domain regression remain outside this display-only acceptance; isolated Chrome/scripted SDK cover changes.
  Cost if wrong: environment-specific behavior is not covered. No real provider tokens were spent.

## Main integration verification

2026-10-10: fast-forwarded local main to the reviewed implementation `08f6f3e` after fetching origin.
The canonical `E:/ReproAgent` environment imports `E:/ReproAgent/src/reproagent/__init__.py`.
Re-ran model/rendering/path/CLI/Windows regressions in that checkout: **90 passed, 3 skipped in 27.90s**;
frontend **8 passed**, pip check green. Full branch diff has no whitespace errors. No source differences
from the previously tested feature commit; wheel and SDK integration evidence remains the 98-test receipt above.
Local logs and screenshots are preserved under `E:/ReproAgent/.local/interactive-trace-viewer-verification/`.

A supplementary real-model smoke on Sphinx #11445 was attempted after acceptance. The prepared target
passed preflight, but the first model attempt failed before exploration. A same-provider read-only diagnostic
confirmed **HTTP 401 / authentication_error: invalid credential**. No candidate or valid reproduction was produced;
this is a provider authentication blocker and does not establish the agent's real-issue success rate.
The failed run's diagnostic report and new interactive trace remain in the local ignored evaluation output.
No credential values or live task payloads are included in this commit.
