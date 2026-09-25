# Always-on CARDZ headed CDP. Never prompts. Never touches Codex 9222.
# 9333 = PriceCharting (cardz-chrome-cdp-9333)
# 9444 = social X/Threads (cardz-chrome-cdp-9444)
param(
  [switch]$SelfTest,
  [switch]$IdentityOnly
)

$ErrorActionPreference = "Stop"
$Ensure = Join-Path $PSScriptRoot "ensure_chrome_cdp.ps1"
if (-not (Test-Path -LiteralPath $Ensure)) { throw "missing $Ensure" }

$Fleet = @(
  @{ Port = 9333; StartUrl = "https://www.pricecharting.com/" },
  @{ Port = 9444; StartUrl = "https://x.com/" }
)

function Test-FleetShape {
  $failed = 0
  $ports = @($Fleet | ForEach-Object { [int]$_.Port })
  if ($ports -contains 9222) {
    Write-Host "SELFTEST_FAIL fleet-contains-9222"
    $failed++
  } else {
    Write-Host "SELFTEST_OK fleet-skips-9222"
  }
  if ($ports -contains 9333) {
    Write-Host "SELFTEST_OK fleet-has-9333-pc"
  } else {
    Write-Host "SELFTEST_FAIL fleet-missing-9333"
    $failed++
  }
  if ($ports -contains 9444) {
    Write-Host "SELFTEST_OK fleet-has-9444-social"
  } else {
    Write-Host "SELFTEST_FAIL fleet-missing-9444"
    $failed++
  }
  if ($ports.Count -lt 2 -or $ports.Count -gt 3) {
    Write-Host "SELFTEST_FAIL fleet-size-$($ports.Count)"
    $failed++
  } else {
    Write-Host "SELFTEST_OK fleet-size-$($ports.Count)"
  }
  return $failed
}

if ($SelfTest) {
  $failed = Test-FleetShape
  if ($failed -gt 0) {
    Write-Host "SELFTEST_FAILED count=$failed"
    exit 1
  }
  Write-Host "SELFTEST_PASSED"
  exit 0
}

$rc = 0
foreach ($item in $Fleet) {
  $port = [int]$item.Port
  if ($port -eq 9222) {
    Write-Host "CDP_REFUSED port=9222 (Codex; fleet will not touch it)"
    $rc = 1
    continue
  }
  $args = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", $Ensure,
    "-Port", "$port",
    "-StartUrl", $item.StartUrl
  )
  if ($IdentityOnly) { $args += "-IdentityOnly" }
  & powershell.exe @args
  if ($LASTEXITCODE -ne 0) {
    Write-Host "FLEET_FAIL port=$port exit=$LASTEXITCODE"
    $rc = 1
  } else {
    Write-Host "FLEET_OK port=$port"
  }
}
exit $rc
