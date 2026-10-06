import importlib
import json
from dataclasses import replace

import pytest


def test_rejects_duplicate_keys_unknown_version_and_nonfinite_numbers():
    serial = importlib.import_module("reproagent.core.serialization")
    for text in ['{"schema_version":1,"schema_version":2}', '{"value":NaN}', '{"value":Infinity}']:
        with pytest.raises(ValueError):
            serial.parse_json(text)
    with pytest.raises(ValueError, match="schema_version"):
        serial.decode_record("task", {"schema_version": 999})
    models = importlib.import_module("reproagent.core.models")
    with pytest.raises(ValueError, match="agent_steps"):
        models.BudgetLimits(agent_steps=-1)


def test_manifest_hash_is_stable_and_sensitive_to_install_path():
    serial = importlib.import_module("reproagent.core.serialization")
    assert serial.canonical_hash({"path": "tests/a.py", "size": 1}) == serial.canonical_hash({"size": 1, "path": "tests/a.py"})
    assert serial.canonical_hash({"path": "tests/a.py"}) != serial.canonical_hash({"path": "tests/nested/a.py"})


def test_contract_round_trip_and_frozen_values():
    models = importlib.import_module("reproagent.core.models")
    serial = importlib.import_module("reproagent.core.serialization")
    source = models.SourceRef(path="issue.md", content_hash="abc", start_line=1, end_line=1, kind="issue")
    contract = models.IssueContract(contract_id="contract-1", expected="empty list", sources=(source,))
    assert serial.decode_record("contracts", serial.encode_record(contract)) == contract
    with pytest.raises(AttributeError):
        contract.expected = "something else"
    assert serial.encode_record(replace(contract, version=2))["version"] == 2


def test_default_limits_survive_request_round_trip(tmp_path):
    models = importlib.import_module("reproagent.core.models")
    serial = importlib.import_module("reproagent.core.serialization")
    request = models.TaskRequest(repo=tmp_path, output_dir=tmp_path / "out", issue_file=tmp_path / "issue.md")
    decoded = serial.decode_record("request", serial.encode_record(request))
    assert decoded == request
    assert decoded.repo == tmp_path
    assert decoded.limits.command_timeout_seconds == 60
    assert decoded.limits.model_cost_limit is None


def test_runtime_context_cannot_be_persisted():
    models = importlib.import_module("reproagent.core.models")
    serial = importlib.import_module("reproagent.core.serialization")
    with pytest.raises(TypeError):
        serial.encode_record(models.CallContext(budget=object()))
