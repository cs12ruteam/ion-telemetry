# Run in Windows PowerShell. -Mode Startup registers the already-built app.
[CmdletBinding()]
param(
    [ValidateSet('Build', 'Startup', 'BuildAndStartup')]
    [string] $Mode = 'Build',

    [string] $ProjectRoot = $PSScriptRoot
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($env:OS -ne 'Windows_NT') {
    throw 'Run this script on the Windows PC with the Turing display.'
}

$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$dist = Join-Path $ProjectRoot 'dist-ion'
$bundle = Join-Path $dist 'turing-system-monitor'
$main = Join-Path $bundle 'main.exe'
$theme = 'res\themes\Ion Telemetry - Python\theme.yaml'
$taskName = 'Turing Ion Telemetry'
$register = $Mode -ne 'Build'

if ($register) {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = [Security.Principal.WindowsPrincipal]::new($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Open PowerShell as administrator under your own Windows account to register startup.'
    }
}

function Invoke-CheckedPython {
    param([string] $Interpreter, [string[]] $Arguments)

    & $Interpreter @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw ('Python exited with code {0}: {1}' -f $LASTEXITCODE, ($Arguments[0]))
    }
}

if ($Mode -ne 'Startup') {
    $python = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
    $required = @(
        $python,
        (Join-Path $ProjectRoot 'main.py'),
        (Join-Path $ProjectRoot 'turing-system-monitor.spec'),
        (Join-Path $ProjectRoot 'tools\windows-installer\generate-version-info.py'),
        (Join-Path $ProjectRoot 'config.yaml'),
        (Join-Path $ProjectRoot $theme),
        (Join-Path $ProjectRoot 'library\sensors\ion_telemetry.py')
    )
    foreach ($file in $required) {
        if (-not (Test-Path -LiteralPath $file -PathType Leaf)) {
            throw "Missing project file: $file"
        }
    }

    $running = Get-Process -Name 'main' -ErrorAction SilentlyContinue |
        Where-Object { $_.Path -eq $main }
    if ($running) {
        throw 'Exit the built Turing monitor from its tray icon before rebuilding.'
    }

    Push-Location -LiteralPath $ProjectRoot
    try {
        $preflight = @'
from pathlib import Path
import yaml
from library.sensors import sensors_custom

config = yaml.safe_load(Path('config.yaml').read_text(encoding='utf-8-sig'))
if config['config']['THEME'] != 'Ion Telemetry - Python':
    raise SystemExit('Select Ion Telemetry - Python and click Save settings before building.')
if not hasattr(sensors_custom, 'IonProcessOne'):
    raise SystemExit('The Ion imports are missing from library/sensors/sensors_custom.py.')
stats = Path('library/stats.py').read_text(encoding='utf-8-sig')
if 'cpu_percentage = cpu_percent(interval=0.1)' not in stats:
    raise SystemExit('Apply the Turing 3.10.0 CPU NaN patch before building.')
'@
        Invoke-CheckedPython $python @('-c', $preflight)
        Invoke-CheckedPython $python @('-m', 'PyInstaller', '--version')
        Invoke-CheckedPython $python @('tools\windows-installer\generate-version-info.py', '3.10.0')
        Invoke-CheckedPython $python @(
            '-m', 'PyInstaller', '--noconfirm',
            '--distpath', $dist,
            '--workpath', (Join-Path $ProjectRoot 'build-ion'),
            'turing-system-monitor.spec'
        )
    }
    finally {
        Pop-Location
    }

    foreach ($file in @('main.exe', 'configure.exe', 'config.yaml', $theme)) {
        if (-not (Test-Path -LiteralPath (Join-Path $bundle $file) -PathType Leaf)) {
            throw "Build output missing: $file"
        }
    }
    Set-Content -LiteralPath (Join-Path $bundle 'version.txt') -Value '3.10.0' -Encoding Ascii
    Write-Host "Build ready: $bundle"
}

if ($register) {
    if (-not (Test-Path -LiteralPath $main -PathType Leaf) -or
        -not (Test-Path -LiteralPath (Join-Path $bundle 'config.yaml') -PathType Leaf)) {
        throw 'Build the app before registering startup.'
    }

    $account = $identity.Name
    $action = New-ScheduledTaskAction -Execute $main -WorkingDirectory $bundle
    $trigger = New-ScheduledTaskTrigger -AtLogOn -User $account
    $taskPrincipal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
    $settings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $taskPrincipal -Settings $settings -Force | Out-Null
    Write-Host "Startup task registered: $taskName ($account)"
}
