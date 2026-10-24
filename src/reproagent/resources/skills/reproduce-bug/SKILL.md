---
name: reproduce-bug
description: Guide Python and pytest reproduction candidates in ReproAgent when turning an issue into a test or correcting a rejected or non-reproducing candidate. Applies to reproduction, not source-code repair.
---

# Reproduce a Bug from Evidence

Produce the smallest supported test that distinguishes the reported defect from correct behavior. Borrow systematic debugging's evidence, hypothesis and experiment loop; full root-cause diagnosis is not a prerequisite for publishing a reproduction candidate.

This skill guides exploration. The task's registered tools, source protections, budgets and completion checks remain authoritative. It grants no additional capabilities.

## Establish the Testable Difference

Separate three questions:

- **Trigger:** What input and conditions should exercise the reported behavior?
- **Observation:** What did an actual execution show?
- **Oracle:** What source-backed correct behavior should the test assert?

Use the issue and displayed original documentation, signatures and existing tests to support the oracle. Buggy implementation behavior helps locate the trigger; it does not by itself define correct behavior. Keep guesses separate from observations. Missing exact output need not block a supported equivalence or invariant, but do not invent desired behavior merely to obtain a failing test.

## Preflight Test Setup and Fixture Compatibility

Before `write_candidate`, check the setup you intend to use with the registered `Read`, `Grep` and `Glob` tools. This is a focused source-based compatibility check, not an executed smoke test. Do not claim that imports, fixtures or builds passed without execution evidence, or invent a command/execution tool.

Check the intended setup in this order, reusing definitions already displayed in the current exploration:

1. **Read the actual entry point.** Before relying on a nearby test, inspect the chosen fixture/helper definition and its relevant initialization path. Locate it with `Glob`/`Grep` and read the signature plus the code that consumes the arguments your candidate supplies. For example, using `SphinxTestApp` requires checking that helper, not only a test that imports it. A familiar name or an existing usage alone is not compatibility evidence.
2. **Match the actual caller values.** Follow those arguments from the candidate into the callee: their concrete types, required methods and default-derived values. A `tmp_path`-derived `pathlib.Path` / `WindowsPath` cannot satisfy a helper that calls `.makedirs()`. When adapting an existing test, compare its fixture/path construction with your own; substituting a path object or string needs support from the receiving API.
3. **Check the local lifecycle.** Inspect applicable `conftest.py`, working usage when present, and the plugins, roots, configuration or dependencies this setup needs. Establish when it discovers inputs, initializes/builds and consumes files. Writing under `app.srcdir` alone does not establish consumption. Missing nearby examples alone are not a blocker when original APIs, types and lifecycle establish compatibility.

A failed search is not evidence that a helper or constraint is absent. If `Grep` is unavailable, use `Glob` and targeted `Read` calls. Complete this local compatibility check before `write_candidate`; it is still a static check, not proof that initialization ran successfully.

If a fixture has a known compatibility conflict, use an original-source-supported compatible setup or a smaller public entry point. For example, direct `Sphinx(...)` construction can avoid a broken test helper, but its signature, required configuration and path arguments still need checking. Keep setup changes within the permitted candidate area. Use the existing environment; do not install dependencies or embed installation code in the candidate. Do not patch protected helpers or fabricate the expected failure.

`ENVIRONMENT_BLOCKED` is terminal in this workflow: setup can fail before assertions, document building or target observation, with no next exploration phase to repair it. Do not publish a known-incompatible fixture hoping to correct it after execution. If essential compatibility information remains unavailable through the registered tools, use `request_information` with that specific gap. Keep this check local to the intended setup and preserve the existing execution/correction budget; it is not a repository-wide environment audit.

## Construct a Minimal Candidate

Use the checked entry point, compatible fixture/helper and essential configuration. Stop broad reading once the setup, supported trigger and source-backed oracle are sufficient to publish.

When adapting a displayed test, carry over its necessary setup together with the function body: decorators/markers, fixture arguments, configuration overrides and test-root selection. Before publishing, compare those setup choices against the actual candidate. Trace the candidate's module reference to the file that defines it, and match the requested output filename/format to the chosen builder or producer. For example, reading `index.txt` requires an original-source-supported text-producing setup; importing pytest alone does not apply a missing fixture marker. Explain intentional setup differences in the existing hypothesis prose; add no tool fields or separate checklist artifact.

Before publishing, compare the candidate's executable input with the issue's supported trigger: expression nesting, argument values, configuration and initialization/cache conditions. Check the code being submitted, not just its hypothesis text. When minimizing or adapting an example, preserve the trigger conditions and explain any material difference using available evidence; literal copying, identical variable names and identical syntax are not required.

Keep independent scenarios in separate test nodes. Assert the target value, exception type or diagnostic body at the precision the evidence supports. Extra exact wording and internal-shape assertions need their own support; remove unsupported checks rather than weakening the whole test into a broad transcript search. Add a control only when it helps distinguish the mechanism.

Publish through `write_candidate`, using its current schema and permitted candidate area. Do not change protected source, replace the target implementation or fabricate its error. Controller owns execution and Verifier owns acceptance; neither a red test nor model prose establishes reproduction.

## Correct from the Actual Failure

Read the specific failed checks and their evidence before choosing the next action. Treat semantic review as a claim to examine, not a measured execution fact.

Identify the earliest evidenced blocker that prevented observation of the target behavior; the first item in a verdict is not necessarily that blocker. If recorded execution failed during setup, import or initialization, investigate that candidate prerequisite before changing the target assertion. Use the recorded stage and exception to locate the relevant original API and caller values. If the stopping stage is unknown, preserve that uncertainty instead of inventing an execution order. Continue correction only when Controller returns to exploration.

| Available evidence | Useful next investigation |
| --- | --- |
| Input absent from a recorded consumed-input list | Check fixture entry point, filename and configuration before changing the oracle. |
| Test passed without the reported symptom | Recheck trigger preconditions. Passing alone does not prove the target branch was skipped. |
| Setup or helper failed before the target, and Controller returned to exploration | Compare the helper's original API and working usage; repair candidate setup. A terminal `ENVIRONMENT_BLOCKED` does not permit another attempt. |
| Timeout, killed process or incomplete probe | Inspect the reported last stage; narrow costly setup or test scope if supported. This is incomplete evidence, not a completed negative reproduction. |
| Assertion rejected as unsupported | Locate original support or remove that independent assertion; do not relax it merely to satisfy review. |
| Publication or citation refused | Correct the stated contract gap or exact displayed reference, using the registered tool schemas. |

Unavailable path or input instrumentation means **unknown**, not “not executed.” Do not claim branch coverage from a passing test, function call count or missing log line.

## Make the Next Attempt Informative

Choose a concrete hypothesis. In the existing hypothesis/reason prose, state the evidence, the condition to change or check, the predicted observable result, and a result that would weaken or refute the explanation. Keep this concise; do not add tool fields or a required reflection artifact. Interpret a contrary result only when the prerequisites for that check and its required observation completed. If execution stops before the check or the required observation is unavailable, preserve uncertainty rather than declaring the hypothesis disproved.

Prefer one meaningful change per experiment so the result is interpretable. Correct coupled setup requirements together when necessary. Compare actual files, parameters and preconditions with the prior attempt; changing only the hypothesis text is not a changed experiment. Repeat unchanged execution only for an explicit purpose such as checking nondeterminism, within the existing budget.

Before another adjustment, compare the earliest evidenced blocker with the prior attempt. If it remains the same and the changes do not address it, return to the entry point, actual inputs and original evidence before publishing again. For example, changing assertion wording cannot resolve unchanged input-discovery or initialization failure. Reassess the hypothesis and choose a check that distinguishes explanations; repeating the symptom is not new support. This is a reason to change investigation, not a fixed retry count or permission to exceed the existing budget.

Use `revise_contract` when newly displayed original evidence warrants changing the interpretation of the reproduction target. It is not a progress log or a way to clear execution failures. Do not revise the contract solely to record a timeout or avoid rejection. When an essential fact cannot be obtained through available tools, use `request_information` with the specific gap; do not invent a diagnostic tool or request an unavailable action as though it ran.

Leave budget for execution feedback and correction. Do not require every investigation above on every attempt. Report an unresolved blocker at the existing limit rather than assume extra steps or time. Historical experience is advice, not current source evidence; hidden reference fixes and gold tests must not guide candidate construction.
