# Launch the cockpit backend and MA scheduler as detached, hidden processes.
# Uses WMI Win32_Process.Create so the processes are NOT children of this
# window: closing any PowerShell/cmd window will not terminate them.

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$py = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $py)) { Write-Host '[ERROR] .venv not found, run start.bat first'; exit 1 }

function Start-HiddenProcess([string]$script, [string]$logFile) {
    $inner = "& '$py' -u $script *>> '$root\$logFile'"
    $line = 'powershell -NoProfile -WindowStyle Hidden -Command "' + $inner + '"'
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create `
        -Arguments @{ CommandLine = $line; CurrentDirectory = $root }
    if ($r.ReturnValue -eq 0) { Write-Host "  started $script (pid $($r.ProcessId))" }
    else { Write-Host "  [ERROR] failed to start $script (code $($r.ReturnValue))" }
}

Start-HiddenProcess 'monitor\scheduler.py' 'monitor\monitor.log'
Start-Sleep -Seconds 2
Start-HiddenProcess 'app.py' 'cockpit.log'
