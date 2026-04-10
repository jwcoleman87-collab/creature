$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$journalDir = Join-Path $root "journal_data"
$runtimeLockFile = Join-Path $journalDir "creature.runtime.lock"
$guardStatusFile = Join-Path $journalDir "guard.status.json"
$guardLockFile = Join-Path $journalDir "guard.lock.json"

function Read-JsonFile([string]$path) {
    $raw = $null
    try { $raw = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8) } catch {}
    if (-not $raw) {
        try { $raw = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::Unicode) } catch {}
    }
    if (-not $raw) { return $null }
    try { return ($raw | ConvertFrom-Json) } catch { return $null }
}

Write-Host "=== Creature Status ==="
Write-Host "Repo: $root"

$runtimeLive = $false
if (Test-Path -LiteralPath $runtimeLockFile) {
    $runtime = Read-JsonFile -path $runtimeLockFile
    if ($runtime) {
        $runtimePid = [int]$runtime.pid
        $proc = Get-Process -Id $runtimePid -ErrorAction SilentlyContinue
        if ($proc) {
            $runtimeLive = $true
            Write-Host "Runtime: RUNNING (pid=$runtimePid, host=$($runtime.host), started_utc=$($runtime.started_utc))" -ForegroundColor Green
        } else {
            Write-Host "Runtime: LOCK PRESENT but pid not active (stale lock)." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Runtime: lock file unreadable." -ForegroundColor Yellow
    }
} else {
    Write-Host "Runtime: STOPPED (no runtime lock)." -ForegroundColor Yellow
}

if (Test-Path -LiteralPath $guardLockFile) {
    $guard = Read-JsonFile -path $guardLockFile
    if ($guard) {
        $guardPid = [int]$guard.guard_pid
        if (Get-Process -Id $guardPid -ErrorAction SilentlyContinue) {
            Write-Host "Guard: RUNNING (pid=$guardPid)" -ForegroundColor Green
        } else {
            Write-Host "Guard: lock present but process not active." -ForegroundColor Yellow
        }
    } else {
        Write-Host "Guard: lock unreadable." -ForegroundColor Yellow
    }
} else {
    Write-Host "Guard: not running (no lock)." -ForegroundColor Yellow
}

if (Test-Path -LiteralPath $guardStatusFile) {
    $gs = Read-JsonFile -path $guardStatusFile
    if ($gs) {
        if (Test-Path -LiteralPath $guardLockFile) {
            Write-Host "Guard status: state=$($gs.state) loop=$($gs.loop) last_exit_code=$($gs.last_exit_code) updated_local=$($gs.updated_local)"
        } else {
            Write-Host "Guard status file exists but guard lock is absent (stale status file)." -ForegroundColor Yellow
        }
    }
}

try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://127.0.0.1:8080/api/status" -TimeoutSec 3
    $api = $resp.Content | ConvertFrom-Json
    $last = $api.last_updated
    $runtime = $api.runtime
    Write-Host "API: ONLINE | status=$($api.status) | health=$($api.health.state) | last_updated=$last"
    if ($runtime) {
        Write-Host "API runtime: instance=$($runtime.instance_id) source=$($runtime.source) pid=$($runtime.pid) cycles=$($runtime.cycle_count) phase=$($runtime.last_cycle_phase)"
    }
} catch {
    Write-Host "API: OFFLINE on http://127.0.0.1:8080/api/status" -ForegroundColor Yellow
}
