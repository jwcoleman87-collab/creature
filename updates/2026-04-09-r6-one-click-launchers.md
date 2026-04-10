# 2026-04-09-r6 - One-Click Launchers

## Goal
Give the operator a true one-click start and one-click stop workflow from Windows Explorer.

## Added files
- `Wake Up Creature.bat`
  - Runs `creature_start.ps1`
  - Waits 3 seconds
  - Opens `http://127.0.0.1:8080`
  - Writes launch/status logs:
    - `journal_data/wake_up.log`
    - `journal_data/wake_status.txt`
- `Sleep Creature.bat`
  - Runs `creature_stop.ps1`
  - Writes stop output:
    - `journal_data/sleep_status.txt`

## Supporting cleanup
- `creature_start.ps1` now clears stale `guard.status.json` before launch.
- `creature_stop.ps1` now removes `guard.status.json` during clean/force stop.
- `creature_status.ps1` now marks orphaned guard status as stale instead of presenting it as live.

## Usage
1. Double-click `Wake Up Creature.bat`
2. Creature starts and dashboard opens automatically.
3. Double-click `Sleep Creature.bat` when you want to stop.
