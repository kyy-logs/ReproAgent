[CmdletBinding()]
param(
    [string]$Python,
    [ValidateSet('all', 'claude-code', 'codex')]
    [string]$Target = 'all',
    [string]$ModelConfig,
    [switch]$Force,
    [switch]$Pause,
    [switch]$NoPause,
    [string]$Project,
    [string]$ProjectPython,
    [string]$TaskConfig,
    [switch]$CollectOnly,
    [switch]$NoPythonInstall
)

$ErrorActionPreference = 'Stop'
$installExit = 2
try {
    # Ignore an unrelated project's Python import settings for this process only.
    Remove-Item Env:PYTHONPATH -ErrorAction SilentlyContinue
    Remove-Item Env:PYTHONHOME -ErrorAction SilentlyContinue

    $pythonVersion = '3.12.14'
    $pythonInstallerUrl = "https://www.python.org/ftp/python/$pythonVersion/python-$pythonVersion-amd64.exe"

    function Test-PythonCandidate([string]$exe, [string[]]$prefixArgs = @()) {
        $probe = 'import json,sys; sys.exit(1) if sys.version_info < (3,11) else print(json.dumps(sys.executable,ensure_ascii=True))'
        $probeArgs = @($prefixArgs) + @('-c', $probe)
        $previous = $ErrorActionPreference
        $ErrorActionPreference = 'Continue'
        try {
            $found = & $exe @probeArgs 2>$null
            $probeExit = $LASTEXITCODE
        } catch {
            $found = $null
            $probeExit = 1
        } finally {
            $ErrorActionPreference = $previous
        }
        if ($probeExit -ne 0 -or -not $found) { return $null }
        try { $decoded = $found | ConvertFrom-Json } catch { return $null }
        if ($decoded -is [string] -and (Test-Path -LiteralPath $decoded -PathType Leaf)) {
            return $decoded
        }
        return $null
    }

    function Find-InstalledPython {
        $roots = @()
        if ($env:LOCALAPPDATA) {
            $roots += Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\python.exe'
        }
        if ($env:ProgramFiles) {
            $roots += Join-Path $env:ProgramFiles 'Python312\python.exe'
        }
        if (${env:ProgramFiles(x86)}) {
            $roots += Join-Path ${env:ProgramFiles(x86)} 'Python312\python.exe'
        }
        foreach ($path in $roots) {
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $found = Test-PythonCandidate $path @()
                if ($found) { return $found }
            }
        }
        $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($launcher) { return Test-PythonCandidate $launcher.Source @('-3') }
        return $null
    }

    function Install-PythonWithWinget {
        $winget = $env:REPROAGENT_TEST_WINGET
        if (-not $winget) {
            $command = Get-Command winget.exe -CommandType Application -ErrorAction SilentlyContinue
            if (-not $command) { return $false }
            $winget = $command.Source
        }
        try {
            & $winget install --id Python.Python.3.12 -e --source winget --silent `
                --disable-interactivity --accept-package-agreements --accept-source-agreements
            return ($LASTEXITCODE -eq 0)
        } catch {
            return $false
        }
    }

    function Install-PythonOfficial {
        $installer = $env:REPROAGENT_TEST_INSTALLER_PATH
        $downloaded = $false
        if (-not $installer) {
            $installer = Join-Path $env:TEMP "reproagent-python-$pythonVersion-amd64.exe"
            try {
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                Invoke-WebRequest -UseBasicParsing -Uri $pythonInstallerUrl -OutFile $installer
                $downloaded = $true
            } catch {
                return $false
            }
        }
        try {
            try {
                $signature = Get-AuthenticodeSignature -LiteralPath $installer
            } catch {
                return $false
            }
            if ($null -eq $signature.SignerCertificate -or
                $signature.Status -ne 'Valid' -or
                $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation') {
                return $false
            }
            $process = Start-Process -FilePath $installer -Wait -PassThru -ArgumentList @(
                '/quiet', 'InstallAllUsers=0', 'PrependPath=0', 'Include_pip=1', 'Include_launcher=1')
            return ($process.ExitCode -eq 0 -or $process.ExitCode -eq 3010)
        } catch {
            return $false
        } finally {
            if ($downloaded -and (Test-Path -LiteralPath $installer)) {
                Remove-Item -LiteralPath $installer -Force -ErrorAction SilentlyContinue
            }
        }
    }

    $repoRoot = Split-Path -Parent $PSScriptRoot
    $candidates = @()
    if ($Python) {
        $candidates += [pscustomobject]@{ Exe = $Python; Args = @() }
    } else {
        $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($launcher) {
            $candidates += [pscustomobject]@{ Exe = $launcher.Source; Args = @('-3') }
        }
        foreach ($name in @('python.exe', 'python3.exe')) {
            $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue
            if ($command -and $command.Source -notlike '*\Microsoft\WindowsApps\*') {
                $candidates += [pscustomobject]@{ Exe = $command.Source; Args = @() }
            }
        }
        foreach ($relative in @('.venv\Scripts\python.exe', 'venv\Scripts\python.exe',
                                '.local\install-env\Scripts\python.exe')) {
            $path = Join-Path $repoRoot $relative
            if (Test-Path -LiteralPath $path -PathType Leaf) {
                $candidates += [pscustomobject]@{ Exe = $path; Args = @() }
            }
        }
    }

    $selected = $null
    foreach ($candidate in $candidates) {
        $selected = Test-PythonCandidate $candidate.Exe $candidate.Args
        if ($selected) { break }
    }

    $explicitFailed = $false
    if ($Python -and -not $selected) {
        Write-Host '[ERROR] Python 3.11+ was not found at the path given with -Python.'
        Write-Host 'Install Python from https://www.python.org/downloads/windows/ with the Python launcher or PATH option.'
        $explicitFailed = $true
        $installExit = 2
    }

    if (-not $selected -and -not $explicitFailed -and -not $NoPythonInstall) {
        Write-Host '[1/5] Python 3.11+ not found; trying automatic installation...'
        $autoSelected = $null
        $autoInstalled = Install-PythonWithWinget
        if ($autoInstalled) {
            if ($env:REPROAGENT_TEST_MODE -eq '1' -and $env:REPROAGENT_TEST_PYTHON_AFTER_INSTALL) {
                $autoSelected = $env:REPROAGENT_TEST_PYTHON_AFTER_INSTALL
            } else {
                $autoSelected = Find-InstalledPython
            }
        }
        if (-not $autoSelected) {
            $autoInstalled = Install-PythonOfficial
            if ($autoInstalled) {
                if ($env:REPROAGENT_TEST_MODE -eq '1' -and $env:REPROAGENT_TEST_PYTHON_AFTER_INSTALL) {
                    $autoSelected = $env:REPROAGENT_TEST_PYTHON_AFTER_INSTALL
                } else {
                    $autoSelected = Find-InstalledPython
                }
            }
        }
        if ($autoSelected -and (Test-Path -LiteralPath $autoSelected -PathType Leaf)) {
            $selected = $autoSelected
        }
    }

    if ($selected) {
        $installArgs = @((Join-Path $PSScriptRoot 'install.py'), '--host', $Target)
        if ($ModelConfig) { $installArgs += @('--model-config', $ModelConfig) }
        if ($Force) { $installArgs += '--force' }
        if ($Project) { $installArgs += @('--project', $Project) }
        if ($ProjectPython) { $installArgs += @('--project-python', $ProjectPython) }
        if ($TaskConfig) { $installArgs += @('--task-config', $TaskConfig) }
        if ($CollectOnly) { $installArgs += '--collect-only' }
        & $selected @installArgs
        $installExit = $LASTEXITCODE
    } elseif (-not $explicitFailed) {
        if ($NoPythonInstall) {
            Write-Host '[ERROR] Python 3.11+ was not found and -NoPythonInstall was set.'
        } else {
            Write-Host '[ERROR] Python 3.11+ was not found and automatic installation failed.'
        }
        Write-Host 'Install Python from https://www.python.org/downloads/windows/ with the Python launcher or PATH option.'
        Write-Host 'Then double-click install.cmd again. Advanced: install.cmd -Python C:\path\python.exe'
    }
} catch {
    Write-Host '[ERROR] Could not start the installer. Check Python and the extracted source folder.'
} finally {
    if ($Pause -and -not $NoPause) {
        Read-Host 'Press Enter to close this window' | Out-Null
    }
}
exit $installExit
