#!/usr/bin/env pwsh
# CGX-QV unified launcher — starts VEYN core, Qallow, and MCP server together.
# Usage:  .\run.ps1 [-Mock] [-NoAuth] [-Sse] [-Release]
#   -Mock     Enable synthetic biometric data (default: true)
#   -NoAuth   Disable bearer-token auth (dev only)
#   -Sse      Run MCP server with SSE transport instead of stdio
#   -Release  Use release build of veyn-core (slower first build, faster runtime)

param(
    [switch]$Mock    = $true,
    [switch]$NoAuth,
    [switch]$Sse,
    [switch]$Release
)

$Root = $PSScriptRoot
Set-Location $Root

# ── Load .env ─────────────────────────────────────────────────────────────────
if (Test-Path "$Root\.env") {
    Get-Content "$Root\.env" | ForEach-Object {
        if ($_ -match '^\s*([^#=\s]+)\s*=\s*(.*)\s*$') {
            [System.Environment]::SetEnvironmentVariable($Matches[1], $Matches[2], 'Process')
        }
    }
}

if ($Mock)   { $env:VEYN_MOCK    = "true" }
if ($NoAuth) { $env:VEYN_NO_AUTH = "true" }

# ── Service launcher ──────────────────────────────────────────────────────────
# PS script blocks cannot be used as .NET event handlers (no Runspace on thread-
# pool threads). Instead each output stream gets its own PowerShell Runspace that
# reads lines synchronously and enqueues them for the main drain loop.
function Start-Service {
    param(
        [string]   $Name,
        [string]   $Dir,
        [string]   $Exe,
        [string[]] $CmdArgs,
        [string]   $Color
    )

    $queue = [System.Collections.Concurrent.ConcurrentQueue[string]]::new()

    $psi = [System.Diagnostics.ProcessStartInfo]::new()
    $psi.FileName               = $Exe
    $psi.Arguments              = ($CmdArgs -join ' ')
    $psi.WorkingDirectory       = $Dir
    $psi.UseShellExecute        = $false
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError  = $true
    $psi.CreateNoWindow         = $true

    # Propagate current process environment to the child.
    foreach ($key in [System.Environment]::GetEnvironmentVariables('Process').Keys) {
        $val = [System.Environment]::GetEnvironmentVariable($key, 'Process')
        if ($null -ne $val) { $psi.Environment[$key] = $val }
    }

    $proc = [System.Diagnostics.Process]::new()
    $proc.StartInfo = $psi
    $proc.Start() | Out-Null

    # One Runspace per stream so ReadLine() blocks without touching the main thread.
    $readerScript = {
        param($reader, $queue, $tag)
        try {
            while ($null -ne ($line = $reader.ReadLine())) {
                $queue.Enqueue("${tag}|${line}")
            }
        } catch { }
    }

    foreach ($pair in @(
        [pscustomobject]@{ Stream = $proc.StandardOutput; Tag = 'OUT' }
        [pscustomobject]@{ Stream = $proc.StandardError;  Tag = 'ERR' }
    )) {
        $rs = [runspacefactory]::CreateRunspace()
        $rs.Open()
        $ps = [powershell]::Create()
        $ps.Runspace = $rs
        $null = $ps.AddScript($readerScript).AddArgument($pair.Stream).AddArgument($queue).AddArgument($pair.Tag)
        $null = $ps.BeginInvoke()
    }

    return [pscustomobject]@{
        Name   = $Name
        Proc   = $proc
        Queue  = $queue
        Color  = $Color
        Prefix = '[' + ('{0,-7}' -f $Name) + ']'
    }
}

# ── Build args ────────────────────────────────────────────────────────────────
$veynArgs = if ($Release) { @('run', '--release', '-p', 'veyn-core') }
            else          { @('run', '-p', 'veyn-core') }
if ($NoAuth) { $veynArgs += @('--', '--no-auth') }

$mcpArgs = @('run', 'cgx-qv-mcp')
if ($Sse) { $mcpArgs += '--sse' }

# ── Start all services ────────────────────────────────────────────────────────
Write-Host ""
Write-Host "  CGX-QV  starting all services..." -ForegroundColor White
Write-Host "  VEYN    ->  http://localhost:7700" -ForegroundColor Cyan
Write-Host "  Qallow  ->  http://localhost:5000" -ForegroundColor Green
Write-Host "  MCP     ->  $(if ($Sse) { 'SSE  http://localhost:5001' } else { 'stdio transport' })" -ForegroundColor Yellow
Write-Host "  Press Ctrl+C to stop everything." -ForegroundColor DarkGray
Write-Host ""

$services = @(
    (Start-Service -Name 'veyn'   -Dir $Root                 -Exe 'cargo'  -CmdArgs $veynArgs         -Color 'Cyan')
    (Start-Service -Name 'qallow' -Dir "$Root\Qallow\python" -Exe 'python' -CmdArgs @('server.py')    -Color 'Green')
    (Start-Service -Name 'mcp'    -Dir "$Root\mcp"           -Exe 'uv'     -CmdArgs $mcpArgs           -Color 'Yellow')
)

# ── Drain loop ────────────────────────────────────────────────────────────────
try {
    while ($true) {
        $anyAlive = $false

        foreach ($svc in $services) {
            if (-not $svc.Proc.HasExited) { $anyAlive = $true }

            $item = $null
            while ($svc.Queue.TryDequeue([ref]$item)) {
                $tag  = $item.Substring(0, 3)   # 'OUT' or 'ERR'
                $text = $item.Substring(4)
                $color = if ($tag -eq 'ERR') { 'Red' } else { $svc.Color }
                Write-Host "$($svc.Prefix) $text" -ForegroundColor $color
            }
        }

        if (-not $anyAlive) {
            Write-Host "`n  All services have exited." -ForegroundColor DarkGray
            break
        }

        Start-Sleep -Milliseconds 10
    }
} finally {
    Write-Host "`n  Stopping all services..." -ForegroundColor DarkGray
    foreach ($svc in $services) {
        if (-not $svc.Proc.HasExited) {
            # /T kills the whole process tree (e.g. cargo -> veyn-core.exe).
            taskkill /PID $svc.Proc.Id /T /F 2>$null | Out-Null
        }
    }
    Write-Host "  Stopped." -ForegroundColor DarkGray
}
