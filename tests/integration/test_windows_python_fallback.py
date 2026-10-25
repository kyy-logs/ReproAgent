"""Exercise the real official-download branch without downloading/executing code."""
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(os.name != 'nt', reason='Windows Python fallback')


def test_official_fallback_uses_runtime_manifest_and_rejects_unsigned_payload(tmp_path):
    root = tmp_path / 'installer'
    scripts = root / 'scripts'
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / 'scripts/install-windows.ps1', scripts / 'install-windows.ps1')
    expected = 'https://www.python.org/ftp/python/3.13.17/python-3.13.17-amd64.exe'
    (scripts / 'python-runtime.json').write_text(json.dumps({
        'version': '3.13.17', 'installer_url': expected,
        'winget_id': 'Python.Python.3.13', 'install_directory': 'Python313',
    }), encoding='utf-8')
    receipt = tmp_path / 'download.json'
    executed = tmp_path / 'executed.txt'
    private_temp = tmp_path / 'temp'
    private_temp.mkdir()
    driver = tmp_path / 'driver.ps1'
    driver.write_text('''
$ErrorActionPreference = 'Stop'
function global:Invoke-WebRequest {
    param([switch]$UseBasicParsing, [string]$Uri, [string]$OutFile)
    @{uri=$Uri} | ConvertTo-Json -Compress | Set-Content -LiteralPath $env:FALLBACK_RECEIPT
    [System.IO.File]::WriteAllText($OutFile, 'unsigned fixture, not an executable')
}
function global:Get-AuthenticodeSignature {
    param([string]$LiteralPath)
    [pscustomobject]@{ Status='NotSigned'; SignerCertificate=$null }
}
function global:Start-Process {
    [System.IO.File]::WriteAllText($env:FALLBACK_EXECUTED, 'executed')
    throw 'An unsigned fixture must never be executed'
}
& $env:FALLBACK_SCRIPT -NoPause
exit $LASTEXITCODE
''', encoding='ascii')
    environment = dict(os.environ, FALLBACK_RECEIPT=str(receipt),
                       FALLBACK_EXECUTED=str(executed), TEMP=str(private_temp), TMP=str(private_temp),
                       FALLBACK_SCRIPT=str(scripts / 'install-windows.ps1'),
                       PATH=r'C:\Windows\System32;C:\Windows\System32\WindowsPowerShell\v1.0')
    for name in list(environment):
        if name.startswith('REPROAGENT_TEST_'):
            environment.pop(name)
    result = subprocess.run(['powershell.exe', '-NoProfile', '-ExecutionPolicy', 'Bypass',
                             '-File', str(driver)], cwd=root, env=environment,
                            capture_output=True, timeout=30)
    assert result.returncode == 2, (result.stdout, result.stderr)
    assert json.loads(receipt.read_text(encoding='utf-8-sig'))['uri'] == expected
    assert not (root / '.local').exists()
    assert not executed.exists(), 'An unsigned downloaded payload must not be started'
