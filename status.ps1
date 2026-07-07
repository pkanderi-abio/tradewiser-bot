# TradeWiser Bot - Status Dashboard (PowerShell 5.1 compatible)
# Run: .\status.ps1   or right-click -> Run with PowerShell

param(
    [string]$ApiKey  = "",
    [string]$BaseUrl = "http://127.0.0.1:8000"
)

# Resolve API key: parameter > env var > installed .env file
if (-not $ApiKey) { $ApiKey = $env:BOT_API_KEY }
if (-not $ApiKey) {
    $envPaths = @(
        "C:\Program Files (x86)\TradeWiser\TradeWiser Bot\.env",
        "$PSScriptRoot\.env"
    )
    foreach ($envPath in $envPaths) {
        if (Test-Path $envPath) {
            $line = Get-Content $envPath | Select-String "^BOT_API_KEY=" | Select-Object -First 1
            if ($line) { $ApiKey = $line.ToString().Split("=", 2)[1].Trim(); break }
        }
    }
}

function Write-Section($text) {
    Write-Host ""
    Write-Host "  $text" -ForegroundColor Cyan
    $line = "-" * $text.Length
    Write-Host "  $line" -ForegroundColor DarkGray
}

function Write-OK($label, $value) {
    Write-Host "  [OK] ${label}: ${value}" -ForegroundColor Green
}

function Write-Warn($label, $value) {
    Write-Host "  [!!] ${label}: ${value}" -ForegroundColor Yellow
}

function Write-Fail($label, $value) {
    Write-Host "  [XX] ${label}: ${value}" -ForegroundColor Red
}

function Write-Hint($text) {
    Write-Host "       --> $text" -ForegroundColor DarkYellow
}

function Invoke-BotApi($path) {
    $headers = @{}
    if ($ApiKey) { $headers["X-API-Key"] = $ApiKey }
    try {
        return Invoke-RestMethod -Uri ($BaseUrl + $path) -Headers $headers -TimeoutSec 15 -ErrorAction Stop
    } catch {
        return $null
    }
}

# ---------------------------------------------------------------------------
# Banner
# ---------------------------------------------------------------------------
Clear-Host
Write-Host ""
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "     TradeWiser Bot  -  Status Dashboard      " -ForegroundColor Cyan
Write-Host "  ============================================" -ForegroundColor Cyan
Write-Host "  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')" -ForegroundColor DarkGray

# ---------------------------------------------------------------------------
# 1. Windows Service
# ---------------------------------------------------------------------------
Write-Section "1. Windows Service"

$svc = Get-Service -Name "TradeWiserBot" -ErrorAction SilentlyContinue
if ($null -eq $svc) {
    Write-Fail "TradeWiserBot" "NOT INSTALLED"
    Write-Hint "Run the MSI installer first"
} elseif ($svc.Status -eq "Running") {
    Write-OK "TradeWiserBot" "RUNNING"
} elseif ($svc.Status -eq "Stopped") {
    Write-Fail "TradeWiserBot" "STOPPED"
    Write-Hint "Start-Service TradeWiserBot"
} else {
    Write-Warn "TradeWiserBot" $svc.Status
}

# ---------------------------------------------------------------------------
# 2. HTTP API
# ---------------------------------------------------------------------------
Write-Section "2. HTTP API"

$health = Invoke-BotApi "/health/"
if ($health -and $health.status -eq "ok") {
    Write-OK "Bot API" "Responding on $BaseUrl"
} else {
    Write-Fail "Bot API" "Not responding on $BaseUrl"
    Write-Hint "Check the service is running and port 8000 is free"
}

# ---------------------------------------------------------------------------
# 3. Alpaca Connection
# ---------------------------------------------------------------------------
Write-Section "3. Alpaca Brokerage Connection"

$orders   = Invoke-BotApi "/trades/current"
$alpacaOk = $false

if ($null -eq $orders) {
    Write-Fail "Alpaca API" "Unreachable (HTTP API is down)"
} elseif ($orders.PSObject.Properties.Name -contains "detail") {
    Write-Fail "Alpaca API" "Authentication failed"
    Write-Hint "Edit .env and set ALPACA_API_KEY and ALPACA_SECRET_KEY"
    Write-Hint "Restart with: Restart-Service TradeWiserBot"
    Write-Host ""
    Write-Host "  .env location: C:\Program Files (x86)\TradeWiser\TradeWiser Bot\.env" -ForegroundColor DarkGray
    Write-Host "  Get free paper keys at: https://app.alpaca.markets" -ForegroundColor DarkGray
} else {
    $alpacaOk  = $true
    Write-OK "Alpaca API" "Authenticated successfully"
    $openCount = $orders.orders.Count
    if ($openCount -gt 0) {
        Write-OK "Open orders" "$openCount order(s) currently open at broker"
    } else {
        Write-OK "Open orders" "None (flat position)"
    }
}

# ---------------------------------------------------------------------------
# 4. Strategy & Live Signals
# ---------------------------------------------------------------------------
Write-Section "4. Trading Strategy and Live Signals"

$strategy = Invoke-BotApi "/trades/strategy/status"
if ($null -eq $strategy) {
    Write-Warn "Strategy" "Unavailable"
} else {
    $p = $strategy.parameters
    $isShortTermOpts = ($p.type -eq "short_term_options") -or ($strategy.strategy -like "*Short*Options*") -or ($strategy.strategy -like "*RSI*")
    if ($isShortTermOpts) {
        Write-OK "Strategy" "Short Term Options (RSI → ATM call options)"
        Write-OK "Instrument" "Always ATM calls (short-term leveraged)"
        if ($p.option_weeks_out) { Write-OK "Option weeks out" $p.option_weeks_out }
        if ($p.profit_target) { Write-OK "Profit target" $p.profit_target }
        if ($p.stop_loss) { Write-OK "Stop loss" $p.stop_loss }
    } else {
        Write-OK "Strategy"       "Momentum"
        Write-OK "Window"         "$($p.window) price ticks (collected every 5s)"
        $buyPct  = [math]::Round($p.buy_threshold  * 100, 2)
        $sellPct = [math]::Round($p.sell_threshold * 100, 2)
        Write-OK "Buy threshold"  "+${buyPct}% momentum across window"
        Write-OK "Sell threshold" "${sellPct}% momentum across window"
    }

    # Positions
    Write-Host ""
    Write-Host "  Current positions:" -ForegroundColor DarkGray
    $hasPositions = $false
    foreach ($sym in $strategy.positions.PSObject.Properties) {
        $qty = $sym.Value
        if ($qty -gt 0) {
            $hasPositions = $true
            $name = $sym.Name.PadRight(32)
            Write-Host "    $name $qty held" -ForegroundColor Green
        }
    }
    if (-not $hasPositions) {
        Write-Host "    (no open positions)" -ForegroundColor DarkGray
    }

    # Live momentum
    $mdProps = $strategy.momentum_data.PSObject.Properties | Measure-Object
    if ($mdProps.Count -gt 0) {
        Write-Host ""
        Write-Host "  Live momentum readings:" -ForegroundColor DarkGray
        $header = "    {0,-28} {1,8}  {2,10}  {3,6}  {4}" -f "Symbol","Price","Momentum","Ticks","Signal"
        Write-Host $header -ForegroundColor DarkGray
        Write-Host "    $('-' * 65)" -ForegroundColor DarkGray
        foreach ($sym in $strategy.momentum_data.PSObject.Properties) {
            $d      = $sym.Value
            $ticks  = "$($d.data_points)/$($p.window)"
            $signal = if ($d.should_buy) { "BUY SIGNAL" } elseif ($d.should_sell) { "SELL SIGNAL" } else { "-" }
            $color  = if ($d.should_buy) { "Green" }      elseif ($d.should_sell) { "Red" }         else { "DarkGray" }
            $row = "    {0,-28} {1,8:N2}  {2,10}  {3,6}  {4}" -f $sym.Name, $d.current_price, $d.momentum_percent, $ticks, $signal
            Write-Host $row -ForegroundColor $color
        }
    } else {
        Write-Warn "Momentum data" "Not yet collected"
        Write-Hint "Wait ~30 seconds then re-run status.ps1"
    }
}

# ---------------------------------------------------------------------------
# 5. Profit & Loss
# ---------------------------------------------------------------------------
Write-Section "5. Profit & Loss"

$pnl = Invoke-BotApi "/trades/pnl"
if ($null -eq $pnl) {
    Write-Warn "P&L" "Unavailable"
} else {
    $acct = $pnl.account

    # Account summary
    $equity    = [math]::Round($acct.equity, 2)
    $cash      = [math]::Round($acct.cash, 2)
    $dayPl     = [math]::Round($acct.day_pl, 2)
    $dayPlPct  = [math]::Round($acct.day_plpc, 2)
    $unrlPl    = [math]::Round($acct.unrealized_pl, 2)
    $unrlPlPct = [math]::Round($acct.unrealized_plpc * 100, 2)
    $realPl    = [math]::Round($pnl.realized_pl, 2)

    $dayColor  = if ($dayPl -ge 0) { "Green" } else { "Red" }
    $unrlColor = if ($unrlPl -ge 0) { "Green" } else { "Red" }
    $realColor = if ($realPl -ge 0) { "Green" } else { "Red" }

    Write-OK  "Portfolio equity"   "`$$equity"
    Write-OK  "Cash available"     "`$$cash"

    $dayStr  = if ($dayPl -ge 0) { "+`$$dayPl (+${dayPlPct}%)" } else { "`$$dayPl (${dayPlPct}%)" }
    $unrlStr = if ($unrlPl -ge 0) { "+`$$unrlPl (+${unrlPlPct}%)" } else { "`$$unrlPl (${unrlPlPct}%)" }
    $realStr = if ($realPl -ge 0) { "+`$$realPl" } else { "`$$realPl" }

    Write-Host "  [P&L] Today's P&L:        $dayStr"  -ForegroundColor $dayColor
    Write-Host "  [P&L] Unrealized P&L:     $unrlStr" -ForegroundColor $unrlColor
    Write-Host "  [P&L] Realized P&L (session): $realStr" -ForegroundColor $realColor

    # Per-position breakdown
    if ($pnl.open_positions -gt 0) {
        Write-Host ""
        Write-Host "  Open positions:" -ForegroundColor DarkGray
        $hdr = "    {0,-28} {1,6}  {2,10}  {3,10}  {4,10}  {5}" -f "Symbol","Qty","Entry","Current","Unrl P&L","Today %"
        Write-Host $hdr -ForegroundColor DarkGray
        Write-Host "    $('-' * 75)" -ForegroundColor DarkGray
        foreach ($pos in $pnl.positions) {
            $upl    = [math]::Round($pos.unrealized_pl, 2)
            $uplPct = [math]::Round($pos.unrealized_intraday_plpc * 100, 2)
            $col    = if ($upl -ge 0) { "Green" } else { "Red" }
            $uplStr = if ($upl -ge 0) { "+`$$upl" } else { "`$$upl" }
            $row = "    {0,-28} {1,6}  {2,10:N2}  {3,10:N2}  {4,10}  {5,6}%" -f `
                $pos.symbol, $pos.qty, $pos.avg_entry_price, $pos.current_price, $uplStr, $uplPct
            Write-Host $row -ForegroundColor $col
        }
    } else {
        Write-Host "    (no open positions)" -ForegroundColor DarkGray
    }
}

# ---------------------------------------------------------------------------
# 6. Recent Trades
# ---------------------------------------------------------------------------
Write-Section "6. Recent Trades (last 10)"

$audit = Invoke-BotApi "/trades/audit?limit=10"
if ($null -eq $audit) {
    Write-Warn "Audit log" "Unavailable"
} elseif ($audit.audit.Count -eq 0) {
    Write-Warn "Audit log" "No trades yet"
    if ($strategy -and $strategy.parameters) {
        $window = $strategy.parameters.window
    } else {
        $window = 5
    }
    $wait = $window * 5
    Write-Hint "Bot needs $window full price ticks before its first signal (~${wait}s from startup)"
} else {
    Write-OK "Total trades logged" $audit.audit.Count
    Write-Host ""
    $hdr = "    {0,-4} {1,-8} {2,-6} {3,-5} {4,-10} {5}" -f "ID","Symbol","Side","Qty","Status","Time (UTC)"
    Write-Host $hdr -ForegroundColor DarkGray
    Write-Host "    $('-' * 55)" -ForegroundColor DarkGray
    $sorted = $audit.audit | Sort-Object -Property id -Descending
    foreach ($e in $sorted) {
        $color = if ($e.side -eq "BUY") { "Green" } elseif ($e.side -in @("SELL","SHORT")) { "Red" } else { "White" }
        if ($e.submitted_at -and $e.submitted_at.Length -ge 19) {
            $ts = $e.submitted_at.Substring(0,19).Replace("T"," ")
        } else {
            $ts = "-"
        }
        $left  = "    {0,-4} {1,-8} " -f $e.id, $e.symbol
        $mid   = "{0,-6}" -f $e.side
        $right = " {0,-5} {1,-10} {2}" -f $e.quantity, $e.status, $ts
        Write-Host $left -NoNewline
        Write-Host $mid  -ForegroundColor $color -NoNewline
        Write-Host $right
    }
}

# ---------------------------------------------------------------------------
# 7. Windows Event Log
# ---------------------------------------------------------------------------
Write-Section "7. Recent Windows Event Log (last 5)"

try {
    $events = Get-EventLog -LogName Application -Source "TradeWiserBot" -Newest 5 -ErrorAction Stop
    foreach ($ev in $events) {
        $ts  = $ev.TimeGenerated.ToString("yyyy-MM-dd HH:mm:ss")
        $msg = $ev.Message.Split("`n")[0].Trim()
        $col = if ($ev.EntryType -eq "Error") { "Red" } elseif ($ev.EntryType -eq "Warning") { "Yellow" } else { "DarkGray" }
        Write-Host "  [$ts] $($ev.EntryType.ToString().PadRight(7)) $msg" -ForegroundColor $col
    }
} catch {
    Write-Host "  (No entries in Application event log for TradeWiserBot)" -ForegroundColor DarkGray
}

# ---------------------------------------------------------------------------
# 8. NewsEventStrategy (Phase 2-6 news-driven multi-day strategy)
# ---------------------------------------------------------------------------
Write-Section "8. NewsEventStrategy (news-driven multi-day)"

$news = Invoke-BotApi "/trades/news-strategy?window_days=30"
if ($null -eq $news) {
    Write-Warn "News strategy" "Unavailable (endpoint not responding)"
} else {
    $s = $news.strategy
    $x = $news.extractor
    $c = $news.calibration

    if ($s.enabled) {
        Write-OK "Feature flag" "ENABLED"
    } else {
        Write-Warn "Feature flag" "DISABLED (exits still evaluated on legacy positions)"
        Write-Hint "Set NEWS_STRATEGY_ENABLED=true in .env to allow new entries"
    }

    Write-OK "LLM provider" "$($x.provider) / $($x.model)"
    if ($x.kill_switch) {
        Write-Warn "Kill switch" "ACTIVE - extractor returning empty"
    }
    if ($x.circuit.state -ne "closed") {
        Write-Warn "Extractor circuit" "$($x.circuit.state) (consecutive_failures=$($x.circuit.consecutive_failures))"
        if ($x.last_error) {
            Write-Host "       last_error: $($x.last_error)" -ForegroundColor DarkYellow
        }
    }

    Write-OK "Entry threshold"    "sev >= $($s.min_severity_to_enter) (stock) | sev >= $($s.min_severity_for_options) (options)"
    Write-OK "Exits"               "-$([math]::Round($s.stop_pct*100,1))% stop | +$([math]::Round($s.target_pct*100,1))% target | $($s.hold_days)d time"
    Write-OK "Concurrent slots"    "max $($s.max_concurrent)"

    # Open positions
    Write-Host ""
    Write-Host "  Open news-strategy positions:" -ForegroundColor DarkGray
    if ($news.open_positions -and $news.open_positions.Count -gt 0) {
        $hdr = "    {0,-24} {1,-6} {2,-8} {3,7}  {4,7}  {5,-8} {6,-12} {7}" -f "Symbol","Inst","State","Entry","Shares","Hold-until","Event","Sev"
        Write-Host $hdr -ForegroundColor DarkGray
        Write-Host "    $('-' * 90)" -ForegroundColor DarkGray
        foreach ($p in $news.open_positions) {
            $entry = if ($p.entry_price) { "{0:N2}" -f $p.entry_price } else { "-" }
            $shares = if ($p.shares) { $p.shares } else { "-" }
            $sev = if ($null -ne $p.entry_severity) { "{0:N1}" -f $p.entry_severity } else { "-" }
            $col = if ($p.state -eq "open") { "Green" } else { "Yellow" }
            $row = "    {0,-24} {1,-6} {2,-8} {3,7}  {4,7}  {5,-8} {6,-12} {7}" -f `
                $p.symbol, $p.instrument, $p.state, $entry, $shares, $p.hold_until, $p.entry_event_type, $sev
            Write-Host $row -ForegroundColor $col
        }
    } else {
        Write-Host "    (no open news-strategy positions)" -ForegroundColor DarkGray
    }

    # 30-day calibration snapshot
    Write-Host ""
    Write-Host "  30-day calibration:" -ForegroundColor DarkGray
    $pnlStr = if ($c.total_realized_pnl -ge 0) { "+`$$([math]::Round($c.total_realized_pnl,2))" } else { "`$$([math]::Round($c.total_realized_pnl,2))" }
    $pnlCol = if ($c.total_realized_pnl -ge 0) { "Green" } else { "Red" }
    Write-Host "    Closed positions : $($c.n_positions_closed)" -ForegroundColor White
    Write-Host "    Hit rate         : $([math]::Round($c.hit_rate*100,1))%" -ForegroundColor White
    Write-Host "    Total P&L        : $pnlStr" -ForegroundColor $pnlCol
    Write-Host "    Mean % return    : $([math]::Round($c.mean_pct_return*100,2))%" -ForegroundColor White
    Write-Host "    Events scored    : $($c.total_events) (error rate $([math]::Round($c.error_rate_events*100,1))%)" -ForegroundColor DarkGray
    foreach ($note in $c.notes) {
        Write-Host "    ! $note" -ForegroundColor Yellow
    }
}

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
$apiOk = ($health -and $health.status -eq "ok")
$svcOk = ($svc -and $svc.Status -eq "Running")

Write-Host ""
Write-Host "  ============================================" -ForegroundColor DarkGray

if ($svcOk -and $apiOk -and $alpacaOk) {
    Write-Host "  STATUS: ACTIVE - Watching markets and trading" -ForegroundColor Green
} elseif ($svcOk -and $apiOk) {
    Write-Host "  STATUS: RUNNING - Alpaca auth failed, no trades will execute" -ForegroundColor Yellow
    Write-Host "  ACTION: Fix .env credentials then Restart-Service TradeWiserBot" -ForegroundColor Yellow
} elseif ($svcOk) {
    Write-Host "  STATUS: SERVICE UP but HTTP API not responding on port 8000" -ForegroundColor Yellow
} else {
    Write-Host "  STATUS: OFFLINE" -ForegroundColor Red
}

Write-Host ""
Write-Host "  Quick commands:" -ForegroundColor DarkGray
Write-Host "    Start service:   Start-Service TradeWiserBot"                             -ForegroundColor DarkGray
Write-Host "    Stop service:    Stop-Service TradeWiserBot"                              -ForegroundColor DarkGray
Write-Host "    Restart service: Restart-Service TradeWiserBot"                           -ForegroundColor DarkGray
Write-Host "    Live event log:  Get-EventLog -LogName Application -Source TradeWiserBot" -ForegroundColor DarkGray
Write-Host "    API browser UI:  Start-Process http://127.0.0.1:8000/docs"                -ForegroundColor DarkGray
Write-Host ""
