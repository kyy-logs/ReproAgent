"""Shared, closed runtime conditions for evidence-backed advisory retrieval."""
from dataclasses import dataclass, asdict

from .core.serialization import parse_json

TOOLS = ("Read", "Grep", "Glob", "write_candidate", "revise_contract", "request_information")
PROTOCOL_CODES = ("MULTIPLE_TOOL_CALLS", "UNKNOWN_TOOL", "INVALID_TOOL_INPUT")
CITATION_CODES = ("SOURCE_RANGE_NOT_DISPLAYED", "SOURCE_HASH_MISMATCH")
ERROR_METADATA = "reproagent_error_code"


@dataclass(frozen=True, slots=True, order=True)
class ExperienceTrigger:
    kind: str
    tool: str
    code: str


def trigger_from_fact(fact):
    if not isinstance(fact, dict) or set(fact) != {"kind", "tool", "code"}:
        return None
    kind, tool, code = (fact[name] for name in ("kind", "tool", "code"))
    if not all(type(value) is str for value in (kind, tool, code)):
        return None
    valid = (kind == "tool_error" and tool == "revise_contract" and code in CITATION_CODES
        or kind == "permission" and tool in TOOLS and code in ("DENIED", "RESERVED_FOR_PUBLISHING")
        and (code != "RESERVED_FOR_PUBLISHING" or tool in ("Read", "Grep", "Glob"))
        or kind == "protocol_error" and tool == "" and code in PROTOCOL_CODES)
    return ExperienceTrigger(kind, tool, code) if valid else None


def checked_triggers(values):
    if not isinstance(values, (list, tuple)) or len(values) > 3:
        raise ValueError("invalid experience trigger list")
    result = []
    for value in values:
        trigger = trigger_from_fact(asdict(value) if isinstance(value, ExperienceTrigger) else value)
        if trigger is None or trigger in result:
            raise ValueError("invalid experience trigger")
        result.append(trigger)
    return tuple(sorted(result))


def evidence_triggers(entry):
    """Inspect a validated frozen atom, never the model's proposed explanation."""
    if not isinstance(entry, dict) or entry.get("kind") != "tool_error":
        return ()
    try:
        fact = parse_json(entry["text"])
    except (KeyError, ValueError, TypeError, RecursionError):
        return ()
    trigger = None
    if fact.get("kind") == "exploration.action":
        trigger = trigger_from_fact({"kind": "permission", "tool": fact.get("tool"), "code": fact.get("code")})
    elif fact.get("kind") == "exploration.tool_error":
        trigger = trigger_from_fact({"kind": "tool_error", "tool": fact.get("tool"), "code": fact.get("code")})
    elif fact.get("kind") == "exploration.protocol_error":
        trigger = trigger_from_fact({"kind": "protocol_error", "tool": "", "code": fact.get("code")})
    elif fact.get("state") == "error" and fact.get("link_state") == "matched":
        arguments = fact.get("arguments")
        if isinstance(arguments, dict) and "source_refs" in arguments:
            trigger = trigger_from_fact({"kind": "tool_error", "tool": fact.get("tool"),
                "code": fact.get("error_code")})
    return (trigger,) if trigger is not None else ()


def normative_tags(tags, triggers):
    """Reserve tool/error namespaces for program-derived facts only."""
    descriptive = [tag for tag in tags if not tag.strip().casefold().startswith(("tool:", "error:"))]
    keys = []
    for trigger in triggers:
        for key in ("tool:" + trigger.tool if trigger.tool else "", "error:" + trigger.code.casefold()):
            if key and key not in keys:
                keys.append(key)
    return list(dict.fromkeys(keys + descriptive))[:5]
