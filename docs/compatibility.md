# Compatibility evidence

| Tool runtime | Target runtime | pytest | Platform | Status |
| --- | --- | --- | --- | --- |
| CPython 3.12.14 | CPython 3.12.14 | 9.1.1 | Windows | Actual local tests: live parent/child cleanup, pytest Probe, src/conftest, export replay and installed wheel |
| CPython 3.12 | CPython 3.10 / 3.11 / 3.12 | 7.4.4 / 8.x / 9.x | Windows / Ubuntu | CI matrix executed once; all 18 jobs failed on environment coupling, fixed and reproduced locally, rerun not yet confirmed |

The tool requires Python >=3.11 because its domain enums use StrEnum. The standalone target
Probe uses Python's standard library plus pytest and does not require ReproAgent or HTTPX.
Python 3.10 is a target compatibility candidate, not a supported tool runtime.

Windows uses a suspended process joined to a Job Object before resume; closing/terminating
the job cleans up attached descendants. Unix uses a fresh session/process group; its branch
has not been executed in this local Windows session. Neither is a general hostile-code sandbox.
Windows symlink test is skipped when the account lacks symlink privilege; ordinary traversal
tests execute separately. Configured package/plugin behavior and external services can require
additional project-specific validation.
