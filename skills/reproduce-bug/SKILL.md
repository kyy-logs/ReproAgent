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

## Construct a Minimal Candidate

Read the relevant entry point and a nearby working test or fixture. Check the actual accepted API, input format and essential configuration rather than guessing setup from names. Stop broad reading once the supported trigger and oracle are sufficient to publish.

Keep independent scenarios in separate test nodes. Assert the target value, exception type or diagnostic body at the precision the evidence supports. Extra exact wording and internal-shape assertions need their own support; remove unsupported checks rather than weakening the whole test into a broad transcript search. Add a control only when it helps distinguish the mechanism.

Trace acquisition and selection too: target call → returned value/report → helper or filter → assertion. A selector, regex, split, index or nonempty guard can impose an unsupported expectation before the final assert. For example, filtering diagnostic lines by the old sentence can discard a correct diagnostic that contains the required new guidance with different wording. Use an original-source-backed way to obtain the actual diagnostic, then check the required meaning. Keep exact selectors when explicitly required; do not replace them with a whole-transcript keyword search that could match test names, input echoes or assertion text. Reason from the issue and original evidence, never a hidden reference fix.

Publish through `write_candidate`, using its current schema and permitted candidate area. Do not change protected source, replace the target implementation or fabricate its error. Controller owns execution and Verifier owns acceptance; neither a red test nor model prose establishes reproduction.

## Correct from the Actual Failure

Read the specific failed checks and their evidence before choosing the next action. Treat semantic review as a claim to examine, not a measured execution fact.

| Available evidence | Useful next investigation |
| --- | --- |
| Input absent from a recorded consumed-input list | Check fixture entry point, filename and configuration before changing the oracle. |
| Test passed without the reported symptom | Recheck trigger preconditions. Passing alone does not prove the target branch was skipped. |
| Setup or helper failed before the target | Compare the helper's original API and working usage; repair candidate setup. |
| Timeout, killed process or incomplete probe | Inspect the reported last stage; narrow costly setup or test scope if supported. This is incomplete evidence, not a completed negative reproduction. |
| Assertion rejected as unsupported | Locate original support or remove that independent assertion; do not relax it merely to satisfy review. |
| Publication or citation refused | Correct the stated contract gap or exact displayed reference, using the registered tool schemas. |

Unavailable path or input instrumentation means **unknown**, not “not executed.” Do not claim branch coverage from a passing test, function call count or missing log line.

## Make the Next Attempt Informative

Choose a concrete hypothesis: “Given evidence X, condition Y may explain result Z; changing/checking Y would distinguish it.” Use the existing hypothesis/reason fields when appropriate; do not add a required reflection artifact or tool argument.

Prefer one meaningful change per experiment so the result is interpretable. Correct coupled setup requirements together when necessary. Compare actual files, parameters and preconditions with the prior attempt; changing only the hypothesis text is not a changed experiment. Repeat unchanged execution only for an explicit purpose such as checking nondeterminism, within the existing budget.

Use `revise_contract` when newly displayed original evidence warrants changing the interpretation of the reproduction target. It is not a progress log or a way to clear execution failures. Do not revise the contract solely to record a timeout or avoid rejection. When an essential fact cannot be obtained through available tools, use `request_information` with the specific gap; do not invent a diagnostic tool or request an unavailable action as though it ran.

Leave budget for execution feedback and correction. Do not require every investigation above on every attempt. Report an unresolved blocker at the existing limit rather than assume extra steps or time. Historical experience is advice, not current source evidence; hidden reference fixes and gold tests must not guide candidate construction.
