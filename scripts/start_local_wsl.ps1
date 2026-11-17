# Starts ComfyUI + Alchemy Studio inside WSL2 (Ubuntu-22.04) and opens the local browser.
param(
    [string]$Distro = 'Ubuntu-22.04',
    [int]$Port = 8890,
    [switch]$LowVram
)

$ErrorActionPreference = 'Stop'
$studioUrl = "http://127.0.0.1:$Port"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if ($projectRoot -match '^([A-Za-z]):\\(.*)$') {
    $drive = $Matches[1].ToLowerInvariant()
    $rest = $Matches[2].Replace('\', '/')
    $wslProjectRoot = "/mnt/$drive/$rest"
} else {
    throw "Unsupported project path for WSL mount: $projectRoot"
}

Write-Host "Alchemy Studio (WSL2 Local): $studioUrl"

$studioAlreadyUp = $false
try {
    $studioHealth = Invoke-RestMethod -Uri "$studioUrl/api/status" -TimeoutSec 3
    if ($null -ne $studioHealth.comfy_connected) {
        if ($studioHealth.comfy_connected) {
            Start-Process $studioUrl
            Write-Host 'Studio is already running and connected to ComfyUI. Opened in browser.'
            exit 0
        }
        $studioAlreadyUp = $true
        Write-Host 'Studio port is already responding; waiting for ComfyUI backend...'
    }
} catch { }

Write-Host 'Starting ComfyUI + Studio in WSL2. Keep this window open while using the app (Ctrl+C to stop).'

$browserWaiter = Start-Job -ArgumentList $studioUrl -ScriptBlock {
    param($targetUrl)
    for ($attempt = 0; $attempt -lt 180; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$targetUrl/api/status" -TimeoutSec 2
            if ($health.comfy_connected -eq $true) {
                Start-Process $targetUrl
                return
            }
        } catch { }
        Start-Sleep -Seconds 1
    }
}

$comfyFlags = '--listen 127.0.0.1 --port 8188 --disable-auto-launch --reserve-vram 1.0 --disable-api-nodes'
if ($LowVram) {
    $comfyFlags += ' --disable-dynamic-vram --lowvram --cpu-vae'
}

$escapedWslRoot = $wslProjectRoot.Replace("'", "'\''")
$uvicornStep = if ($studioAlreadyUp) {
    'echo "[2/2] Studio is already running on http://127.0.0.1:' + $Port + '; keeping ComfyUI alive (Ctrl+C to stop)."' + "`n" + 'wait'
} else {
    'echo "[2/2] Starting Alchemy Studio on http://127.0.0.1:' + $Port + ' ..."' + "`n" + "COMFY_SERVER=http://127.0.0.1:8188 DO_NOT_TRACK=1 .venv/bin/python -m uvicorn studio.app:app --host 127.0.0.1 --port $Port"
}

$bashCmd = @(
    'set -euo pipefail',
    'export PATH="$HOME/.local/node22/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wsl/lib"',
    "cd '$escapedWslRoot'",
    'mkdir -p logs',
    'COMFY_PID=""',
    'cleanup() { if [ -n "$COMFY_PID" ] && kill -0 "$COMFY_PID" 2>/dev/null; then kill "$COMFY_PID" 2>/dev/null || true; fi; }',
    'trap cleanup EXIT INT TERM',
    'if ! curl -sf http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then',
    '  echo "[1/2] Starting ComfyUI backend (first cold load may take ~60-90s)..."',
    "  (cd backend/ComfyUI && HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 ../../.venv/bin/python main.py $comfyFlags > ../../logs/comfyui.log 2>&1) &",
    '  COMFY_PID=$!',
    '  COMFY_READY=0',
    '  for i in $(seq 1 150); do',
    '    if curl -sf http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then COMFY_READY=1; break; fi',
    '    if ! kill -0 "$COMFY_PID" 2>/dev/null; then echo "ComfyUI exited unexpectedly. Check logs/comfyui.log" >&2; exit 1; fi',
    '    sleep 1',
    '  done',
    '  if [ "$COMFY_READY" -ne 1 ]; then echo "ComfyUI timed out waiting to become ready. Check logs/comfyui.log" >&2; exit 1; fi',
    'else',
    '  echo "[1/2] ComfyUI backend is already running."',
    'fi',
    $uvicornStep
) -join "`n"

try {
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($bashCmd))
    & wsl -d $Distro -- bash -lc "echo $encoded | base64 -d | bash"
    $wslExitCode = $LASTEXITCODE
} finally {
    Stop-Job -Job $browserWaiter -ErrorAction SilentlyContinue
    Remove-Job -Job $browserWaiter -Force -ErrorAction SilentlyContinue
}

exit $wslExitCode
