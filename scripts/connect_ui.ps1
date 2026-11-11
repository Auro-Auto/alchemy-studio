# Keep this terminal open while using the remote ComfyUI.
param([string]$SshAlias = 'my-gpu-pc', [int]$LocalPort = 18888)
Write-Host "ComfyUI: http://127.0.0.1:$LocalPort"
& ssh -N -L "127.0.0.1:${LocalPort}:127.0.0.1:8188" -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 $SshAlias
exit $LASTEXITCODE
