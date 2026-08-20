#requires -Version 5.1
<#
.SYNOPSIS
Run the Filmocabulary Home production server. Windows equivalent of scripts/home.

.PARAMETER Command
One of start, prepare, backup, or manage (see PRODUCTION.md).

.PARAMETER RemainingArgs
Arguments forwarded to the backup or manage Django commands.

.EXAMPLE
powershell -ExecutionPolicy Bypass -File scripts/home.ps1 start
powershell -ExecutionPolicy Bypass -File scripts/home.ps1 manage createsuperuser
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "prepare", "backup", "manage")]
    [string]$Command = "start",

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$ProjectDir = Split-Path -Parent $PSScriptRoot
$Python = Join-Path $ProjectDir ".venv\Scripts\python.exe"

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
}
