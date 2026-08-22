# Shared helpers for CARDZ-037 daily-chain scripts.
# Loaded with:  . (Join-Path $PSScriptRoot "cardz_chain_lib.ps1")
#
# Invoke-CappedProcess: PS 5.1 Start-Process leaves ExitCode empty unless
# $p.Handle is touched BEFORE the process exits (see preflight_daily_chain.ps1).
# Refresh used to skip publish because accept's real exit 0 came back as $null.
#
# Wait-CardzChainIdle: mutating CARDZ-037 tasks must not overlap. Refresh that
# GET_LOCKs with timeout 0 while morning still holds the lease is not a
# publish retry, it is a collision.

function Invoke-CappedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$File,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][int]$Seconds,
        [string]$WorkingDirectory = "",
        [string]$LogPath = ""
    )
    $outFile = [System.IO.Path]::GetTempFileName()
    $errFile = [System.IO.Path]::GetTempFileName()
    try {
        $start = @{
            FilePath               = $File
            ArgumentList           = $Arguments
            PassThru               = $true
            NoNewWindow            = $true
            RedirectStandardOutput = $outFile
            RedirectStandardError  = $errFile
        }
        if (-not [string]::IsNullOrWhiteSpace($WorkingDirectory)) {
            $start.WorkingDirectory = $WorkingDirectory
        }
        $p = Start-Process @start
        # PS 5.1: ExitCode stays empty unless the handle was touched before exit.
        $null = $p.Handle
        if (-not $p.WaitForExit($Seconds * 1000)) {
            cmd.exe /c "taskkill /PID $($p.Id) /T /F" | Out-Null
            if ($LogPath) {
                "[timeout] $File $($Arguments -join ' ') after ${Seconds}s" | Add-Content -LiteralPath $LogPath
            }
            if ($LogPath -and (Test-Path -LiteralPath $outFile)) {
                Get-Content -LiteralPath $outFile -ErrorAction SilentlyContinue | Add-Content -LiteralPath $LogPath
            }
            if ($LogPath -and (Test-Path -LiteralPath $errFile)) {
                Get-Content -LiteralPath $errFile -ErrorAction SilentlyContinue | Add-Content -LiteralPath $LogPath
            }
            return 124
        }
        if ($LogPath -and (Test-Path -LiteralPath $outFile)) {
            Get-Content -LiteralPath $outFile -ErrorAction SilentlyContinue | Add-Content -LiteralPath $LogPath
        }
        if ($LogPath -and (Test-Path -LiteralPath $errFile)) {
            Get-Content -LiteralPath $errFile -ErrorAction SilentlyContinue | Add-Content -LiteralPath $LogPath
        }
        $code = $p.ExitCode
        if ($null -eq $code) { return -998 }
        return [int]$code
    }
    finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
}

function Test-MorningPcCapLog {
    param([Parameter(Mandatory = $true)][AllowEmptyString()][string]$Text)
    if ([string]::IsNullOrWhiteSpace($Text)) { return $false }
    return [bool]($Text -match 'force-network' -or $Text -match 'pc_cdp_fresh_pages')
}

function Wait-CardzChainIdle {
    param(
        [int]$WaitSeconds = 1500,
        [string[]]$ExcludeTaskNames = @(),
        [string]$LogPath = ""
    )
    $names = @(
        "CARDZ-037-Morning-Browser-Lanes",
        "CARDZ-037-Nightly-Collect-Accept",
        "CARDZ-037-Refresh-Publish"
    ) | Where-Object { $ExcludeTaskNames -notcontains $_ }

    function Get-Busy {
        $busy = @()
        foreach ($n in $names) {
            $t = Get-ScheduledTask -TaskName $n -ErrorAction SilentlyContinue
            if ($null -ne $t -and [string]$t.State -eq "Running") { $busy += $n }
        }
        return @($busy)
    }

    $busyNow = Get-Busy
    if ($busyNow.Count -eq 0) {
        return @{ Idle = $true; Reason = "idle"; Busy = @() }
    }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    if ($LogPath) {
        "[Wait-CardzChainIdle] busy=$($busyNow -join ',') wait=${WaitSeconds}s" | Add-Content -LiteralPath $LogPath
    }
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 15
        $busyNow = Get-Busy
        if ($busyNow.Count -eq 0) {
            return @{ Idle = $true; Reason = "became-idle"; Busy = @() }
        }
    }
    if ($LogPath) {
        "[Wait-CardzChainIdle] SKIPPED_BUSY still=$($busyNow -join ',')" | Add-Content -LiteralPath $LogPath
    }
    return @{ Idle = $false; Reason = "SKIPPED_BUSY"; Busy = @($busyNow) }
}
