# Legacy Sphinx tox dependency repair

This opt-in profile is for the pinned SWT Sphinx container layout, using Python 3.9,
tox 4.16.0 and `/opt/miniconda3/envs/testbed`. It is not a ReproAgent dependency and
does not change the default official harness or an existing round's score.

## Cause and scope

For Sphinx 8627, the parent environment has setuptools 80.9.0 and can import
`pkg_resources`. However, virtualenv 21.14.5 seeds tox's fresh `py39` environment
with setuptools 82.0.1, without `pkg_resources`. The project's unconstrained
`setuptools` dependency leaves that seeded version installed. Pytest then fails
while importing `sphinx.testing.fixtures`, before either candidate test executes.

`constraints.txt` pins setuptools to the already recorded parent version.
The `tox` wrapper applies that constraint to **tox's test-environment package
installation**, not just the parent interpreter. It forwards the original tox
arguments and exit status. The separate `.pkg` build environment is unchanged;
this is a targeted runtime-import repair, not a complete dependency lock.

## Use in a disposable evaluation container

Keep the original image, frozen candidate, evaluation scripts and logs. Mount:

| Host file (absolute path) | Read-only container target |
| --- | --- |
| this directory's `tox` | `/opt/miniconda3/envs/testbed/bin/tox` |
| this directory's `constraints.txt` | `/opt/reproagent-repair/constraints.txt` |
| frozen pre or post `eval.sh` | `/reproagent-eval.sh` |

Run the container with `/bin/bash /reproagent-eval.sh`. Use the **same image and
profile in both arms**. The wrapper requires executable permission, stored in Git;
its LF line endings are protected by `.gitattributes`. No host repository needs
to be mounted writable. Dependency installation may access the package index.

For a container where these files are already available, the equivalent command is:

```sh
/opt/miniconda3/envs/testbed/bin/python -m tox \
  -x 'testenv.install_command=python -I -m pip install --constraint /opt/reproagent-repair/constraints.txt {opts} {packages}' \
  -epy39 -v -- tests/test_repro.py
```

Record the full image ID, profile/script/prediction hashes, actual installed
versions and complete logs in a **new environment-repair diagnostic run**. Verify
that pytest reached the test bodies, not merely that the container exited.
Do not import these modified-environment results as a previously verified official
round or silently change the denominator.

## Frozen-script rollback defect and explicit derivative

The frozen 8627 candidate was replayed in two fresh containers on 2026-10-14:
original: **2 failed** at the intended assertions; patched: **2 passed**. The
`pkg_resources` import failure disappeared. This establishes local differential
execution for this dependency profile; it is not a new official score.

Both original frozen evaluation scripts then fail their final rollback command:
`git apply /root/pre_state.patch` reports `unrecognized input` and exits 128.
Their initial `git diff HEAD <base>` compares commits and omits the working-tree
setup adjustments. Also, `git checkout <base>` does not discard the applied
working-tree patch. Merely ignoring an empty patch would not restore the source.

The explicit diagnostic helper derives a **new** script without changing the
frozen file or pinned harness:

```sh
python -m evals.repairs.pinned_sphinx_script \
  --input frozen-eval.sh --output diagnostic-eval.sh \
  --base-commit 332d80ba8433aea41c3709fa52737ede4405072b
```

It accepts only the recorded Sphinx unit-test layout, checks the base HEAD,
captures the initial tracked working-tree diff, runs the unchanged test command,
then restores tracked files and reapplies the initial diff only when nonempty.
It compares the restored diff to the original and emits `ROLLBACK_FAILED` / 125
if cleanup fails. Successful cleanup preserves the **test's own exit code**;
test failures are not turned into exit 0. Patch heredocs are unchanged.

Mount the derivative at `/reproagent-eval.sh` for a separate diagnostic round.
The generated script resets `/testbed`; that directory must be in the writable
layer of a fresh disposable container, never a host source bind mount. New
untracked candidate files disappear when that container is discarded. This
helper is not an in-place cleanup tool for reusable repositories.

Neither the dependency profile nor the derivative creates an official execution
receipt. New official grading still needs a separately versioned harness repair
and verified provenance. Existing official results remain unchanged.
