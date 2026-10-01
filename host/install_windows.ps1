# Installs the DC32 Display host for the current user and starts it at every logon.
#   powershell -ExecutionPolicy Bypass -File host\install_windows.ps1
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Data = Join-Path $env:LOCALAPPDATA "dc32-display"
$Venv = Join-Path $Data "venv"
New-Item -ItemType Directory -Force -Path $Data | Out-Null

function Find-Python {
  foreach ($c in @("py -3", "python")) {
    try { $v = & ([scriptblock]::Create("$c -c `"import sys;print(sys.version_info>=(3,9))`"")) 2>$null; if ($v -eq "True") { return $c } } catch {}
  }
  return $null
}
$py = Find-Python
if (-not $py) {
  Write-Host "Python 3 not found - installing via winget (user scope)"
  winget install -e --id Python.Python.3.12 --scope user --silent --accept-package-agreements --accept-source-agreements
  $env:Path = [Environment]::GetEnvironmentVariable("Path","User") + ";" + [Environment]::GetEnvironmentVariable("Path","Machine")
  $py = Find-Python
  if (-not $py) { throw "Python install failed" }
}
Write-Host "Using $py"
if (-not (Test-Path (Join-Path $Venv "Scripts\python.exe"))) { & ([scriptblock]::Create("$py -m venv `"$Venv`"")) }
$VPy = Join-Path $Venv "Scripts\python.exe"
$VPyw = Join-Path $Venv "Scripts\pythonw.exe"
& $VPy -m pip install --upgrade pip --quiet
& $VPy -m pip install --quiet -e $Here
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }

# Logon task: interactive user session (needed for screen capture), restart on failure, no time limit.
$TaskName = "DC32 Display"
$Action = New-ScheduledTaskAction -Execute $VPyw -Argument "-m dc32host run" -WorkingDirectory $Here
$Trigger = New-ScheduledTaskTrigger -AtLogOn -User "$env:USERDOMAIN\$env:USERNAME"
$Settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero) `
            -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) -MultipleInstances IgnoreNew
$Principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Principal $Principal -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName
Write-Host "Installed. Task '$TaskName' starts at logon. Logs: $Data\logs\host.log  Config: $env:APPDATA\dc32-display\config.json"
