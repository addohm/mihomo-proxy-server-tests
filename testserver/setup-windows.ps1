<#
Set up a fresh Windows 11 PC as a remote tester for mihomo_speedtest.py, reachable through a reverse SSH
tunnel to an external server (our VPS). Roadmap step 4.

  Right-click -> "Run with PowerShell", or:  powershell -ExecutionPolicy Bypass -File setup-windows.ps1

It asks for administrator rights, then:
  1. installs OpenSSH (client + server) and Python 3; the SSH server listens on 127.0.0.1 only, keys only
  2. asks for the public key(s) allowed to log in to this PC
  3. keeps the PC awake on mains power
  4. looks for VPN connections and VPN/TUN adapters and turns them off, so the tunnel goes direct
  5. makes this PC's own tunnel key and asks for the server's address
  6. shows the key and the command to run on the server (testserver/tunnel-host.sh), then starts the
     tunnel: a scheduled task (as SYSTEM, at startup) keeps "ssh -R 127.0.0.1:PORT:127.0.0.1:22" up.
     Keepalives every 15 s notice a dead tunnel within a minute; it then reconnects every 15 s, either
     indefinitely (the default) or a set number of times in a row before giving up until the next
     restart or re-run

Safe to re-run; previous answers are the defaults. The server address can change: re-run, enter the new
address, press Enter for the rest. Files: C:\ProgramData\mihomo-tester (key, settings, tunnel.log).
Reach the PC from the server with:  ssh -p PORT WINDOWS_USER@127.0.0.1
#>

$ErrorActionPreference = 'Stop'

$me = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Start-Process powershell.exe -Verb RunAs -ArgumentList "-NoProfile -NoExit -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    exit
}

$Dir = Join-Path $env:ProgramData 'mihomo-tester'
$Key = "$Dir\id_ed25519"
$Conf = "$Dir\tunnel.json"
$Loop = "$Dir\tunnel.ps1"
$Log = "$Dir\tunnel.log"
$Task = 'mihomo-tester tunnel'
$SshDir = "$env:SystemRoot\System32\OpenSSH"
# SIDs, not names: group names are localised (e.g. on Chinese Windows)
$Admins = '*S-1-5-32-544'
$System = '*S-1-5-18'

function Ask($question, $default) {
    $a = Read-Host "$question [$default]"
    if ($a) { $a.Trim() } else { "$default" }
}

function Read-Log {
    # cmd.exe keeps the log open while ssh runs, so open it with sharing (Get-Content can't)
    if (-not (Test-Path $Log)) { return '' }
    $f = [IO.File]::Open($Log, 'Open', 'Read', 'ReadWrite')
    try { (New-Object IO.StreamReader $f).ReadToEnd() } finally { $f.Close() }
}

function Stop-Tunnel {
    try { Stop-ScheduledTask -TaskName $Task -ErrorAction SilentlyContinue } catch {}
    Get-CimInstance Win32_Process -Filter "Name='ssh.exe'" |
        Where-Object { $_.CommandLine -like "*$Dir*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

# Only SYSTEM and Administrators may read the folder: it holds the tunnel's private key, and the task
# runs its script as SYSTEM. OpenSSH also refuses a private key that others can read.
New-Item -ItemType Directory -Force $Dir | Out-Null
icacls $Dir /inheritance:r /grant:r "${System}:(OI)(CI)F" "${Admins}:(OI)(CI)F" | Out-Null

Write-Host '==> OpenSSH (may take a few minutes on a fresh install)'
foreach ($cap in 'OpenSSH.Client~~~~0.0.1.0', 'OpenSSH.Server~~~~0.0.1.0') {
    if ((Get-WindowsCapability -Online -Name $cap).State -ne 'Installed') {
        Add-WindowsCapability -Online -Name $cap | Out-Null
    }
}
Set-Service sshd -StartupType Automatic
Start-Service sshd  # the first start writes sshd_config and the host keys
$cfg = "$env:ProgramData\ssh\sshd_config"
$text = (Get-Content $cfg -Raw) -replace '(?s)# mihomo-tester: begin.*?# mihomo-tester: end\r?\n', ''
$ours = "# mihomo-tester: begin`r`nListenAddress 127.0.0.1`r`nPasswordAuthentication no`r`n# mihomo-tester: end`r`n"
Set-Content -Path $cfg -Value ($ours + $text) -Encoding ascii -NoNewline
if (-not (Test-Path HKLM:\SOFTWARE\OpenSSH)) { New-Item HKLM:\SOFTWARE\OpenSSH | Out-Null }
New-ItemProperty HKLM:\SOFTWARE\OpenSSH -Name DefaultShell -PropertyType String -Force `
    -Value "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" | Out-Null
Restart-Service sshd
Write-Host '    SSH server: 127.0.0.1:22 only (reached through the tunnel), keys only'

Write-Host '==> Who may log in'
# Administrators' keys live here, not in the user's .ssh folder
$authKeys = "$env:ProgramData\ssh\administrators_authorized_keys"
while ($true) {
    $pub = (Read-Host 'Paste a public SSH key allowed to log in to this PC (Enter = done)').Trim()
    if (-not $pub) { break }
    if ($pub -notmatch '^(ssh-|ecdsa-|sk-)\S+ \S+') { Write-Host '    Not an SSH public key; try again.'; continue }
    if ((Test-Path $authKeys) -and (Get-Content $authKeys) -contains $pub) { Write-Host '    Already there.'; continue }
    Add-Content -Path $authKeys -Value $pub -Encoding ascii
}
if (Test-Path $authKeys) {
    icacls $authKeys /setowner $Admins | Out-Null  # sshd wants it owned by Administrators or SYSTEM
    icacls $authKeys /inheritance:r /grant:r "${System}:F" "${Admins}:F" | Out-Null
    Write-Host "    $(@(Get-Content $authKeys | Where-Object { $_.Trim() }).Count) key(s) may log in"
} else {
    Write-Host '    Warning: no keys yet, so nobody can log in. Re-run to add one.' -ForegroundColor Yellow
}

Write-Host '==> Python'
function Test-Python {
    if (-not (Get-Command py.exe -ErrorAction SilentlyContinue)) { return $false }
    try { & py.exe -3 --version | Out-Null; $LASTEXITCODE -eq 0 } catch { $false }
}
if (-not (Test-Python) -and (Get-Command winget.exe -ErrorAction SilentlyContinue)) {
    & winget.exe install -e --id Python.Python.3.13 --scope machine --accept-package-agreements `
        --accept-source-agreements --override '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0'
}
if (Test-Python) {
    # PyYAML lets the speed test read the controller secret from the client's config (no prompt over SSH)
    # wheels only: a brand-new Python may have no PyYAML wheel yet, and building one needs a C compiler
    & py.exe -3 -m pip install --quiet --disable-pip-version-check --only-binary :all: pyyaml
    if ($LASTEXITCODE -eq 0) { Write-Host "    $(& py.exe -3 --version), PyYAML" }
    else { Write-Host "    $(& py.exe -3 --version); no PyYAML for it yet (optional: the secret may be asked for)" }
}
else { Write-Host '    Warning: no Python. Install Python 3 from python.org for all users.' -ForegroundColor Yellow }

Write-Host '==> Power: never sleep on mains power'
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0

Write-Host '==> VPNs (the tunnel must go direct)'
$found = $false
$vpns = @(Get-VpnConnection -ErrorAction SilentlyContinue) + @(Get-VpnConnection -AllUserConnection -ErrorAction SilentlyContinue)
foreach ($v in $vpns | Where-Object { $_.ConnectionStatus -eq 'Connected' }) {
    rasdial.exe $v.Name /disconnect | Out-Null
    Write-Host "    Disconnected VPN connection '$($v.Name)'"
    $found = $true
}
$pattern = 'VPN|Tunnel|\bTUN\b|TAP-Win|Wintun|WireGuard|OpenVPN|Tailscale|ZeroTier|AnyConnect|Fortinet|' +
    'GlobalProtect|PANGP|Juniper|Pulse Secure|SonicWall|NordLynx|Mullvad|Hamachi|\bMeta\b|Mihomo|Clash|' +
    'sing-box|v2ray|Xray'
foreach ($a in Get-NetAdapter | Where-Object { $_.Status -ne 'Disabled' -and "$($_.Name) $($_.InterfaceDescription)" -match $pattern }) {
    Disable-NetAdapter -Name $a.Name -Confirm:$false
    Write-Host "    Disabled adapter '$($a.Name)' ($($a.InterfaceDescription)); undo: Enable-NetAdapter -Name '$($a.Name)'"
    $found = $true
}
if ($found) {
    Write-Host '    If one of these is a mihomo client''s TUN (Clash Verge Rev, Mihomo Party, ...), also turn TUN off'
    Write-Host '    in that client, or it comes back. Turn off any VPN app''s auto-connect too.'
} else {
    Write-Host '    None found'
}

Write-Host '==> Tunnel'
if (-not (Test-Path $Key)) {
    $p = Start-Process "$SshDir\ssh-keygen.exe" -Wait -NoNewWindow -PassThru `
        -ArgumentList "-q -t ed25519 -N `"`" -C mihomo-tester-$env:COMPUTERNAME -f `"$Key`""
    if ($p.ExitCode -ne 0) { throw 'ssh-keygen failed' }
}
# ssh-keygen makes the user running this the key's owner and gives them access; ssh.exe running as SYSTEM
# refuses a key owned by or readable by another account, so hand it to Administrators and reset it to the
# folder's SYSTEM + Administrators access
icacls $Key /setowner $Admins | Out-Null
icacls $Key /reset | Out-Null
icacls "$Key.pub" /reset | Out-Null
# trust the server's host key afresh at the next connect (the server or its address may have changed)
Remove-Item "$Dir\known_hosts" -ErrorAction SilentlyContinue
$c = if (Test-Path $Conf) { Get-Content $Conf -Raw | ConvertFrom-Json }
     else { [pscustomobject]@{ Host = ''; Port = 22; User = 'tunnel'; RemotePort = 2222; Retries = 0 } }
do { $c.Host = (Ask 'Server address (IP or host name)' $c.Host).Trim('[', ']') } until ($c.Host)
$c.Port = [int](Ask 'Server SSH port' $c.Port)
$c.User = Ask 'User on the server' $c.User
$c.RemotePort = [int](Ask 'Port on the server that leads to this PC' $c.RemotePort)
$retries = if ($null -ne $c.Retries) { $c.Retries } else { 0 }
$c | Add-Member -Force Retries ([int](Ask 'Reconnect attempts before giving up (0 = keep the tunnel open indefinitely)' $retries))
$c | ConvertTo-Json | Set-Content -Path $Conf -Encoding ascii

$pub = (Get-Content "$Key.pub" -Raw).Trim()
Write-Host ''
Write-Host "This PC's public key:" -ForegroundColor Cyan
Write-Host $pub
Write-Host ''
Write-Host "On the server, as root, with tunnel-host.sh from the project's testserver folder (adds user" -ForegroundColor Cyan
Write-Host "'$($c.User)', which may only hold this port forward; safe to re-run):" -ForegroundColor Cyan
Write-Host "bash tunnel-host.sh '$pub' $($c.RemotePort) $($c.User)"
Write-Host ''
Write-Host "Windows user name for logging in through the tunnel: $env:USERNAME"
Write-Host ''
Read-Host 'Press Enter once that is done on the server' | Out-Null

Set-Content -Path $Loop -Encoding ascii -Value @'
# Written by setup-windows.ps1; run as SYSTEM by the "mihomo-tester tunnel" task. Keeps the tunnel up.
$Dir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Log = "$Dir\tunnel.log"
$fails = 0
while ($true) {
    $c = Get-Content "$Dir\tunnel.json" -Raw | ConvertFrom-Json
    if ((Test-Path $Log) -and (Get-Item $Log).Length -gt 1MB) { Move-Item -Force $Log "$Log.old" }
    Add-Content $Log "$(Get-Date -Format s) connecting to $($c.User)@$($c.Host) port $($c.Port)"
    $start = Get-Date
    # ssh's messages go to the log through cmd.exe, which lets others read the file meanwhile ("ssh -E"
    # locks it). No paths here contain spaces or quotes, so plain words are safe on cmd's command line.
    cmd.exe /c ("$env:SystemRoot\System32\OpenSSH\ssh.exe -N -i $Dir\id_ed25519 -p $($c.Port) " +
        '-o BatchMode=yes -o IdentitiesOnly=yes -o ExitOnForwardFailure=yes -o LogLevel=VERBOSE ' +
        '-o ConnectTimeout=20 -o ServerAliveInterval=15 -o ServerAliveCountMax=4 ' +
        "-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$Dir\known_hosts " +
        "-R 127.0.0.1:$($c.RemotePort):127.0.0.1:22 $($c.User)@$($c.Host) 2>>$Log")
    $code = $LASTEXITCODE
    if (((Get-Date) - $start).TotalSeconds -ge 60) { $fails = 0 }  # it was up: count failures afresh
    $fails++
    if ($c.Retries -gt 0 -and $fails -gt $c.Retries) {
        Add-Content $Log "$(Get-Date -Format s) disconnected (exit $code); gave up after $($c.Retries) attempts (restart the PC or re-run setup)"
        exit 0
    }
    $of = if ($c.Retries -gt 0) { " of $($c.Retries)" } else { '' }
    Add-Content $Log "$(Get-Date -Format s) disconnected (exit $code); reconnecting in 15 s (attempt $fails$of)"
    Start-Sleep 15
}
'@

Stop-Tunnel
if (Test-Path $Log) { Move-Item -Force $Log "$Log.old" }
$action = New-ScheduledTaskAction -Execute powershell.exe `
    -Argument "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$Loop`""
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
$principal = New-ScheduledTaskPrincipal -UserId SYSTEM -LogonType ServiceAccount -RunLevel Highest
Register-ScheduledTask -TaskName $Task -Action $action -Trigger (New-ScheduledTaskTrigger -AtStartup) `
    -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $Task

Write-Host 'Connecting...'
$out = ''
for ($i = 0; $i -lt 30 -and $out -notmatch 'Authenticated to|disconnected \(exit'; $i++) {
    Start-Sleep 1
    $out = Read-Log
}
Start-Sleep 3  # a refused port forward ends the session right after authentication
$out = Read-Log
if ($out -match 'Authenticated to' -and $out -notmatch 'disconnected \(exit') {
    Write-Host "Tunnel is up. From the server: ssh -p $($c.RemotePort) $env:USERNAME@127.0.0.1" -ForegroundColor Green
} else {
    Write-Host 'The tunnel did not come up yet; it retries every 15 s (as set above). Last lines of the log:' -ForegroundColor Yellow
    (Read-Log) -split "`r?`n" | Select-Object -Last 15
    Write-Host "Log: $Log   Re-run this script to change the address."
}
