[CmdletBinding()]
param(
    [ValidateSet(
        'Doctor', 'Up', 'Bootstrap', 'Migrate', 'Import', 'Routes', 'Discovery', 'Audit', 'Alerts', 'Status',
        'Daily', 'Full-Backfill', 'Rebuild-Db', 'Export-Snapshot', 'Seed-Build', 'Seed-Verify', 'Seed-Restore',
        'Registry', 'Explain', 'Graph', 'Generate-Docs', 'Down'
    )]
    [string]$Action = 'Bootstrap',
    [switch]$ExternalDb,
    [ValidateSet('staging', 'production')]
    [string]$Mode = 'staging',
    [switch]$RefreshActiveUniverse,
    [switch]$RequireGemRateRefresh,
    [ValidateSet('top100', 'top300', 'top350', 'top100_plus_200', 'reserve50')]
    [string]$PresentationView = 'top300',
    [string]$Identifier,
    [ValidateSet('json', 'markdown', 'html')]
    [string]$Format = 'html',
    [string]$Output,
    [switch]$Check,
    [switch]$Publish,
    [string]$BootstrapArchive,
    [switch]$RestoreOverwrite
)

$Arguments = @((Join-Path $PSScriptRoot 'backend.py'), $Action.ToLowerInvariant())
if ($ExternalDb) { $Arguments += '--external-db' }
if ($Action -eq 'Daily') { $Arguments += @('--mode', $Mode) }
if ($RefreshActiveUniverse) { $Arguments += '--refresh-active-universe' }
if ($RequireGemRateRefresh) { $Arguments += '--require-gemrate-refresh' }
if ($Action -in @('Daily', 'Audit')) { $Arguments += @('--presentation-view', $PresentationView) }
if ($Identifier) { $Arguments += $Identifier }
if ($Action -eq 'Graph') { $Arguments += @('--format', $Format) }
if ($Output) { $Arguments += @('--output', $Output) }
if ($Check) { $Arguments += '--check' }
if ($Publish) { $Arguments += '--publish' }
if ($BootstrapArchive) { $Arguments += @('--bootstrap-archive', $BootstrapArchive) }
if ($RestoreOverwrite) { $Arguments += '--restore-overwrite' }
& python @Arguments
exit $LASTEXITCODE
