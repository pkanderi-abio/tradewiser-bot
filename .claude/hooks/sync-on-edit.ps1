# Claude Code PostToolUse hook: sync edited app/ files to the installed
# TradeWiser directory and restart the service.
#
# Reads hook JSON from stdin (Claude Code spec):
#   { "tool_input": { "file_path": "..." }, "tool_response": { "filePath": "..." } }
#
# Silent on irrelevant files (anything outside app/ or windows_service.py).
# Errors are swallowed so a hook failure never blocks Claude.

$ErrorActionPreference = "Continue"

try {
    $payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
    $file = $payload.tool_response.filePath
    if (-not $file) { $file = $payload.tool_input.file_path }
    if (-not $file) { exit 0 }

    $repoRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
    $syncScript = Join-Path $repoRoot "sync-to-installed.ps1"
    if (-not (Test-Path $syncScript)) { exit 0 }

    & $syncScript -File $file 2>&1 | Out-Null
} catch {
    # Hook errors must not block Claude
}

exit 0
