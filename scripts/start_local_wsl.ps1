# Starts the installed local project in WSL2 and opens its browser UI.
param(
    [string]$Distro = 'Ubuntu-22.04',
    [ValidateRange(1, 65535)][int]$Port = 8890,
    [switch]$LowVram
)

$ErrorActionPreference = 'Stop'
$studioUrl = "http://127.0.0.1:$Port"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path

if ($projectRoot -match '^\\\\(?:wsl\.localhost|wsl\$)\\([^\\]+)(\\.*)?$') {
    $pathDistro = $Matches[1]
    $linuxPath = $Matches[2]
    if ($PSBoundParameters.ContainsKey('Distro') -and $Distro -ne $pathDistro) {
        throw "The project belongs to WSL distribution '$pathDistro', but -Distro specifies '$Distro'."
    }
    $Distro = $pathDistro
    $wslProjectRoot = if ($linuxPath) { $linuxPath.Replace('\', '/') } else { '/' }
} elseif ($projectRoot -match '^([A-Za-z]):\\(.*)$') {
    $wslProjectRoot = & wsl -d $Distro --exec wslpath -a -u $projectRoot
    if ($LASTEXITCODE -ne 0 -or -not $wslProjectRoot) {
        throw "Could not resolve the project path inside WSL distribution '$Distro'."
    }
    $wslProjectRoot = ($wslProjectRoot -join '').Trim()
} else {
    throw 'Use a Windows drive path or a project under \\wsl.localhost\DISTRO\ or \\wsl$\DISTRO\.'
}

function Invoke-WslBash([string]$Script) {
    $encoded = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($Script))
    & wsl -d $Distro -- bash -lc "echo $encoded | base64 -d | bash"
}

$escapedWslRoot = $wslProjectRoot.Replace("'", "'\''")
$preflightCode = @'
import json
from pathlib import Path
import socket
import sys
from urllib.request import urlopen

def occupied(port):
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) == 0

root = Path.cwd()
if not (root / "web/dist/index.html").is_file():
    raise SystemExit("Missing frontend build. Run npm ci && npm run build in web/ first.")

backend_ready = occupied(8188)
if backend_ready:
    try:
        with urlopen("http://127.0.0.1:8188/system_stats", timeout=3) as response:
            info = json.load(response)
        if info.get("system", {}).get("comfyui_version") != "0.44.0":
            raise ValueError("expected ComfyUI 0.44.0")
    except (OSError, ValueError, AttributeError) as error:
        raise SystemExit(f"Port 8188 is occupied by an unverified service: {error}")

studio_ready = occupied(int(sys.argv[1]))
if studio_ready:
    try:
        with urlopen(f"http://127.0.0.1:{sys.argv[1]}/api/status", timeout=5) as response:
            status = json.load(response)
        if not isinstance(status, dict) or not isinstance(status.get("comfy_connected"), bool):
            raise ValueError("unexpected Studio response")
    except (OSError, ValueError, AttributeError) as error:
        raise SystemExit(f"Studio port is occupied by an unverified service: {error}")

print(json.dumps({"backend_ready": backend_ready, "studio_ready": studio_ready}))
'@

$preflightShell = @(
    'set -euo pipefail',
    "cd -- '$escapedWslRoot'",
    'test -x .venv/bin/python || { echo "Run bash scripts/install.sh inside WSL first." >&2; exit 1; }',
    ".venv/bin/python - '$Port' <<'PY_ALCHEMY'",
    $preflightCode,
    'PY_ALCHEMY'
) -join [char]10

$preflightJson = Invoke-WslBash $preflightShell
if ($LASTEXITCODE -ne 0) { throw 'WSL preflight failed. Resolve the error above before starting the application.' }
$state = ($preflightJson -join [char]10) | ConvertFrom-Json
if ($LowVram -and $state.backend_ready) {
    throw '-LowVram configures a new backend. Stop the existing ComfyUI after its tasks finish, then rerun this command.'
}
if ($state.backend_ready -and $state.studio_ready) {
    Start-Process $studioUrl
    Write-Host 'Verified ComfyUI 0.44.0 and the existing Studio. Opened in browser.'
    exit 0
}

Write-Host "Alchemy Studio (WSL2 Local): $studioUrl"
Write-Host 'Keep this terminal open while using the app. Ctrl+C stops services started by this command.'

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
if ($LowVram) { $comfyFlags += ' --disable-dynamic-vram --lowvram --cpu-vae' }
$backendStep = if ($state.backend_ready) {
    'echo "[1/2] Using the verified ComfyUI backend."'
} else {
    @(
        'echo "[1/2] Starting ComfyUI backend..."',
        "  (cd backend/ComfyUI && exec env HF_HUB_DISABLE_TELEMETRY=1 DO_NOT_TRACK=1 ../../.venv/bin/python main.py $comfyFlags >> ../../logs/comfyui.log 2>&1) &",
        'COMFY_PID=$!',
        'COMFY_READY=0',
        'for i in $(seq 1 150); do',
        '  if .venv/bin/python -c "from scripts.start_backend import server_info; server_info()" >/dev/null 2>&1; then COMFY_READY=1; break; fi',
        '  if ! kill -0 "$COMFY_PID" 2>/dev/null; then echo "ComfyUI exited. Check logs/comfyui.log" >&2; exit 1; fi',
        '  sleep 1',
        'done',
        'if [ "$COMFY_READY" -ne 1 ]; then echo "ComfyUI startup timed out. Check logs/comfyui.log" >&2; exit 1; fi'
    ) -join [char]10
}

$studioStep = if ($state.studio_ready) {
    @(
        'echo "[2/2] Studio already exists. Keeping the backend started by this command alive."',
        'wait "$COMFY_PID"'
    ) -join [char]10
} else {
    @(
        'echo "[2/2] Starting Alchemy Studio..."',
        "COMFY_SERVER=http://127.0.0.1:8188 DO_NOT_TRACK=1 .venv/bin/python -m uvicorn studio.app:app --host 127.0.0.1 --port $Port"
    ) -join [char]10
}

$bashCmd = @(
    'set -euo pipefail',
    'export PATH="$HOME/.local/node22/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/wsl/lib"',
    "cd -- '$escapedWslRoot'",
    'mkdir -p logs',
    'COMFY_PID=""',
    'cleanup() {',
    '  trap - EXIT INT TERM',
    '  if [ -n "$COMFY_PID" ] && kill -0 "$COMFY_PID" 2>/dev/null; then',
    '    kill "$COMFY_PID" 2>/dev/null || true',
    '    for i in $(seq 1 50); do kill -0 "$COMFY_PID" 2>/dev/null || break; sleep 0.1; done',
    '    if kill -0 "$COMFY_PID" 2>/dev/null; then kill -KILL "$COMFY_PID" 2>/dev/null || true; fi',
    '    wait "$COMFY_PID" 2>/dev/null || true',
    '  fi',
    '}',
    'trap cleanup EXIT',
    "trap 'exit 130' INT",
    "trap 'exit 143' TERM",
    $backendStep,
    $studioStep
) -join [char]10

try {
    Invoke-WslBash $bashCmd
    $wslExitCode = $LASTEXITCODE
} finally {
    Stop-Job -Job $browserWaiter -ErrorAction SilentlyContinue
    Remove-Job -Job $browserWaiter -Force -ErrorAction SilentlyContinue
}
exit $wslExitCode
