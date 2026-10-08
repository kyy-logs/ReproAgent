[CmdletBinding(DefaultParameterSetName = 'Run')]
param(
    [Parameter(Mandatory = $true, ParameterSetName = 'Run')]
    [string]$TaskConfig,
    [Parameter(Mandatory = $true, ParameterSetName = 'Inspect')]
    [string]$Inspect,
    [Parameter(Mandatory = $true, ParameterSetName = 'Check')]
    [switch]$Check,
    [string]$ModelConfig,
    [string]$ToolPython,
    [Parameter(ParameterSetName = 'Run')]
    [string]$FixedRepo,
    [Parameter(ParameterSetName = 'Run')]
    [string]$FixedPython,
    [Parameter(ParameterSetName = 'Run')]
    [ValidateSet('native', 'agentscope')]
    [string]$ModelBackend,
    [Parameter(ParameterSetName = 'Run')]
    [ValidateSet('native', 'agentscope')]
    [string]$AgentBackend
)

$ErrorActionPreference = 'Stop'
$reproRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
if (-not $ToolPython) { $ToolPython = Join-Path $reproRoot '.venv/Scripts/python.exe' }
if (-not $ModelConfig) { $ModelConfig = Join-Path $reproRoot 'examples/model.deepseek.json' }
$reproKeyFile = Join-Path $reproRoot '.local/deepseek.key'
$reproExit = 2
$reproLoadedKey = $false
$reproPreviousEncoding = $env:PYTHONIOENCODING
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding
$OutputEncoding = [Console]::OutputEncoding
try {
    $env:PYTHONIOENCODING = 'utf-8'
    if (-not (Test-Path -LiteralPath $ToolPython -PathType Leaf)) {
        throw 'ReproAgent tool Python is missing. Install the project in .venv or pass -ToolPython.'
    }
    if ($Inspect) {
        & $ToolPython -m reproagent inspect $Inspect
        $reproExit = $LASTEXITCODE
    } else {
        $reproModel = Get-Content -LiteralPath $ModelConfig -Raw | ConvertFrom-Json
        $reproKeyName = [string]$reproModel.api_key_env
        if ($reproKeyName -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw 'Invalid model API key environment variable name.' }
        $reproPreviousKey = [Environment]::GetEnvironmentVariable($reproKeyName, 'Process')
        if ($Check) {
            $reproCredentialSource = 'missing'
            if ($reproPreviousKey) { $reproCredentialSource = 'environment' }
            elseif ($reproKeyName -eq 'DEEPSEEK_API_KEY' -and (Test-Path -LiteralPath $reproKeyFile -PathType Leaf)) {
                $reproCredentialSource = 'local-encrypted-file'
            }
            & $ToolPython -c 'import json,sys,reproagent.cli; print(json.dumps(dict(cli_available=True, tool_python=sys.executable)))'
            if ($LASTEXITCODE -ne 0) { throw 'ReproAgent CLI import failed. Install reproagent-local into the tool interpreter.' }
            [Console]::Error.WriteLine('Credential source: ' + $reproCredentialSource + ' (not authenticated by check)')
            $reproExit = 0
        } else {
            if (-not $reproPreviousKey -and $reproKeyName -eq 'DEEPSEEK_API_KEY' -and (Test-Path -LiteralPath $reproKeyFile -PathType Leaf)) {
                try {
                    # A PowerShell 7 parent can pass its module paths to Windows PowerShell 5.
                    Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security')
                    $reproSecure = ConvertTo-SecureString ((Get-Content -LiteralPath $reproKeyFile -Raw).Trim())
                    $reproCredential = [System.Net.NetworkCredential]::new('', $reproSecure)
                    [Environment]::SetEnvironmentVariable($reproKeyName, $reproCredential.Password, 'Process')
                    $reproLoadedKey = $true
                } catch { throw 'Unable to load the local encrypted credential. Set the configured API key environment variable in the launching terminal.' }
            }
            $reproArgs = @('-m', 'reproagent', 'run', '--config', $TaskConfig, '--model-config', $ModelConfig)
            # Both backends are one infrastructure now, so the launcher selects nothing of
            # its own: an old command that names one is forwarded as the deprecated alias
            # it is and the CLI says so, and a new command leaves the choice out entirely.
            if ($ModelBackend) { $reproArgs += @('--model-backend', $ModelBackend) }
            if ($AgentBackend) { $reproArgs += @('--agent-backend', $AgentBackend) }
            if ($ModelBackend -or $AgentBackend) {
                [Console]::Error.WriteLine('-ModelBackend/-AgentBackend are deprecated and no longer select a runtime: ReproAgent runs on the AgentScope infrastructure only.')
            }
            if ($FixedRepo) { $reproArgs += @('--fixed-repo', $FixedRepo) }
            if ($FixedPython) { $reproArgs += @('--fixed-python', $FixedPython) }
            & $ToolPython @reproArgs
            $reproExit = $LASTEXITCODE
        }
    }
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    $reproExit = 2
} finally {
    if ($reproLoadedKey) { [Environment]::SetEnvironmentVariable($reproKeyName, $reproPreviousKey, 'Process') }
    if ($null -eq $reproPreviousEncoding) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue }
    else { $env:PYTHONIOENCODING = $reproPreviousEncoding }
}
exit $reproExit
