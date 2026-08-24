# CARDZ 037 daily-chain preflight: fail fast on dead infrastructure, before the
# first collector/accept/publish step burns an hour and dies with a stack trace.
# Called at the top of nightly_collect_accept.ps1 / morning_browser_lanes.ps1 /
# refresh_publish.ps1 (see call sites). Prints one line per check, never a secret.
#
# Exit codes: 0 = all green
#             1 = HARD fail (docker engine / MySQL 3308 / disk / WSL)   -> caller aborts
#             2 = SOFT fail only (github credential / Chrome CDP)        -> caller continues, already notified
#
# Modes: nightly = docker+mysql+disk (+cred soft; no WSL/CDP: nightly does not publish)
#        morning = + wsl(hard) + cred(soft) + cdp(soft)
#        refresh = + wsl(hard) + cred(soft)
#        release = same as refresh
#        all     = everything
param(
  [ValidateSet("nightly", "morning", "refresh", "release", "all")][string]$Mode = "all",
  [string]$RepoRoot = "",
  [int]$MinFreeGb = 10,
  [int]$MysqlPort = 3308,
  [int]$CdpPort = 9333,
  [int]$TimeoutSec = 60,
  [switch]$NoNotify
)
$ErrorActionPreference = "Continue"
if ([string]::IsNullOrWhiteSpace($RepoRoot)) { $RepoRoot = Split-Path -Parent $PSScriptRoot }
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"

$hard = New-Object System.Collections.Generic.List[string]
$soft = New-Object System.Collections.Generic.List[string]
function Say([string]$m) { Write-Output "[preflight $Mode $([datetime]::UtcNow.ToString('HH:mm:ssZ'))] $m" }
function HardFail([string]$m) { $hard.Add($m); Say "HARD $m" }
function SoftFail([string]$m) { $soft.Add($m); Say "SOFT $m" }

# Run an external exe with a wall-clock cap. Returns @{ Exit=<int|-999 on timeout>; Out=<string> }.
function Invoke-Capped([string]$File, [string[]]$Arguments, [int]$Seconds) {
  $outFile = [System.IO.Path]::GetTempFileName()
  $errFile = [System.IO.Path]::GetTempFileName()
  try {
    $p = Start-Process -FilePath $File -ArgumentList $Arguments -NoNewWindow -PassThru `
      -RedirectStandardOutput $outFile -RedirectStandardError $errFile
    $null = $p.Handle   # PS 5.1: ExitCode stays empty unless the handle was touched before exit
    if (-not $p.WaitForExit($Seconds * 1000)) {
      try { $p.Kill() } catch {}
      return @{ Exit = -999; Out = "timeout after ${Seconds}s" }
    }
    $text = ((Get-Content -LiteralPath $outFile -Raw -ErrorAction SilentlyContinue) + (Get-Content -LiteralPath $errFile -Raw -ErrorAction SilentlyContinue))
    return @{ Exit = $p.ExitCode; Out = ([string]$text).Trim() }
  } finally {
    Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
  }
}

$needWsl  = $Mode -in @("morning", "refresh", "release", "all")
$needCred = $true
$needCdp  = $Mode -in @("morning", "all")

# 1. Docker engine (MySQL 3308 is a Docker Desktop container).
$docker = Invoke-Capped "docker" @("info", "--format", "{{.ServerVersion}}") $TimeoutSec
if ($docker.Exit -eq 0 -and $docker.Out) { Say "docker ok engine=$($docker.Out)" }
else { HardFail "docker engine unreachable (exit=$($docker.Exit)): $($docker.Out -replace '\s+',' ' | ForEach-Object { $_.Substring(0, [Math]::Min(200, $_.Length)) })" }

# 2. MySQL 3308 TCP.
$tnc = Test-NetConnection -ComputerName 127.0.0.1 -Port $MysqlPort -WarningAction SilentlyContinue -InformationLevel Quiet
if ($tnc) { Say "mysql tcp ok 127.0.0.1:$MysqlPort" } else { HardFail "mysql tcp closed 127.0.0.1:$MysqlPort" }

# 3. Disk free on the repo's drive.
try {
  $driveName = (Get-Item -LiteralPath $RepoRoot).PSDrive.Name
  $freeGb = [math]::Round((Get-PSDrive -Name $driveName).Free / 1GB, 1)
  if ($freeGb -ge $MinFreeGb) { Say "disk ok ${driveName}: ${freeGb}GB free" } else { HardFail "disk low ${driveName}: ${freeGb}GB free < ${MinFreeGb}GB" }
} catch { HardFail "disk check error: $($_.Exception.Message)" }

# 4. GitHub credential (release push runs in WSL, but WSL git is configured to
#    /mnt/c/.../git-credential-manager.exe, i.e. the same Windows Credential
#    Manager store this check reads). Never print the output.
if ($needCred) {
  $env:GCM_INTERACTIVE = "Never"; $env:GIT_TERMINAL_PROMPT = "0"
  $credOk = $false; $credWhy = ""
  # Windows PowerShell 5.1 mangles `"..." | git credential fill` stdin ("refusing to
  # work with credential missing protocol field"); feed it via a temp file + cmd
  # redirect instead. The probe file holds only protocol/host, no secret.
  $probeFile = [System.IO.Path]::GetTempFileName()
  try {
    [System.IO.File]::WriteAllText($probeFile, "protocol=https`nhost=github.com`n`n")
    $credOut = @(& cmd /c "git credential fill < `"$probeFile`" 2>&1")
    $credExit = $LASTEXITCODE
    $hasPassword = (@($credOut | Where-Object { "$_" -like 'password=*' }).Count -gt 0)
    $credOk = ($credExit -eq 0) -and $hasPassword
    if (-not $credOk) { $credWhy = "git credential fill exit=$credExit, password line present=$hasPassword" }
  } catch { $credWhy = "git credential fill threw: $($_.Exception.Message)" }
  finally { $credOut = $null; [System.IO.File]::Delete($probeFile) }
  $sshKeys = @(Get-ChildItem -LiteralPath (Join-Path $env:USERPROFILE ".ssh") -Filter "id_*" -File -ErrorAction SilentlyContinue | Where-Object { $_.Name -notlike '*.pub' })
  $sshAgent = Test-Path -LiteralPath "\\.\pipe\openssh-ssh-agent"
  if ($credOk) { Say "github credential ok (helper=manager; ssh keys=$($sshKeys.Count) agent=$sshAgent)" }
  else { SoftFail "github https credential missing: $credWhy (ssh keys=$($sshKeys.Count) agent=$sshAgent; remote is https so ssh does not substitute)" }
}

# 5. WSL Ubuntu answers (publish path = wsl.exe -d Ubuntu -- bash daily_public_release.sh).
if ($needWsl) {
  $wsl = Invoke-Capped "wsl.exe" @("-d", "Ubuntu", "--", "true") $TimeoutSec
  if ($wsl.Exit -eq 0) { Say "wsl ubuntu ok" } else { HardFail "wsl -d Ubuntu -- true failed (exit=$($wsl.Exit)): $($wsl.Out)" }
}

# 6. Chrome CDP for browser lanes (morning). Reuses ensure_chrome_cdp.ps1: revives if down.
if ($needCdp) {
  $ensure = Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1"
  if (-not (Test-Path -LiteralPath $ensure)) { SoftFail "ensure_chrome_cdp.ps1 missing at $ensure" }
  else {
    $cdp = Invoke-Capped "powershell.exe" @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$ensure`"", "-Port", "$CdpPort") 90
    if ($cdp.Exit -eq 0) { Say "cdp ok $($cdp.Out)" } else { SoftFail "cdp $CdpPort down: $($cdp.Out)" }
  }
}

# ---- verdict ---------------------------------------------------------------
$code = 0
if ($soft.Count -gt 0) { $code = 2 }
if ($hard.Count -gt 0) { $code = 1 }
if ($code -ne 0) {
  $summary = "RED CARDZ preflight [$Mode] " + $(if ($code -eq 1) { "HARD" } else { "soft" }) + ": " + (($hard + $soft) -join " | ")
  Say $summary
  if (-not $NoNotify) {
    $notify = Join-Path $RepoRoot "scripts\notify_hermes.py"
    if (Test-Path -LiteralPath $notify) {
      $safe = ($summary -replace '[<>]', "'") -replace '&', 'and'
      $help = (& $py -X utf8 $notify --help 2>&1 | Out-String)
      if ($help -match '\{[^}]*\bsend\b[^}]*\}') { & $py -X utf8 $notify send --text $safe 2>&1 | ForEach-Object { Say "notify: $_" } }
      else { & $py -X utf8 $notify chain --chain $(if ($Mode -in @('nightly','morning','refresh')) { $Mode } else { 'refresh' }) --status $safe --exit-code 1 2>&1 | ForEach-Object { Say "notify: $_" } }
    } else { Say "notify_hermes.py missing at $notify; not notified" }
  }
}
Say "preflight exit=$code"
exit $code
