# CARDZ 036 / FE03 daily public release entrypoint.
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
$script = Join-Path $PSScriptRoot "daily_public_release.sh"
$wslScript = (& wsl.exe -d Ubuntu -- wslpath -a $script).Trim()
if ($LASTEXITCODE -ne 0 -or -not $wslScript) {
    throw "Could not resolve the WSL daily release script path"
}

& wsl.exe -d Ubuntu -- bash $wslScript
exit $LASTEXITCODE
