[CmdletBinding()]
param(
    [string]$OutputPath = "",
    [switch]$SkipBuild,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "Common.ps1")

try {
    $context = Initialize-MarsDeployment
    Assert-MarsOfflinePorts -Context $context
    Assert-MarsDocker
    Test-MarsComposeConfiguration -Context $context

    $targetPlatform = Get-MarsSetting -Name "MARS_DOCKER_PLATFORM" -Values $context.Settings -Default "linux/amd64"
    if ($targetPlatform -ne "linux/amd64") {
        throw "Windows 离线包只允许 linux/amd64，当前配置：$targetPlatform"
    }

    if (-not $OutputPath) {
        $OutputPath = Join-Path $PSScriptRoot "images\mars-windows-amd64.tar"
    }
    $OutputPath = [IO.Path]::GetFullPath($OutputPath)
    $hashPath = $OutputPath + ".sha256"
    foreach ($destination in @($OutputPath, $hashPath)) {
        if (Test-Path -LiteralPath $destination) {
            if (-not $Force) {
                throw "输出文件已存在：$destination。请指定新路径，或用 -Force 明确覆盖。"
            }
            $existing = Get-Item -LiteralPath $destination
            if ($existing.PSIsContainer -or ($existing.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                throw "输出路径不能是目录或链接：$destination"
            }
        }
    }
    $outputDirectory = Split-Path -Parent $OutputPath
    New-Item -ItemType Directory -Path $outputDirectory -Force | Out-Null

    $backendImage = Get-MarsSetting -Name "MARS_BACKEND_IMAGE" -Values $context.Settings -Default "mars-v31-backend:windows-amd64"
    $frontendImage = Get-MarsSetting -Name "MARS_FRONTEND_IMAGE" -Values $context.Settings -Default "mars-v31-frontend:windows-amd64"
    $redisImage = Get-MarsSetting -Name "MARS_REDIS_IMAGE" -Values $context.Settings -Default "redis:7.4-alpine"
    if (-not $SkipBuild) {
        if ($script:MarsDockerServerArch -eq "amd64") {
            $buildArguments = @("compose") + $context.ComposeArguments + @(
                "build", "--pull", "backend", "frontend"
            )
            Invoke-MarsDocker -Arguments $buildArguments -Description "Windows amd64 镜像构建"
        }
        else {
            & docker buildx version | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "当前构建机不是 amd64，且缺少 Docker Buildx。请安装完整 Docker Desktop 后重试。"
            }
            $backendBuildArgs = @(
                "buildx", "build", "--platform", $targetPlatform, "--pull", "--load",
                "--file", (Join-Path $context.RepoRoot "deploy\windows\Dockerfile.backend"),
                "--tag", $backendImage,
                "--build-arg", "MARS_PYTHON_IMAGE=$(Get-MarsSetting -Name 'MARS_PYTHON_IMAGE' -Values $context.Settings -Default 'python:3.11-slim-bookworm')",
                "--build-arg", "MARS_INSTALL_STATIC_ALPHA=$(Get-MarsSetting -Name 'MARS_INSTALL_STATIC_ALPHA' -Values $context.Settings -Default '1')",
                "--build-arg", "MARS_INSTALL_SYSTEM_TOOLS=$(Get-MarsSetting -Name 'MARS_INSTALL_SYSTEM_TOOLS' -Values $context.Settings -Default '1')",
                "--build-arg", "MARS_TORCH_VERSION=$(Get-MarsSetting -Name 'MARS_TORCH_VERSION' -Values $context.Settings -Default '2.12.1')",
                "--build-arg", "MARS_TORCH_WHEEL_BASE_URL=$(Get-MarsSetting -Name 'MARS_TORCH_WHEEL_BASE_URL' -Values $context.Settings -Default 'https://download.pytorch.org/whl/cpu')",
                "--build-arg", "MARS_UV_VERSION=$(Get-MarsSetting -Name 'MARS_UV_VERSION' -Values $context.Settings -Default '0.11.1')",
                $context.RepoRoot
            )
            Invoke-MarsDocker -Arguments $backendBuildArgs -Description "Backend amd64 交叉构建"
            $frontendBuildArgs = @(
                "buildx", "build", "--platform", $targetPlatform, "--pull", "--load",
                "--file", (Join-Path $context.RepoRoot "deploy\windows\Dockerfile.frontend"),
                "--tag", $frontendImage,
                "--build-arg", "MARS_NODE_IMAGE=$(Get-MarsSetting -Name 'MARS_NODE_IMAGE' -Values $context.Settings -Default 'node:20-alpine')",
                "--build-arg", "MARS_NPM_REGISTRY=$(Get-MarsSetting -Name 'MARS_NPM_REGISTRY' -Values $context.Settings -Default 'https://registry.npmjs.org')",
                "--build-arg", "BACKEND_URL=http://backend:8000",
                "--build-arg", "NEXT_PUBLIC_BACKEND_URL=http://127.0.0.1:$($context.BackendPort)",
                "--build-arg", "NEXT_PUBLIC_WS_URL=ws://127.0.0.1:$($context.BackendPort)",
                $context.RepoRoot
            )
            Invoke-MarsDocker -Arguments $frontendBuildArgs -Description "Frontend amd64 交叉构建"
        }
        Invoke-MarsDocker -Arguments @("pull", "--platform", $targetPlatform, $redisImage) -Description "Redis 镜像下载"
    }
    foreach ($image in @($backendImage, $frontendImage, $redisImage)) {
        Assert-MarsImagePlatform -Image $image -Expected $targetPlatform
    }
    $partialArchive = $OutputPath + ".partial-" + [guid]::NewGuid().ToString("N")
    $partialHash = $partialArchive + ".sha256"
    try {
        Invoke-MarsDocker -Arguments @(
            "save", "--output", $partialArchive, $backendImage, $frontendImage, $redisImage
        ) -Description "离线镜像导出"

        $hash = (Get-FileHash -Algorithm SHA256 -LiteralPath $partialArchive).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText($partialHash, "$hash  $([IO.Path]::GetFileName($OutputPath))`n")
        Move-Item -LiteralPath $partialArchive -Destination $OutputPath -Force:$Force
        Move-Item -LiteralPath $partialHash -Destination $hashPath -Force:$Force
    }
    finally {
        foreach ($partial in @($partialArchive, $partialHash)) {
            if (Test-Path -LiteralPath $partial -PathType Leaf) {
                [IO.File]::Delete($partial)
            }
        }
    }
    Write-Host "离线镜像包已生成：$OutputPath" -ForegroundColor Green
    Write-Host "SHA256：$hash"
    Write-Host "将 tar、sha256、mars_v2 和干净的 mars_v31_wireless 一起拷入内网。"
}
catch {
    Write-Host "离线镜像导出失败：$($_.Exception.Message)" -ForegroundColor Red
    exit 1
}
