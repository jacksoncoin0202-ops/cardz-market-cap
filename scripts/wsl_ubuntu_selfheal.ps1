# WSL Ubuntu self-heal for the V2 tick (2026-08-23).
#
# Three times in one morning `wsl.exe -d Ubuntu` stopped answering with
# Wsl/Service/0x8007274c while the utility VM stayed alive (the docker engine
# on the same VM kept answering and MySQL 3308 kept serving).  Every tick then
# fails at `& wsl.exe @args` until a human runs `wsl -t Ubuntu`; the 10-minute
# schedule retries for ever against a distro that will not come back on its
# own.  Terminating ONLY the Ubuntu distro brought it back in 5 s (10:32Z).
#
# Decision table (exit code is the verdict, the caller logs it):
#   0  WSL_OK        distro answered `true` inside the cap
#   0  WSL_REVIVED   distro was silent, VM alive, `wsl -t Ubuntu` revived it
#   3  WSL_DEAD      distro silent and either the VM is dead too (docker does
#                    not answer: `wsl --shutdown` territory, a human's call
#                    because it takes MySQL 3308 down) or the revive failed
#   4  WSL_SILENT_DRYRUN  -DryRun: distro silent, nothing terminated
#
# A worker still inside a silent distro is lost either way; the V2 journal's
# lease / attempt accounting re-runs its task on the next tick.  Nothing here
# ever runs `wsl --shutdown`.
[CmdletBinding()]
param(
    [int]$ProbeCapMs = 30000,
    [int]$DockerCapMs = 30000,
    [switch]$DryRun
)
$ErrorActionPreference = "Continue"

function Invoke-CappedExit([string]$File, [string]$ArgumentString, [int]$CapMs) {
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = $File
    $psi.Arguments = $ArgumentString
    $psi.UseShellExecute = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.CreateNoWindow = $true
    try { $p = [System.Diagnostics.Process]::Start($psi) } catch { return -998 }
    $null = $p.Handle
    if (-not $p.WaitForExit($CapMs)) {
        try { $p.Kill() } catch {}
        return -999
    }
    return $p.ExitCode
}

function Test-WslUbuntu([int]$CapMs) {
    return ((Invoke-CappedExit "wsl.exe" "-d Ubuntu -- true" $CapMs) -eq 0)
}

$t0 = [datetime]::UtcNow
if (Test-WslUbuntu $ProbeCapMs) {
    Write-Output ("WSL_OK probeMs=" + [int]([datetime]::UtcNow - $t0).TotalMilliseconds)
    exit 0
}
$dockerExit = Invoke-CappedExit "docker.exe" "info --format {{.ServerVersion}}" $DockerCapMs
$vmAlive = ($dockerExit -eq 0)
if ($DryRun) {
    Write-Output "WSL_SILENT_DRYRUN vmAlive=$vmAlive dockerExit=$dockerExit wouldTerminate=$vmAlive"
    exit 4
}
if (-not $vmAlive) {
    Write-Output "WSL_DEAD vmAlive=False dockerExit=$dockerExit action=none (wsl --shutdown is a human's call: it takes MySQL 3308 down)"
    exit 3
}
Write-Output "WSL_SILENT vmAlive=True action=wsl-t-Ubuntu"
$null = Invoke-CappedExit "wsl.exe" "-t Ubuntu" 60000
Start-Sleep -Seconds 5
if (Test-WslUbuntu $ProbeCapMs) {
    Write-Output ("WSL_REVIVED totalMs=" + [int]([datetime]::UtcNow - $t0).TotalMilliseconds)
    exit 0
}
Write-Output "WSL_DEAD vmAlive=True revive=failed"
exit 3
