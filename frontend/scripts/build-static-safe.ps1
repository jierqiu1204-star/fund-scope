param(
    [int]$TimeoutSeconds = 240
)

$ErrorActionPreference = "Stop"

$frontendDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$pwsh = "C:\Program Files\PowerShell\7\pwsh.exe"
if (-not (Test-Path $pwsh)) {
    $pwsh = "pwsh"
}

$command = '$env:NEXT_TELEMETRY_DISABLED="1"; corepack pnpm build:static'
$process = Start-Process `
    -FilePath $pwsh `
    -ArgumentList @("-NoLogo", "-NoProfile", "-Command", $command) `
    -WorkingDirectory $frontendDir `
    -NoNewWindow `
    -PassThru

try {
    if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
        & taskkill.exe /PID $process.Id /T /F | Out-Null
        throw "本地静态构建超过 ${TimeoutSeconds} 秒，已终止 node/next/pnpm 子进程树。请改用服务器 Docker 构建验证。"
    }
    exit $process.ExitCode
}
finally {
    $process.Dispose()
}
