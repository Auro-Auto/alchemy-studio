# Starts the existing remote app and keeps its SSH tunnel open. No password is stored.
param([string]$SshAlias = 'my-gpu-pc', [int]$LocalPort = 18890,
      [string]$RemoteDirectory = '~/projects/alchemy-lab')
$ErrorActionPreference = 'Stop'
$studioUrl = "http://127.0.0.1:$LocalPort"
Write-Host "Alchemy Studio: $studioUrl"
try {
    $studioHealth = Invoke-RestMethod -Uri "$studioUrl/api/status" -TimeoutSec 3
    if ($null -ne $studioHealth.comfy_connected) {
        Start-Process $studioUrl
        Write-Host 'Existing Studio connection opened in your browser.'
        exit 0
    }
} catch { }
Write-Host 'Keep this terminal open while presenting. Press Ctrl+C to close the tunnel.'
$browserWaiter = Start-Job -ArgumentList $studioUrl -ScriptBlock {
    param($targetUrl)
    for ($attempt = 0; $attempt -lt 120; $attempt++) {
        try {
            $health = Invoke-RestMethod -Uri "$targetUrl/api/status" -TimeoutSec 2
            if ($null -ne $health.comfy_connected) {
                Start-Process $targetUrl
                return
            }
        } catch { }
        Start-Sleep -Seconds 1
    }
}
try {
    # Quote the remote path as one shell argument. Resolve ~/ using the remote shell.
    if ($RemoteDirectory.StartsWith('~/')) {
        $remotePath = '$HOME/' + "'" + $RemoteDirectory.Substring(2).Replace("'", "'\''") + "'"
    } else {
        $remotePath = "'" + $RemoteDirectory.Replace("'", "'\''") + "'"
    }
    $remoteCommand = "cd -- $remotePath && .venv/bin/python scripts/start_studio.py && sleep infinity"
    & ssh -t -L "127.0.0.1:${LocalPort}:127.0.0.1:8890" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 $SshAlias $remoteCommand
    $sshExitCode = $LASTEXITCODE
} finally {
    Stop-Job -Job $browserWaiter -ErrorAction SilentlyContinue
    Remove-Job -Job $browserWaiter -Force -ErrorAction SilentlyContinue
}
exit $sshExitCode
