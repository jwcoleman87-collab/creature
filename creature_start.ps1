param(
    [switch]$Foreground
)

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$journalDir = Join-Path $root "journal_data"
$guardScript = Join-Path $root "run_creature_guard.ps1"
$guardLockFile = Join-Path $journalDir "guard.lock.json"
$runtimeLockFile = Join-Path $journalDir "creature.runtime.lock"
$guardStopFile = Join-Path $journalDir "guard.stop"
$creatureStopFile = Join-Path $journalDir "creature.stop"
$guardStatusFile = Join-Path $journalDir "guard.status.json"

if (-not (Test-Path -LiteralPath $guardScript)) {
    Write-Host "Guard script not found: $guardScript" -ForegroundColor Red
    exit 1
}

New-Item -ItemType Directory -Path $journalDir -Force | Out-Null
Remove-Item -LiteralPath $guardStopFile -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $creatureStopFile -ErrorAction SilentlyContinue
Remove-Item -LiteralPath $guardStatusFile -ErrorAction SilentlyContinue

$activeGuardPid = $null
if (Test-Path -LiteralPath $guardLockFile) {
    try {
        $lock = Get-Content -LiteralPath $guardLockFile -Raw | ConvertFrom-Json
        $activeGuardPid = [int]$lock.guard_pid
    } catch {}
}

if ($activeGuardPid) {
    $guardProc = Get-Process -Id $activeGuardPid -ErrorAction SilentlyContinue
    if ($guardProc) {
        $apiOnline = $false
        try {
            $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8080/api/status" -TimeoutSec 2
            if ($resp.StatusCode -eq 200) { $apiOnline = $true }
        } catch {}

        $runtimeLockExists = Test-Path -LiteralPath $runtimeLockFile
        if ($apiOnline -or $runtimeLockExists) {
            Write-Host "Creature guard already running (pid=$activeGuardPid)." -ForegroundColor Yellow
            Write-Host "Dashboard: http://127.0.0.1:8080"
            exit 0
        }

        Write-Host "Stale guard lock detected (PID reused). Recovering..." -ForegroundColor Yellow
    }

    Remove-Item -LiteralPath $guardLockFile -ErrorAction SilentlyContinue
}

if ($Foreground) {
    Write-Host "Starting Creature guard in foreground..."
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $guardScript
    exit $LASTEXITCODE
}

Push-Location $root
try {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = "$env:WINDIR\System32\WindowsPowerShell\v1.0\powershell.exe"
    $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$guardScript`""
    $psi.WorkingDirectory = $root
    $psi.UseShellExecute = $true
    $psi.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
    [void][System.Diagnostics.Process]::Start($psi)
    Start-Sleep -Seconds 1

    $newGuardPid = $null
    if (Test-Path -LiteralPath $guardLockFile) {
        try {
            $lock = Get-Content -LiteralPath $guardLockFile -Raw | ConvertFrom-Json
            $newGuardPid = [int]$lock.guard_pid
        } catch {}
    }

    if ($newGuardPid) {
        Write-Host "Creature guard started in background (pid=$newGuardPid)." -ForegroundColor Green
    } else {
        Write-Host "Start command issued. Run .\\creature_status.ps1 to confirm runtime." -ForegroundColor Yellow
    }
    Write-Host "Dashboard: http://127.0.0.1:8080"
} finally {
    Pop-Location
}
