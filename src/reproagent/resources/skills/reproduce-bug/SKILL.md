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

- **Fixture and helper contract:** Read the relevant fixture definition, nearest `conftest.py`, helper implementation and a nearby working usage. Check required plugins, test roots, configuration and optional dependencies that this setup actually needs; a familiar fixture name does not establish compatibility.
- **Caller/callee value types:** Follow the values passed between the candidate, fixture and helper, especially temporary paths. A `pathlib.Path` / `WindowsPath` cannot satisfy a legacy helper that calls `.makedirs()`. Check the actual accepted path type and working construction pattern instead of assuming all path objects or strings are interchangeable.
- **Minimal lifecycle:** Establish how the fixture or constructor discovers inputs, when it initializes/builds, and where input and output files belong. Writing under `app.srcdir` does not by itself prove the application will consume those files or produce the target output. Resolve setup conflicts before adding target-behavior assertions.

If a fixture has a known compatibility conflict, use an original-source-supported compatible setup or a smaller public entry point. For example, direct `Sphinx(...)` construction can avoid a broken test helper, but its signature, required configuration and path arguments still need checking. Keep setup changes within the permitted candidate area; do not patch protected helpers, install dependencies through unavailable tools, or fabricate the expected failure.

`ENVIRONMENT_BLOCKED` is terminal in this workflow: setup can fail before assertions, document building or target observation, with no next exploration phase to repair it. Do not publish a known-incompatible fixture hoping to correct it after execution. If essential compatibility information remains unavailable through the registered tools, use `request_information` with that specific gap. Keep this check local to the intended setup and preserve the existing execution/correction budget; it is not a repository-wide environment audit.

## Construct a Minimal Candidate

Use the checked entry point, compatible fixture/helper and essential configuration. Stop broad reading once the setup, supported trigger and source-backed oracle are sufficient to publish.

Keep independent scenarios in separate test nodes. Assert the target value, exception type or diagnostic body at the precision the evidence supports. Extra exact wording and internal-shape assertions need their own support; remove unsupported checks rather than weakening the whole test into a broad transcript search. Add a control only when it helps distinguish the mechanism.

Publish through `write_candidate`, using its current schema and permitted candidate area. Do not change protected source, replace the target implementation or fabricate its error. Controller owns execution and Verifier owns acceptance; neither a red test nor model prose establishes reproduction.

## Correct from the Actual Failure

Read the specific failed checks and their evidence before choosing the next action. Treat semantic review as a claim to examine, not a measured execution fact.

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

Choose a concrete hypothesis: “Given evidence X, condition Y may explain result Z; changing/checking Y would distinguish it.” Use the existing hypothesis/reason fields when appropriate; do not add a required reflection artifact or tool argument.

Prefer one meaningful change per experiment so the result is interpretable. Correct coupled setup requirements together when necessary. Compare actual files, parameters and preconditions with the prior attempt; changing only the hypothesis text is not a changed experiment. Repeat unchanged execution only for an explicit purpose such as checking nondeterminism, within the existing budget.

Use `revise_contract` when newly displayed original evidence warrants changing the interpretation of the reproduction target. It is not a progress log or a way to clear execution failures. Do not revise the contract solely to record a timeout or avoid rejection. When an essential fact cannot be obtained through available tools, use `request_information` with the specific gap; do not invent a diagnostic tool or request an unavailable action as though it ran.

Leave budget for execution feedback and correction. Do not require every investigation above on every attempt. Report an unresolved blocker at the existing limit rather than assume extra steps or time. Historical experience is advice, not current source evidence; hidden reference fixes and gold tests must not guide candidate construction.
