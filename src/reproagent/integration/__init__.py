"""User-level AI tool integration for ReproAgent.

This package installs and runs the thin entry points that let Claude Code and
Codex call the existing ReproAgent CLI.  It deliberately imports no core,
adapter or AgentScope module: installing an entry point must not assemble the
reproduction runtime.
"""

__all__ = []
