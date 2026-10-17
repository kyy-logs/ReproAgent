# Preflight provenance before reading credentials or starting a paid model run.
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot),
    [string]$DataRoot = '',
    [string]$Python = '',
    [string]$Manifest = '',
    [string]$Bindings = '',
    [string]$Output = '',
    [string]$Name = 'reproagent-sdk-disabled',
    [string]$ModelConfig = '',
    [string]$ExpectedCommit = '',
    [switch]$CheckOnly
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
if (-not $DataRoot) { $DataRoot = $ProjectRoot }
$DataRoot = [System.IO.Path]::GetFullPath($DataRoot)
if (-not $Python) { $Python = Join-Path $ProjectRoot '.venv/Scripts/python.exe' }
if (-not $ModelConfig) { $ModelConfig = Join-Path $ProjectRoot 'examples/model.deepseek.non-thinking.json' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) { throw 'PYTHON_NOT_FOUND' }
$oldKey = $env:DEEPSEEK_API_KEY
$oldPath = $env:PATH
Push-Location -LiteralPath $ProjectRoot
try {
    $probe = @'
import json, sys
from pathlib import Path
import reproagent, evals
root = Path(sys.argv[1]).resolve()
product = Path(reproagent.__file__).resolve()
evaluation = Path(evals.__file__).resolve()
if not product.is_relative_to(root / 'src') or not evaluation.is_relative_to(root / 'evals'):
    print('IMPORT_PROVENANCE_MISMATCH: interpreter imports another checkout', file=sys.stderr)
    raise SystemExit(3)
from evals.swt_bench.provenance import capture_tool_source
source = capture_tool_source(root)
print(json.dumps({'project_root':str(root), 'python':sys.executable,
                  'product_module':str(product), 'eval_module':str(evaluation),
                  'source_hash':source['source_hash'], 'git_dirty_count':source['git_dirty_count']}))
'@
    $probeOutput = & $Python -X utf8 -c $probe $ProjectRoot
    if ($LASTEXITCODE -ne 0) { throw 'IMPORT_PROVENANCE_MISMATCH' }
    $provenance = $probeOutput | ConvertFrom-Json
    $commit = & git -C $ProjectRoot rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'SOURCE_COMMIT_UNAVAILABLE' }
    $commit = $commit.Trim()
    if ($ExpectedCommit -and ($ExpectedCommit.Length -lt 7 -or -not $commit.StartsWith($ExpectedCommit))) {
        throw 'SOURCE_COMMIT_MISMATCH'
    }
    $provenance | Add-Member -NotePropertyName git_commit -NotePropertyValue $commit
    if ($CheckOnly) {
        $provenance | ConvertTo-Json -Compress
        exit 0
    }
    foreach ($item in @($Manifest, $Bindings, $Output)) {
        if (-not $item) { throw 'Manifest, Bindings and Output are required for a live run' }
    }
    foreach ($item in @($Manifest, $Bindings, $ModelConfig)) {
        if (-not (Test-Path -LiteralPath $item -PathType Leaf)) { throw 'RUN_INPUT_NOT_FOUND' }
    }
    Write-Output ('provenance: ' + ($provenance | ConvertTo-Json -Compress))
    $keyPath = Join-Path $DataRoot '.local/deepseek.key'
    $secure = ConvertTo-SecureString ((Get-Content -LiteralPath $keyPath -Raw).Trim())
    $env:DEEPSEEK_API_KEY = ([System.Net.NetworkCredential]::new('', $secure)).Password
    $env:PATH = (Join-Path $DataRoot '.local/bin') + [System.IO.Path]::PathSeparator + $oldPath
    & $Python -X utf8 -u -m evals.swt_bench run `
        --catalog (Join-Path $DataRoot '.local/swt-bench/data/catalog.json') `
        --manifest $Manifest --bindings $Bindings --model-config $ModelConfig --name $Name --output $Output
    exit $LASTEXITCODE
}
finally {
    $env:DEEPSEEK_API_KEY = $oldKey
    $env:PATH = $oldPath
    Pop-Location
}
