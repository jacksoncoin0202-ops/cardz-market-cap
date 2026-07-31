# One-button maintenance for GemRate PSA10 POP>=1000 pool (940).
# Usage (from repo root):
#   powershell -NoProfile -File scripts\maintain_qualified_pool.ps1
#   powershell -NoProfile -File scripts\maintain_qualified_pool.ps1 -ImageLimit 50

param(
  [int]$MapLimit = 0,
  [int]$HarvestLimit = 0,
  [int]$ImageLimit = 0,
  [switch]$SkipMap,
  [switch]$SkipHarvest,
  [switch]$SkipImages,
  [ValidateSet("full","incremental")]
  [string]$HarvestMode = "incremental"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

$Py = "C:\Users\jackson0202\AppData\Local\Programs\Python\Python310\python.exe"
if (-not (Test-Path $Py)) {
  $Py = "python"
}

$argsList = @("-X", "utf8", "pipelines\qualified_pool_operator.py", "maintain", "--harvest-mode", $HarvestMode)
if ($SkipMap) { $argsList += "--skip-map" }
if ($SkipHarvest) { $argsList += "--skip-harvest" }
if ($SkipImages) { $argsList += "--skip-images" }
if ($MapLimit -gt 0) { $argsList += @("--map-limit", "$MapLimit") }
if ($HarvestLimit -gt 0) { $argsList += @("--harvest-limit", "$HarvestLimit") }
if ($ImageLimit -gt 0) { $argsList += @("--image-limit", "$ImageLimit") }

Write-Host "Running: $Py $($argsList -join ' ')"
& $Py @argsList
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "`n=== gap-report ==="
& $Py -X utf8 pipelines\qualified_pool_operator.py gap-report
exit $LASTEXITCODE
