# Compatibility evidence

| Tool runtime | Target runtime | pytest | Platform | Status |
| --- | --- | --- | --- | --- |
| CPython 3.12.14 | CPython 3.12.14 | 9.1.1 | Windows | Actual local tests: live parent/child cleanup, pytest Probe, src/conftest, export replay and installed wheel; the whole SDK chain (Glob → Grep → Read → write_candidate → execute → verify → repeat → fixed version → export) runs over a mock transport |
| CPython 3.12 | CPython 3.10 / 3.11 / 3.12 | 7.4.4 / 8.x / 9.x | Windows / Ubuntu | CI matrix passes 18/18 (Windows 245 passed; Ubuntu 240 passed, 5 Windows-only launcher tests skipped), AgentScope SDK modules and the SWT chain included; the first run failed on environment coupling, see docs/implementation-status.md |

`agentscope==2.0.9` is a main dependency, so it is installed by the plain `pip install -e ".[dev]"`
the README and CI use; the `[agentscope]` extra is empty and kept only for one deprecation cycle.
A test environment built without the SDK therefore fails to import the SDK test modules instead of
silently skipping them, which is the same install error the product itself raises.

The tool requires Python >=3.11 because its domain enums use StrEnum. The standalone target
Probe uses Python's standard library plus pytest and does not require ReproAgent or HTTPX.
Python 3.10 is a target compatibility candidate, not a supported tool runtime.

Windows uses a suspended process joined to a Job Object before resume; closing/terminating
the job cleans up attached descendants. Unix uses a fresh session/process group; its branch
has not been executed in this local Windows session. Neither is a general hostile-code sandbox.
Windows symlink test is skipped when the account lacks symlink privilege; ordinary traversal
tests execute separately. Configured package/plugin behavior and external services can require
additional project-specific validation.
