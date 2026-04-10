param(
    [int]$TimeoutSeconds = 45,
    [switch]$Force
)

$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$journalDir = Join-Path $root "journal_data"
$guardStopFile = Join-Path $journalDir "guard.stop"
$creatureStopFile = Join-Path $journalDir "creature.stop"
$runtimeLockFile = Join-Path $journalDir "creature.runtime.lock"
$guardLockFile = Join-Path $journalDir "guard.lock.json"
$guardStatusFile = Join-Path $journalDir "guard.status.json"

New-Item -ItemType Directory -Path $journalDir -Force | Out-Null

Set-Content -LiteralPath $guardStopFile -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Encoding utf8
Set-Content -LiteralPath $creatureStopFile -Value (Get-Date -Format "yyyy-MM-dd HH:mm:ss") -Encoding utf8
Write-Host "Stop requested (guard + creature)." -ForegroundColor Yellow

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$stopped = $false

while ((Get-Date) -lt $deadline) {
    if (-not (Test-Path -LiteralPath $runtimeLockFile)) {
        $stopped = $true
        break
    }
    Start-Sleep -Seconds 2
}

if ($stopped) {
    Remove-Item -LiteralPath $guardStatusFile -ErrorAction SilentlyContinue
    Write-Host "Creature runtime stopped cleanly." -ForegroundColor Green
    exit 0
}

if (-not $Force) {
    Write-Host "Runtime lock still present after $TimeoutSeconds seconds." -ForegroundColor Red
    Write-Host "If needed, run again with -Force to terminate remaining process."
    exit 1
}

$runtimePid = $null
if (Test-Path -LiteralPath $runtimeLockFile) {
    try {
        $runtime = Get-Content -LiteralPath $runtimeLockFile -Raw | ConvertFrom-Json
        $runtimePid = [int]$runtime.pid
    } catch {}
}

if ($runtimePid) {
    Stop-Process -Id $runtimePid -Force -ErrorAction SilentlyContinue
}

$guardPid = $null
if (Test-Path -LiteralPath $guardLockFile) {
    try {
        $guard = Get-Content -LiteralPath $guardLockFile -Raw | ConvertFrom-Json
        $guardPid = [int]$guard.guard_pid
    } catch {}
}

if ($guardPid) {
    Stop-Process -Id $guardPid -Force -ErrorAction SilentlyContinue
}

Remove-Item -LiteralPath $runtimeLockFile -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $guardLockFile -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $guardStatusFile -ErrorAction SilentlyContinue
Write-Host "Force stop completed." -ForegroundColor Yellow
