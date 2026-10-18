# STATE injection policy repair acceptance — 2026-10-18

## Scope and provenance

- Worktree: `C:/Users/Dell/.codex/worktrees/task-state-context/ReproAgent`
- Branch: `codex/task-state-context`; HEAD: `ccd181d9476360ac378b2745f3fcb326245e7483`.
- The starting tree already contained the product repairs used by the A/C experiment.
  Starting diff SHA-256: `effe7ad68724b3e03e8ec165da6890fc9a78d1cd64d4344b490547c2a0a5adc0`.
  Those repairs were preserved. This is not a claim about clean ccd181d.
- No paid model calls, Astra use, commits, pushes or merges in this repair.
- Logs, baseline/after patches and source hashes:
  `E:/ReproAgent/repro-results/state-injection-policy-2026-10-18/`.

## Changes in this repair

1. `TaskRequest.inject_task_state` defaults to false. Legacy JSON without this field
   loads unchanged. The flag must be boolean and is saved in request.json.
2. `app.py -> agentscope_explorer_factory -> AgentScopeExplorer -> AgentScopeRuntime
   -> ExplorationContextBuilder` carries the per-task policy to actual SDK requests.
   Off disables only the extra task_state message and context.prepared event.
   Program state/budget persistence still runs before model calls.
3. STATE completion separates historical observation from final acceptance:
   `observation_candidate_id` and `evidence_level` preserve TaskResult's observation;
   `final_accepted` requires DONE, published delivery, a candidate and repeated or
   differential evidence. `accepted_candidate_id` is null until those conditions hold.
   A failed replay or exhausted task does not erase past observations or claim final acceptance.
4. Existing STATE-specific integration tests now explicitly opt in. Shared test harnesses
   follow the production default. Added config-to-wire tests cover both modes through
   CLI config loading, actual assembly, SDK agent and MockTransport HTTP payloads.
5. Updated docs/agentscope.md and docs/observability.md.

The Controller, Verifier, TaskResult evidence semantics, tool permissions, budget rules,
SDK history, tool-output offloading and read_artifact behaviour were not redesigned.
The pre-existing Grep/pre-target-warning/contract-caveat repairs remain part of the tree.

## Acceptance

| Requirement | Result |
|---|---|
| Legacy config sends no extra STATE | Passed: actual HTTP payload contains no task_state |
| Explicit true reaches actual SDK HTTP | Passed: one state per request, live refusal and budget |
| Local state remains available with injection off | Passed: contract, refusal, status and budget persist |
| Selected input policy is auditable | Passed: saved request includes the boolean flag |
| Failed replay is not final acceptance | Passed: negative latest verdict + historical single observation + final false |
| Exhaustion/failed export/single observation cannot become final | Passed: parameterized record/view and rehydration checks |
| Completed repeated/differential result remains accepted | Passed |
| Controller TaskResult is not rewritten by STATE projection | Passed |

## Test evidence and initial failures

- `red.log`: 15 failed before implementation; demonstrated default injection and provisional
  candidate being exposed as accepted, along with the absent explicit config policy.
- `green2.log`: 26 passed after implementation.
- `focused.log`: 94 passed in 53.29 s across seven relevant modules.
- `full.log`: complete project pytest run, exit 1, **1213 passed / 8 failed / 6 skipped**
  in 1685.32 s. The first full run is retained and is not reported as clean.
- `retry-external.log`: affected two complete modules, **17 passed**, exit 0, 33.34 s.
  No product/test code changed between the complete run and this retry.

The first full-run basetemp was mistakenly placed under E:/ReproAgent/repro-results,
inside the other checkout. Two concrete consequences were verified from raw output:

1. Seven failures in tests/integration/test_swt_controls.py: nested pytest inherited
   E:/ReproAgent as rootdir. Raw buggy.json/fixed.json recorded call phases under a
   repro-results/... prefix, while the control compared tests/test_reference.py::...
   and reported target never collected. These failures were:
   test_success_requires_buggy_call_failure_and_all_fixed_calls_pass;
   test_collection_error_is_not_a_discriminating_control;
   test_skipped_or_setup_failed_nodes_do_not_qualify[setup_failure/skipped];
   test_control_never_mutates_generation_repositories;
   test_control_cli_records_every_selected_case;
   test_control_subprocess_environment_excludes_secrets.
2. test_capture_covers_code_prompts_resources_evals_and_pyproject in
   tests/unit/test_swt_provenance.py expected a fixture outside Git, but read the
   enclosing E:/ReproAgent commit 317342d7ea5d8567cad998b2c713e98a058f1659.

Both complete modules passed when rerun with basetemp at
C:/Users/Dell/AppData/Local/Temp/reproagent-state-policy-20261018-retry1,
outside both checkouts. This is a directory correction, not a product-code fix.
The deduplicated tested outcomes after retry are **1221 passed / 6 skipped**;
there was no single all-green full-suite invocation. All six skips are Windows
link/symlink privileges or linked-interpreter conditions, not counted as passes.
Source hashes stayed unchanged during the complete run and retry.

Reproduce the affected module retry with a fresh external basetemp:

```powershell
$env:REPROAGENT_RG_PATH = 'E:/ReproAgent/.local/bin/rg.exe'
.venv/Scripts/python.exe -m pytest tests/integration/test_swt_controls.py tests/unit/test_swt_provenance.py -q -rs --basetemp "$env:TEMP/reproagent-state-policy-independent-retry"
```

For a full independent run, use the same external-directory rule and omit the two
module selectors. Do not put basetemp inside either checkout.

## Configuration and limits

Normal task JSON may omit the new field; the default is false. To opt in experimentally:

```json
{"inject_task_state": true}
```

This is one field to add to an existing task config, not a complete task file.
No new command is required. This change establishes input policy and honest state
semantics; offline tests do not establish a higher reproduction rate. Future real-model
comparisons must freeze the same source and keep offloading fixed between arms.
