import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

ROOT = Path(__file__).resolve().parents[2]
PS = shutil.which("pwsh") or shutil.which("powershell")


@pytest.mark.skipif(not PS or os.name != "nt", reason="Windows launcher requires PowerShell")
def test_round_launcher_checks_imports_before_loading_credentials_or_running_models(tmp_path):
    command = [PS, "-NoProfile", "-File", str(ROOT / "scripts/run-sdk-round.ps1"),
        "-ProjectRoot", str(ROOT), "-DataRoot", str(tmp_path / "no-credentials"), "-CheckOnly"]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf8")
    assert result.returncode == 0, result.stderr
    data = json.loads(result.stdout)
    assert Path(data["product_module"]).is_relative_to(ROOT / "src")
    assert Path(data["eval_module"]).is_relative_to(ROOT / "evals")
    assert data["git_commit"]
    wrong = subprocess.run(command + ["-ExpectedCommit", "0" * 40], capture_output=True, text=True, encoding="utf8")
    assert wrong.returncode != 0 and "SOURCE_COMMIT_MISMATCH" in wrong.stderr


@pytest.mark.skipif(not PS or os.name != "nt", reason="Windows launcher requires PowerShell")
def test_round_launcher_rejects_python_importing_another_checkout_before_credentials(tmp_path):
    other = Path("E:/ReproAgent/.venv/Scripts/python.exe")
    if ROOT == Path("E:/ReproAgent") or not other.is_file():
        pytest.skip("requires a separate main-checkout interpreter")
    result = subprocess.run([PS, "-NoProfile", "-File", str(ROOT / "scripts/run-sdk-round.ps1"),
        "-ProjectRoot", str(ROOT), "-Python", str(other), "-DataRoot", str(tmp_path / "no-credentials"), "-CheckOnly"],
        capture_output=True, text=True, encoding="utf8")
    assert result.returncode != 0 and "IMPORT_PROVENANCE_MISMATCH" in result.stderr
