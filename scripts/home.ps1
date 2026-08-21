#requires -Version 5.1
<#
.SYNOPSIS
Run the Filmocabulary Home production server. Windows equivalent of scripts/home.

.PARAMETER Command
One of start, prepare, backup, manage, or service (see PRODUCTION.md).

.PARAMETER RemainingArgs
Arguments forwarded to the backup or manage Django commands.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts/home.ps1 start
powershell -ExecutionPolicy Bypass -File scripts/home.ps1 manage createsuperuser
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "prepare", "backup", "manage", "service")]
    [string]$Command = "start",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python     = Join-Path $ProjectDir ".venv\Scripts\python.exe"

Set-Location $ProjectDir
$env:DJANGO_SETTINGS_MODULE = "config.settings.production"

if (-not (Test-Path -PathType Leaf $Python)) {
    Write-Error "Missing .venv. Create it and install requirements-production.txt first."
    exit 1
}

function Invoke-Prepare {
    & $Python manage.py migrate --noinput
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $Python manage.py collectstatic --noinput
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

    & $Python manage.py check
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

function Test-Uvicorn {
    & $Python -c "import uvicorn" *> $null
    return $LASTEXITCODE -eq 0
}

$TaskName = "Filmocabulary Home Server"
$LogDir   = Join-Path $ProjectDir "logs"
$LogFile  = Join-Path $LogDir "home.log"

function Install-HomeService {
    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

    $action = New-ScheduledTaskAction `
        -Execute "powershell.exe" `
        -Argument "-NoProfile -WindowStyle Hidden -Command `"& '$PSCommandPath' start *>> '$LogFile'`"" `
        -WorkingDirectory $ProjectDir

    $trigger = New-ScheduledTaskTrigger -AtLogOn

    $settings = New-ScheduledTaskSettingsSet `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) `
        -ExecutionTimeLimit ([TimeSpan]::Zero) `
        -DontStopOnIdleEnd -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger `
        -Settings $settings -RunLevel Limited -Force | Out-Null

    Start-ScheduledTask -TaskName $TaskName

    Write-Host "Installed and started as a Scheduled Task (runs at logon, restarts on failure)."
    Write-Host "Log file: $LogFile"
}

function Uninstall-HomeService {
    Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue |
        Stop-ScheduledTask -ErrorAction SilentlyContinue

    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Host "Service removed."
}

function Get-HomeServiceStatus {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    if (-not $task) {
        Write-Host "Not installed."
        return
    }

    $info = $task | Get-ScheduledTaskInfo
    Write-Host "State: $($task.State)"
    Write-Host "Last run: $($info.LastRunTime)  Result: $($info.LastTaskResult)"
    Write-Host "Next run: $($info.NextRunTime)"
}

switch ($Command) {
    "start" {
        if (-not (Test-Uvicorn)) {
            Write-Error "Uvicorn is missing. Install requirements-production.txt in .venv."
            exit 1
        }
        Invoke-Prepare
        & $Python -m config.uvicorn
        exit $LASTEXITCODE
    }

    "prepare" {
        Invoke-Prepare
    }

    "backup" {
        & $Python manage.py backup_database @RemainingArgs
        exit $LASTEXITCODE
    }

    "manage" {
        & $Python manage.py @RemainingArgs
        exit $LASTEXITCODE
    }

    "service" {
        $action = if ($RemainingArgs.Count -ge 1) { $RemainingArgs[0] } else { "status" }

        switch ($action) {
            "install"   { Install-HomeService }
            "uninstall" { Uninstall-HomeService }
            "start"     { Start-ScheduledTask -TaskName $TaskName }
            "stop"      { Stop-ScheduledTask -TaskName $TaskName }
            "restart"   {
                Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
                Start-ScheduledTask -TaskName $TaskName
            }
            "status"    { Get-HomeServiceStatus }
            "logs"      { Get-Content -Path $LogFile -Tail 50 -Wait }
            default {
                Write-Error "Usage: scripts\home.ps1 service {install|uninstall|start|stop|restart|status|logs}"
                exit 2
            }
        }
    }
}