# Proof that Invoke-CappedProcess returns a real exit code on Windows PowerShell 5.1.
# Planted bug: Start-Process without touching $p.Handle can leave ExitCode $null
# (2026-08-20 refresh skipped publish because daily-accept's 0 came back empty).
#
# Run: powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test_capped_process_exitcode.ps1
$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
. (Join-Path $here "cardz_chain_lib.ps1")
$py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
$failed = New-Object System.Collections.Generic.List[string]
$checks = 0
function Check([string]$label, $got, $want) {
    $script:checks++
    if ($got -ne $want) { $script:failed.Add("FAIL $label`n  got  $got`n  want $want") }
}

# --- planted: no Handle (the 2026-08-20 refresh bug) ----------------------
$tmp0 = Join-Path $env:TEMP ("cardz-exit0-{0}.py" -f [guid]::NewGuid().ToString("N"))
$tmp7 = Join-Path $env:TEMP ("cardz-exit7-{0}.py" -f [guid]::NewGuid().ToString("N"))
Set-Content -LiteralPath $tmp0 -Value "raise SystemExit(0)" -Encoding ascii
Set-Content -LiteralPath $tmp7 -Value "raise SystemExit(7)" -Encoding ascii
$out = [System.IO.Path]::GetTempFileName()
$err = [System.IO.Path]::GetTempFileName()
try {
    $p = Start-Process -FilePath $py -ArgumentList @("-X", "utf8", $tmp0) -PassThru -NoNewWindow `
        -RedirectStandardOutput $out -RedirectStandardError $err
    # deliberately do NOT touch $p.Handle
    $null = $p.WaitForExit(15000)
    $planted = $p.ExitCode
} finally {
    Remove-Item -LiteralPath $out, $err -Force -ErrorAction SilentlyContinue
}
if ($null -eq $planted) {
    Write-Output "PLANTED_BUG_FIRED ExitCode is null without Handle (this is the refresh publish skip)"
} else {
    Write-Output "PLANTED_BUG_DID_NOT_FIRE_ON_THIS_HOST ExitCode=$planted (library still must touch Handle)"
}

# Source ratchet: the library must keep the Handle touch.
$lib = Get-Content -LiteralPath (Join-Path $here "cardz_chain_lib.ps1") -Raw
Check "library touches `$p.Handle before WaitForExit" ($lib -match '\$null = \$p\.Handle') $true
# The two refresh_publish.ps1 checks that stood here went to archive/scripts/ with
# that wrapper on 2026-08-25. watchdog_live_release.ps1 is now the live consumer
# of this library, so the dotsource ratchet points at it instead.
Check "watchdog_live_release dotsources the library" (
    (Get-Content -LiteralPath (Join-Path $here "watchdog_live_release.ps1") -Raw) -match 'cardz_chain_lib\.ps1'
) $true

# --- library: real codes --------------------------------------------------
$code0 = Invoke-CappedProcess -File $py -Arguments @("-X", "utf8", $tmp0) -Seconds 20
Check "capped exit 0" $code0 0
$code7 = Invoke-CappedProcess -File $py -Arguments @("-X", "utf8", $tmp7) -Seconds 20
Check "capped exit 7" $code7 7
Remove-Item -LiteralPath $tmp0, $tmp7 -Force -ErrorAction SilentlyContinue

Check "Test-MorningPcCapLog empty" (Test-MorningPcCapLog "") $false
Check "Test-MorningPcCapLog force-network" (Test-MorningPcCapLog "incr --force-network") $true
Check "Test-MorningPcCapLog pc_cdp_fresh_pages" (Test-MorningPcCapLog '"adapter": "pc_cdp_fresh_pages"') $true
Check "Test-MorningPcCapLog unrelated" (Test-MorningPcCapLog "daily-accept ok") $false

foreach ($line in $failed) { Write-Output $line }
Write-Output "$($checks - $failed.Count)/$checks checks passed"
if ($failed.Count -gt 0) { exit 1 }
exit 0
