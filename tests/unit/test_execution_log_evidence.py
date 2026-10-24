"""Diagnostic log excerpts are bounded original-run data, never source evidence."""
from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from reproagent.core.models import EvidenceRef
from reproagent.core.serialization import bytes_hash, canonical_bytes
from reproagent.core.verifier import Verifier
from reproagent.store import TaskStore


def log_run(tmp_path, stdout="", stderr=""):
    root = tmp_path / "task"
    run_id = "run-test"
    refs = {}
    for stream, text in (("stdout", stdout), ("stderr", stderr)):
        path = root / "runs" / run_id / (stream + ".log")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
        refs[stream] = EvidenceRef(path.relative_to(root).as_posix(),
                                  bytes_hash(path.read_bytes()), 1, max(1, len(text.splitlines())))
    raw = SimpleNamespace(stdout_ref=refs["stdout"], stderr_ref=refs["stderr"], log_truncated=False)
    return SimpleNamespace(run_id=run_id, execution_role="original", raw=raw), Verifier(TaskStore(root), None, None)


def collect(run, verifier, **kwargs):
    from reproagent.core.execution_log_evidence import execution_log_evidence
    return execution_log_evidence(run, read_ref=verifier.resolve, **kwargs)


def test_original_warning_and_import_exception_keep_exact_checked_line_ranges(tmp_path):
    line = "\x1b[91mWARNING: autodoc: failed to import class 'Missing'\x1b[0m"
    run, verifier = log_run(tmp_path, "build started\n" + line + "\ntrace follows\n",
                            "AttributeError: module 'target' has no attribute 'Missing'\n")
    evidence = collect(run, verifier)
    payload = evidence.payload()
    assert payload["execution_role"] == "original"
    assert payload["kind"] == "execution_log"
    assert len(payload["signals"]) == 2
    for signal in payload["signals"]:
        ref = EvidenceRef(**{k: signal[k] for k in ("path", "content_hash", "start_line", "end_line")})
        lines = verifier.resolve(ref).splitlines()
        assert signal["text"] == "\n".join(lines[ref.start_line - 1:ref.end_line])
    assert line in [s["text"] for s in payload["signals"]]


def test_plain_output_and_source_echo_are_not_warning_signals(tmp_path):
    run, verifier = log_run(tmp_path, 'build done\n    print("WARNING: autodoc: failed to import class")\n'
                            'value = "ImportError: not a real exception"\n')
    assert collect(run, verifier) is None


@pytest.mark.parametrize("change", ["hash", "other_run", "absolute", "missing", "encoding"])
def test_unavailable_log_is_marked_without_disclosing_unchecked_content(tmp_path, change):
    run, verifier = log_run(tmp_path, "WARNING: private canary\n")
    ref = run.raw.stdout_ref
    path = verifier.store.root / ref.path
    if change == "hash":
        path.write_text("WARNING: altered canary\n", encoding="utf-8")
    elif change == "other_run":
        run.raw.stdout_ref = replace(ref, path="runs/run-other/stdout.log")
    elif change == "absolute":
        run.raw.stdout_ref = replace(ref, path=str(path.resolve()))
    elif change == "missing":
        path.unlink()
    else:
        path.write_bytes(b"WARNING: \xff")
        run.raw.stdout_ref = replace(ref, content_hash=bytes_hash(path.read_bytes()))
    evidence = collect(run, verifier)
    payload = evidence.payload()
    assert payload["signals"] == []
    assert payload["unavailable_streams"] == ["stdout"]
    assert "canary" not in json.dumps(payload)


def test_fixed_run_is_not_read_even_when_it_contains_the_only_warning(tmp_path):
    run, verifier = log_run(tmp_path, "WARNING: fixed-only canary\n")
    run.execution_role = "fixed"
    def refused(ref):
        pytest.fail("fixed logs must never be read")
    from reproagent.core.execution_log_evidence import execution_log_evidence
    assert execution_log_evidence(run, read_ref=refused) is None


def test_signal_budget_omits_whole_lines_and_preserves_an_explicit_count(tmp_path):
    run, verifier = log_run(tmp_path, "WARNING: " + "汉" * 3000 + "\nImportError: missing_optional\n")
    evidence = collect(run, verifier, max_bytes=700)
    payload = evidence.payload()
    assert len(canonical_bytes(payload)) <= 700
    assert payload["omitted_signals"] == 1
    assert payload["signals"][0]["text"] == "ImportError: missing_optional"
    assert "汉" not in json.dumps(payload)


def test_duplicates_do_not_consume_signal_budget(tmp_path):
    run, verifier = log_run(tmp_path, "WARNING: duplicate\n" * 30)
    payload = collect(run, verifier).payload()
    assert len(payload["signals"]) == 1
    assert payload["omitted_signals"] == 0


def test_feedback_redacts_logs_and_retains_them_ahead_of_optional_probe_excerpts(tmp_path):
    from reproagent.core.models import Verdict, VerificationIssue
    from reproagent.core.verification_feedback import render_verification_feedback
    run, verifier = log_run(tmp_path, "WARNING: missing class secret-value\n")
    logs = collect(run, verifier)
    verdict = Verdict(None, "unconfirmed", issues=(VerificationIssue(
        "TARGET_TRIGGER_UNCONFIRMED", "target_triggered", "provider_claim", "target unconfirmed"),))
    evidence = ({"path": "probe.jsonl", "content_hash": "h", "start_line": 1,
                 "end_line": 1, "text": "large evidence " * 400},)
    text = render_verification_feedback(verdict, result_code="INVALID_CANDIDATE",
        byte_limit=2048, evidence=evidence, log_evidence=(logs,), secrets=("secret-value",))
    data = json.loads(text)
    assert len(text.encode()) <= 2048 and "secret-value" not in text
    assert "[REDACTED]" in data["execution_logs"][0]["signals"][0]["text"]
    assert data["projection"]["omitted_evidence"] == 1
    assert data["verdict"]["issues"][0]["code"] == "TARGET_TRIGGER_UNCONFIRMED"


def test_raw_log_reference_default_range_does_not_hide_later_verified_lines(tmp_path):
    run, verifier = log_run(tmp_path, "build started\nnormal output\nWARNING: missing input\n")
    run.raw.stdout_ref = replace(run.raw.stdout_ref, end_line=1)
    payload = collect(run, verifier).payload()
    assert payload["signals"][0]["start_line"] == 3
    assert payload["signals"][0]["text"] == "WARNING: missing input"


@pytest.mark.parametrize("prefix", ["/tmp/docs/index.rst:3: ", r"C:\docs\index.rst:3: "])
def test_document_location_prefix_is_kept_as_a_real_warning(tmp_path, prefix):
    run, verifier = log_run(tmp_path, prefix + "WARNING: autodoc: failed to import class 'Missing'\n")
    assert collect(run, verifier).payload()["signals"][0]["text"].startswith(prefix)


def test_optional_log_envelope_cannot_overflow_an_otherwise_fitting_feedback(tmp_path):
    from reproagent.core.models import Verdict
    from reproagent.core.verification_feedback import render_verification_feedback
    run, verifier = log_run(tmp_path, "WARNING: unavailable\n")
    (verifier.store.root / run.raw.stdout_ref.path).unlink()
    logs = collect(run, verifier)
    verdict = Verdict(None, "unconfirmed")
    baseline = render_verification_feedback(verdict, result_code="INVALID_CANDIDATE", byte_limit=8192)
    text = render_verification_feedback(verdict, result_code="INVALID_CANDIDATE",
        byte_limit=len(baseline.encode()), log_evidence=(logs,))
    assert len(text.encode()) <= len(baseline.encode())
    assert json.loads(text)["projection"]["omitted_details"] >= 1
