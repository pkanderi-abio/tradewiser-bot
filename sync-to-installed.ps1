# sync-to-installed.ps1
# Syncs source files to the installed TradeWiser directory and restarts the service.
#
# Modes:
#   .\sync-to-installed.ps1                     # Sync all relevant files (app/ + windows_service.py)
#   .\sync-to-installed.ps1 -File <path>        # Sync a single file (used by Claude Code hook)
#   .\sync-to-installed.ps1 -NoRestart          # Skip the service restart
#
# Requires admin rights (writes to C:\Program Files (x86)\).

#Requires -RunAsAdministrator

param(
    [string]$File = "",
    [switch]$NoRestart
)

$ErrorActionPreference = "Stop"
$src = $PSScriptRoot
$dst = "C:\Program Files (x86)\TradeWiser\TradeWiser Bot"
$svcName = "TradeWiserBot"

# Files/dirs that get synced in full-sync mode (and which paths a hook is allowed to sync)
$syncRoots = @("app", "windows_service.py")

function Test-RelevantPath {
    param([string]$relPath)
    foreach ($root in $syncRoots) {
        if ($relPath -eq $root -or $relPath.StartsWith($root + "\") -or $relPath.StartsWith($root + "/")) {
            return $true
        }
    }
    return $false
}

function Copy-One {
    param([string]$relPath)
    $srcPath = Join-Path $src $relPath
    $dstPath = Join-Path $dst $relPath

    if (-not (Test-Path $srcPath)) {
        Write-Host "  [skip] $relPath (not in source)" -ForegroundColor Yellow
        return
    }

    $dstDir = Split-Path -Parent $dstPath
    if (-not (Test-Path $dstDir)) {
        New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
    }

    if (Test-Path $srcPath -PathType Container) {
        # Mirror directory, excluding caches and tests
        & robocopy $srcPath $dstPath /MIR /XD __pycache__ .pytest_cache /XF *.pyc *.pyo /NJH /NJS /NDL /NC /NS /NP | Out-Null
    } else {
        Copy-Item $srcPath $dstPath -Force
    }
    Write-Host "  [sync] $relPath" -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Resolve target paths
# ---------------------------------------------------------------------------
$targets = @()

if ($File) {
    # Single-file mode (used by hook). Convert absolute -> relative if needed.
    $rel = $File
    if ([System.IO.Path]::IsPathRooted($File)) {
        $srcRoot = $src.TrimEnd('\') + '\'
        if ($File.StartsWith($srcRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
            $rel = $File.Substring($srcRoot.Length)
        } else {
            # File is outside the project - silently skip (hook will receive paths from other projects too)
            exit 0
        }
    }

    if (-not (Test-RelevantPath $rel)) {
        # Not in app/ or windows_service.py - skip silently
        exit 0
    }

    $targets = @($rel)
} else {
    $targets = $syncRoots
}

# ---------------------------------------------------------------------------
# Copy
# ---------------------------------------------------------------------------
foreach ($t in $targets) { Copy-One $t }

# ---------------------------------------------------------------------------
# Restart service
# ---------------------------------------------------------------------------
if (-not $NoRestart) {
    $svc = Get-Service $svcName -ErrorAction SilentlyContinue
    if ($null -eq $svc) {
        Write-Host "  [warn] Service $svcName not installed - run .\deploy.ps1 first" -ForegroundColor Yellow
    } elseif ($svc.Status -eq 'Running') {
        Write-Host "  [restart] $svcName" -ForegroundColor Cyan
        Restart-Service $svcName -Force
    } else {
        Write-Host "  [start] $svcName (was $($svc.Status))" -ForegroundColor Cyan
        Start-Service $svcName
    }
}
