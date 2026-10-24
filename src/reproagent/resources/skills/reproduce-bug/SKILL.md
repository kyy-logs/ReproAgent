---
name: reproduce-bug
description: Guide Python and pytest reproduction tests when locating an issue's trigger or correcting a rejected or non-reproducing candidate. Applies to reproduction, not source-code repair.
---

# Locate the Trigger and Reproduce the Bug

Find a source-supported trigger and publish the smallest discriminating test. Borrow systematic debugging's backward tracing, working-example comparison and hypothesis testing. Full root-cause diagnosis is not required before publishing.

## Start from the Strongest Anchor

Choose an entry, not a mandatory sequence:

| Available information | First useful investigation |
| --- | --- |
| Traceback | Read the relevant failure site and caller; follow actual argument values toward their origin. Distinguish setup from target failure. |
| Exact warning/error text | Search for its producer; read the surrounding conditions and relevant caller/configuration. |
| Named API and wrong result | Read its definition and a nearby original test; compare inputs and expectations. |
| Vague description | Extract one observable symptom and likely public entry point; search relevant modules. |
| Execution feedback | Start with the earliest evidenced blocker below. |

Use registered `Grep`, `Glob` and `Read` on the frozen original snapshot. Search narrowly with identifiers, error fragments or known paths; read local context. A failed search does not prove absence: adjust the anchor/scope or use another registered reader.

Trace the relevant data and guards: what input reached this operation, who supplied it, and which conditions control it? Stop widening the search once you have enough evidence for a supported trigger and compatible setup; do not map the whole repository first.

## Compare with a Working Original Example

Use supplied environment facts, not remembered versions. Before introducing a fixture, check its definition and applicable plugin/conftest registration or a supported working use in this project. An uncollected inventory is unknown, not an empty list; a definition alone does not prove activation.

Prefer an original test using the same API, fixture or builder. Preserve required markers, arguments, configuration, roots and initialization order; identify what the issue changes. Do not copy only the test body.

Before relying on a helper, inspect its actual signature and relevant initialization, not just a familiar usage. Follow candidate values into it: concrete types, required methods and defaults. A `pathlib.Path` cannot satisfy `.makedirs()`. Check applicable `conftest.py` and discovery/build timing as needed. Writing an input does not establish consumption; match output formats to their producer.

Reuse displayed definitions. A missing nearby example need not block publication when original APIs, types and lifecycle establish compatibility. Resolve known conflicts with supported candidate setup or a smaller public entry point. Static inspection does not establish that imports or builds passed.

## Separate Trigger, Observation and Oracle

- **Trigger:** Executable input, configuration and lifecycle conditions.
- **Observation:** What an actual execution showed; incomplete runs leave uncertainty.
- **Oracle:** Source-supported correct behavior to assert.

Use the issue and displayed original documentation, signatures and tests for the oracle. Buggy implementation helps locate a trigger but does not alone define correctness. A supported invariant/equivalence may suffice without exact output. Finding a warning producer does not establish that its warning should disappear.

Keep independent scenarios in separate test nodes. Assert only supported precision; extra wording, unrelated outputs or internal-shape checks need their own basis. Add a control only when it distinguishes explanations.

## Make One Informative Experiment

In existing hypothesis/reason prose, connect **evidence → suspected condition → candidate change → predicted observation**, including what would weaken the explanation. No new tool fields or reflection artifact.

For example: "The prior build consumed only the default input; a documented setting selects our file. Change that setting while retaining the assertion. Consumption without the symptom would weaken this trigger hypothesis; failure before discovery leaves it untested." Without consumption/coverage instrumentation, do not claim a path was skipped.

Prefer one meaningful change; coupled setup requirements may change together. Preserve issue trigger conditions, including nesting, values and initialization/cache state. Check actual candidate content in history, feedback or proposed arguments, not just the hypothesis. Snapshot readers cannot read generated candidate files. Rewording the explanation is not a new experiment.

Once trigger, compatible setup and oracle are supported, use `write_candidate`. Leave budget for feedback and correction. Controller executes and Verifier accepts; neither a red test nor persuasive prose establishes reproduction.

## Correct the Earliest Evidenced Blocker

Read failed checks and referenced evidence. Semantic review is a claim, not an execution measurement; its first listed issue need not be the earliest blocker. Continue only when Controller returns to exploration.

| Evidence | Next investigation |
| --- | --- |
| Setup/import/helper failed before the target | Trace the original helper and caller values; fix candidate setup before target assertions. |
| Input absent from recorded consumed inputs | Check filename, discovery and initialization, rather than rewording assertions. |
| Completed test passed without the symptom | Recheck trigger conditions and the working example; passing alone does not prove branch non-execution. |
| Timeout, killed process or incomplete probe | Inspect the last recorded stage; narrow costly setup/scope when supported. The hypothesis is not disproved. |
| Unsupported assertion | Find original support or remove that independent check; do not substitute an unrelated failure or broad transcript search. |
| Publication/citation refused | Address the stated contract gap or exact displayed reference. |

If the same blocker recurs, compare actual inputs/setup with the prior attempt. Change investigation when the attempted adjustment did not address it. Do not stack guesses. Repeat unchanged execution only for an explicit purpose, such as checking nondeterminism, within budget. No fixed retry count or full investigation checklist is required.

## Preserve Product Boundaries

Use only registered tools and permitted candidate files. Do not invent shell/run/coverage tools, modify protected source/helpers, replace the target, install dependencies (including in candidate code), or fabricate its error. Unobserved stages and branch reachability remain unknown.

A terminal `ENVIRONMENT_BLOCKED` grants no further attempt. Use `request_information` for an essential fact unavailable through allowed tools. Use `revise_contract` only when new original evidence changes the interpretation, not to log progress, clear timeouts or redefine correctness. Permissions, budgets and completion checks remain authoritative. Experience is advice, not current evidence; hidden reference fixes and gold tests must not guide construction.
