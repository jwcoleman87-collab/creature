$ErrorActionPreference = "Continue"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$journalDir = Join-Path $root "journal_data"
$python = Join-Path $root "venv\Scripts\python.exe"
$entry = Join-Path $root "crypto_main.py"
$outLog = Join-Path $journalDir "guard.stdout.log"
$errLog = Join-Path $journalDir "guard.stderr.log"
$guardStopFile = Join-Path $journalDir "guard.stop"
$guardStatusFile = Join-Path $journalDir "guard.status.json"
$guardLockFile = Join-Path $journalDir "guard.lock.json"

New-Item -ItemType Directory -Path $journalDir -Force | Out-Null

function Ensure-Utf8Log([string]$path) {
    if (-not (Test-Path -LiteralPath $path)) {
        New-Item -ItemType File -Path $path -Force | Out-Null
        return
    }

    try {
        $bytes = [System.IO.File]::ReadAllBytes($path)
        if ($bytes.Length -ge 2 -and $bytes[0] -eq 0xFF -and $bytes[1] -eq 0xFE) {
            $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
            $archive = "$path.legacy-utf16-$stamp"
            Move-Item -LiteralPath $path -Destination $archive -Force
            New-Item -ItemType File -Path $path -Force | Out-Null
        }
    } catch {
        New-Item -ItemType File -Path $path -Force | Out-Null
    }
}

function Write-GuardLog([string]$path, [string]$message) {
    $line = "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] [guard] $message"
    Add-Content -LiteralPath $path -Value $line -Encoding utf8
}

function Write-GuardStatus([string]$state, [int]$loop, [int]$lastExitCode, [string]$note) {
    $payload = @{
        state = $state
        guard_pid = $PID
        host = $env:COMPUTERNAME
        updated_local = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
        updated_utc = (Get-Date).ToUniversalTime().ToString("yyyy-MM-ddTHH:mm:ssZ")
        loop = $loop
        last_exit_code = $lastExitCode
        note = $note
    } | ConvertTo-Json
    Set-Content -LiteralPath $guardStatusFile -Value $payload -Encoding utf8
}

if (Test-Path -LiteralPath $guardLockFile) {
    $activePid = $null
    try {
        $active = Get-Content -LiteralPath $guardLockFile -Raw | ConvertFrom-Json
        $activePid = [int]$active.guard_pid
    } catch {}

    if ($activePid -and (Get-Process -Id $activePid -ErrorAction SilentlyContinue)) {
        Write-GuardLog -path $errLog -message "Another guard is already running (pid=$activePid). Exiting."
        exit 2
    }

    Remove-Item -LiteralPath $guardLockFile -ErrorAction SilentlyContinue
}

$lockPayload = @{
    guard_pid = $PID
    started_local = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss")
    host = $env:COMPUTERNAME
} | ConvertTo-Json
Set-Content -LiteralPath $guardLockFile -Value $lockPayload -Encoding utf8

if (-not (Test-Path -LiteralPath $python)) {
    Write-GuardLog -path $errLog -message "Missing venv python at $python"
    Write-GuardStatus -state "failed" -loop 0 -lastExitCode 1 -note "Missing venv python."
    Remove-Item -LiteralPath $guardLockFile -ErrorAction SilentlyContinue
    exit 1
}

$loop = 0
$lastExitCode = 0
Write-GuardStatus -state "running" -loop $loop -lastExitCode $lastExitCode -note "Guard online."
Ensure-Utf8Log -path $outLog
Ensure-Utf8Log -path $errLog
Write-GuardLog -path $outLog -message "Guard online."

try {
    while ($true) {
        if (Test-Path -LiteralPath $guardStopFile) {
            Write-GuardLog -path $outLog -message "Stop file detected. Guard shutting down."
            Write-GuardStatus -state "stopping" -loop $loop -lastExitCode $lastExitCode -note "Stop file detected."
            Remove-Item -LiteralPath $guardStopFile -ErrorAction SilentlyContinue
            break
        }

        $loop += 1
        Write-GuardStatus -state "running" -loop $loop -lastExitCode $lastExitCode -note "Launching creature runtime."
        Write-GuardLog -path $outLog -message "Starting crypto_main.py (loop=$loop)."

        try {
            & $python $entry 1>> $outLog 2>> $errLog
            $lastExitCode = $LASTEXITCODE
        } catch {
            $lastExitCode = -1
            $msg = $_.Exception.Message
            Write-GuardLog -path $errLog -message "Launch exception: $msg"
        }

        Write-GuardLog -path $outLog -message "crypto_main.py exited (code=$lastExitCode). Restarting in 5s."
        Write-GuardStatus -state "running" -loop $loop -lastExitCode $lastExitCode -note "Child exited; restart in 5s."
        Start-Sleep -Seconds 5
    }
} finally {
    Write-GuardStatus -state "stopped" -loop $loop -lastExitCode $lastExitCode -note "Guard stopped."
    Remove-Item -LiteralPath $guardLockFile -ErrorAction SilentlyContinue
}
