# ReproAgent evaluation

SWT-Bench Lite tooling is now available through `python -m evals.swt_bench` from the repository root. See [the guide](../docs/swt-bench.md) for pinned data, the twenty-case development manifest, independent preflight, serial runs, test-patch predictions and external report provenance. No official Docker evaluation or new human correctness judgement has been completed. This development subset does not replace the preserved historical corpus below.

Current status: **20 distinct historical bugs across seven repositories have been evaluated**.
The final full repair round completes the pipeline and fresh buggy/fixed exported replays for **20/20**.
See `../docs/expanded-case-repairs.md`, `cases/expanded-final-results.json` and `cases/expanded-final-review.csv`.
The intermediate 18/20 repair round remains in `cases/expanded-repaired-results.json`.
Each repair round uses one attempt per case with unchanged inputs, commits, model, target environments and effective budgets.
Product source and prompts were frozen separately for each round. Results below describe the preserved earlier baseline.
The new frozen-code batch completes the live DeepSeek pipeline, fixed-version validation and
independent exported replay for **8/17** cases; **9/17 are EXHAUSTED**. See
`../docs/expanded-case-validation.md`, `cases/expanded-historical-results.json` and
`cases/expanded-review.csv`. All 17 preflight controls fail on buggy versions and pass on fixed versions;
these researcher-authored controls are excluded from Agent input and do not count as Agent successes.

Together with the prior three post-fix successes, the cumulative completion count is **11/20**,
recorded in `cases/cumulative-historical-results.json`. The prior three use their final post-fix run;
the new seventeen use their first attempt. This is not a twenty-case first-attempt benchmark.
See `../docs/historical-case-fixes.md` and `cases/historical-smoke-verified-results.json` for the older batch.
Codex has inspected the successful generated tests; independent human correctness review remains pending,
so `effective_reproductions` is null and no general reproduction rate is claimed.
All three researcher-authored pytest controls fail on buggy versions and pass on fixed versions.
The original three-case baseline's live failures are tool argument/schema errors, overly broad missing-information blockers,
and invalid contract field types. See `../docs/historical-case-validation.md` and
`cases/historical-smoke-results.json`. Product code and prompts were unchanged for this baseline.
The unchanged original 0/3 baseline remains available, together with the failed intermediate iteration
in `cases/historical-smoke-after-fixes-results.json`. These are hand-picked diagnostic cases, not a representative benchmark.
Unit/integration fixtures are synthetic and do not establish a reproduction rate.

`EvalCase` keeps the original issue/version/environment separate from fixed materials.
`generation_input` exposes only the allowed description and buggy repository. The Controller
consults the fixed version after the candidate has been frozen and independently replayed.
Preparation steps are documentation for the researcher to perform in disposable environments,
not shell commands executed automatically. `run_case` requires `review_status="approved"` and
can incur model charges; it is never invoked by the offline test suite.

Before approving a historical case, verify the public issue URL and content hash, buggy/fixed
commit IDs, runnable interpreters, setup instructions and actual symptom. Exclude repair patches
and new regression tests from the buggy input. An environment failure stays in the denominator.
Record model endpoint/ID, description hash, versions and budgets for every baseline comparison.

After generation, independently run the exported `replay.py` in a fresh buggy copy and record
`export_replayed`; manually inspect correctness and set `human_judgement`. Both are required
for effective reproduction rates. Unknown costs remain null, never zero. A Controller DONE
alone does not establish an independently reviewed evaluation success.

The selected 20-case corpus is complete. Representative larger samples, independent human review
and model comparisons remain pending. No general benchmark scores are published. `cases/review.csv`
and `cases/expanded-review.csv` record source and symptom checks by Codex, with independent human
judgement left unset. Each result retains the denominator, commit/input hashes, budgets and evidence.
For the original additional-seventeen baseline, product source and prompt hashes are identical between the previous successful build and that batch;
the new cases were locked before model calls, with no retries or prompt/budget changes after failures.
Descriptions are researcher-authored behavioral summaries/minimized inputs, not untouched issue text.
The target environment is prepared in advance and uses explicit source roots and a repro_tests directory;
this evaluates library bugs in runnable environments. It does not assess automatic environment repair.
