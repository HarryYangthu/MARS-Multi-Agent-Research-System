Set-StrictMode -Version 3.0

$script:MarsDeployRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:MarsRepoRoot = [IO.Path]::GetFullPath((Join-Path $script:MarsDeployRoot "..\.."))

function Read-MarsDotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $values = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $values
    }

    foreach ($rawLine in [IO.File]::ReadAllLines($Path)) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#") -or -not $line.Contains("=")) {
            continue
        }
        if ($line.StartsWith("export ")) {
            $line = $line.Substring(7).Trim()
        }
        $parts = $line.Split(@("="), 2, [StringSplitOptions]::None)
        $name = $parts[0].Trim()
        $value = $parts[1].Trim()
        if ($value.Length -ge 2) {
            $first = $value.Substring(0, 1)
            $last = $value.Substring($value.Length - 1, 1)
            if (($first -eq '"' -and $last -eq '"') -or ($first -eq "'" -and $last -eq "'")) {
                $value = $value.Substring(1, $value.Length - 2)
            }
        }
        if ($name) {
            $values[$name] = $value
        }
    }
    return $values
}

function Get-MarsSetting {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][hashtable]$Values,
        [string]$Default = ""
    )

    $processValue = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($processValue)) {
        return $processValue.Trim()
    }
    if ($Values.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace([string]$Values[$Name])) {
        return ([string]$Values[$Name]).Trim()
    }
    return $Default
}

function ConvertTo-MarsComposePath {
    param([Parameter(Mandatory = $true)][string]$Path)

    return [IO.Path]::GetFullPath($Path).Replace("\", "/")
}

function ConvertTo-MarsPort {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $port = 0
    if (-not [int]::TryParse($Value, [ref]$port) -or $port -lt 1 -or $port -gt 65535) {
        throw "$Name 必须是 1 到 65535 之间的端口，当前值：$Value"
    }
    return $port
}

function Initialize-MarsDeployment {
    param(
        [switch]$Production,
        [switch]$SkipMountValidation
    )

    $envPath = Join-Path $script:MarsDeployRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $script:MarsDeployRoot "windows.env.example") -Destination $envPath
        Write-Host "已创建本机配置：$envPath" -ForegroundColor Cyan
    }
    $values = Read-MarsDotEnv -Path $envPath

    $overlaySetting = Get-MarsSetting -Name "MARS_V31_OVERLAY_PATH" -Values $values
    if ($overlaySetting) {
        $overlayPath = if ([IO.Path]::IsPathRooted($overlaySetting)) {
            [IO.Path]::GetFullPath($overlaySetting)
        }
        else {
            [IO.Path]::GetFullPath((Join-Path $script:MarsRepoRoot $overlaySetting))
        }
    }
    else {
        $overlayPath = [IO.Path]::GetFullPath(
            (Join-Path (Split-Path -Parent $script:MarsRepoRoot) "mars_v31_wireless")
        )
    }

    $packManifest = Join-Path $overlayPath "project_packs\pimc\project_pack.yaml"
    $adapterModule = Join-Path $overlayPath "src\mars_v31_wireless\adapter.py"
    if (-not $SkipMountValidation -and (
        -not (Test-Path -LiteralPath $packManifest -PathType Leaf) -or
        -not (Test-Path -LiteralPath $adapterModule -PathType Leaf)
    )) {
        throw @"
找不到完整的 mars_v31_wireless Overlay。
请把 mars_v31_wireless 与 mars_v2 放在同一父目录，或在：
$envPath
设置 MARS_V31_OVERLAY_PATH 为它的绝对 Windows 路径。
"@
    }

    [Environment]::SetEnvironmentVariable(
        "MARS_V31_OVERLAY_PATH",
        (ConvertTo-MarsComposePath -Path $overlayPath),
        "Process"
    )

    if ($Production -and -not $SkipMountValidation) {
        $repoSetting = Get-MarsSetting -Name "PIMC_REPO_HOST_PATH" -Values $values
        $dataSetting = Get-MarsSetting -Name "PIMC_DATA_HOST_PATH" -Values $values
        if (-not $repoSetting -or -not $dataSetting) {
            throw "生产模式要求在 .env 中设置 PIMC_REPO_HOST_PATH 和 PIMC_DATA_HOST_PATH。"
        }
        if (-not [IO.Path]::IsPathRooted($repoSetting) -or
            -not [IO.Path]::IsPathRooted($dataSetting)) {
            throw "生产模式的 PIMC_REPO_HOST_PATH 和 PIMC_DATA_HOST_PATH 必须是绝对 Windows 路径。"
        }
        $repoPath = [IO.Path]::GetFullPath($repoSetting)
        $dataPath = [IO.Path]::GetFullPath($dataSetting)
        $requiredRepoFiles = @(
            (Join-Path $repoPath "tools\mars_adapter_entry.py"),
            (Join-Path $repoPath "mars_baseline_manifest.json")
        )
        foreach ($requiredPath in $requiredRepoFiles) {
            if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
                throw "PIMC 只读仓库缺少可信文件：$requiredPath"
            }
        }
        $dataManifest = Join-Path $dataPath "mars_data_manifest.json"
        if (-not (Test-Path -LiteralPath $dataManifest -PathType Leaf)) {
            throw "PIMC 只读数据目录缺少清单：$dataManifest"
        }
        [Environment]::SetEnvironmentVariable(
            "PIMC_REPO_HOST_PATH",
            (ConvertTo-MarsComposePath -Path $repoPath),
            "Process"
        )
        [Environment]::SetEnvironmentVariable(
            "PIMC_DATA_HOST_PATH",
            (ConvertTo-MarsComposePath -Path $dataPath),
            "Process"
        )
    }

    $frontendPort = ConvertTo-MarsPort -Name "MARS_FRONTEND_PORT" -Value (
        Get-MarsSetting -Name "MARS_FRONTEND_PORT" -Values $values -Default "3001"
    )
    $backendPort = ConvertTo-MarsPort -Name "MARS_BACKEND_PORT" -Value (
        Get-MarsSetting -Name "MARS_BACKEND_PORT" -Values $values -Default "8000"
    )
    if ($frontendPort -eq $backendPort) {
        throw "前端和后端不能使用同一个端口：$frontendPort"
    }
    $executionDevice = (
        Get-MarsSetting -Name "MARS_EXECUTION_DEVICE" -Values $values -Default "cpu"
    ).ToLowerInvariant()
    if ($executionDevice -notin @("cpu", "gpu")) {
        throw "MARS_EXECUTION_DEVICE 只能是 cpu 或 gpu，当前值：$executionDevice"
    }
    [Environment]::SetEnvironmentVariable(
        "MARS_EXECUTION_DEVICE",
        $executionDevice,
        "Process"
    )
    $runtimeMode = if ($Production) {
        "production"
    }
    else {
        Get-MarsSetting -Name "MARS_RUNTIME_MODE" -Values $values -Default "development"
    }
    $mockMode = if ($Production) {
        "never"
    }
    else {
        Get-MarsSetting -Name "MARS_MOCK_MODE" -Values $values -Default "auto"
    }
    if ($runtimeMode -notin @("development", "staging", "production")) {
        throw "MARS_RUNTIME_MODE 只能是 development、staging 或 production。"
    }
    if ($mockMode -notin @("auto", "always", "never")) {
        throw "MARS_MOCK_MODE 只能是 auto、always 或 never。"
    }
    if ($runtimeMode -eq "production" -and -not $Production -and -not $SkipMountValidation) {
        throw "生产模式请使用 Start-Mars.ps1 -Production，以确保真实仓库和数据只读挂载。"
    }

    $composeFiles = @((Join-Path $script:MarsDeployRoot "compose.yaml"))
    if ($Production) {
        $composeFiles += (Join-Path $script:MarsDeployRoot "compose.production.yaml")
    }
    $composeArguments = @(
        "--project-directory", $script:MarsDeployRoot,
        "--env-file", $envPath
    )
    foreach ($composeFile in $composeFiles) {
        $composeArguments += @("-f", $composeFile)
    }

    return [PSCustomObject]@{
        BackendPort = $backendPort
        ComposeArguments = $composeArguments
        DeployRoot = $script:MarsDeployRoot
        EnvPath = $envPath
        ExecutionDevice = $executionDevice
        FrontendPort = $frontendPort
        OverlayPath = $overlayPath
        Production = [bool]$Production
        RuntimeMode = $runtimeMode
        MockMode = $mockMode
        RepoRoot = $script:MarsRepoRoot
        Settings = $values
    }
}

function Assert-MarsDocker {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        throw "未找到 Docker。请先安装并启动 Docker Desktop。"
    }

    $server = & docker version --format '{{.Server.Os}}|{{.Server.Arch}}' 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $server) {
        throw "Docker 服务未启动。请打开 Docker Desktop，等待状态变为 Running 后重试。"
    }
    $serverParts = ([string]$server).Trim().Split("|")
    if ($serverParts[0] -ne "linux") {
        throw "Docker Desktop 当前不是 Linux Containers 模式。请切换到 Linux containers。"
    }
    if ($serverParts.Count -gt 1 -and $serverParts[1] -ne "amd64") {
        Write-Warning "当前 Docker 架构为 $($serverParts[1])；目标镜像是 linux/amd64，构建会使用仿真。"
    }
    $script:MarsDockerServerOs = $serverParts[0]
    $script:MarsDockerServerArch = if ($serverParts.Count -gt 1) {
        $serverParts[1]
    }
    else {
        ""
    }

    & docker compose version | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "未找到 Docker Compose v2。请升级 Docker Desktop。"
    }
}

function Assert-MarsImagePlatform {
    param(
        [Parameter(Mandatory = $true)][string]$Image,
        [string]$Expected = "linux/amd64"
    )

    $actual = & docker image inspect --format '{{.Os}}/{{.Architecture}}' $Image 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $actual) {
        throw "离线包缺少镜像：$Image"
    }
    $actual = ([string]$actual).Trim()
    if ($actual -ne $Expected) {
        throw "镜像 $Image 架构错误：期望 $Expected，实际 $actual。"
    }
}

function Invoke-MarsDocker {
    param(
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    & docker @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description 失败，Docker 返回代码 $LASTEXITCODE。"
    }
}

function Test-MarsComposeConfiguration {
    param([Parameter(Mandatory = $true)]$Context)

    $arguments = @("compose") + $Context.ComposeArguments + @("config", "--quiet")
    Invoke-MarsDocker -Arguments $arguments -Description "Docker Compose 配置检查"
}

function Resolve-MarsImageArchive {
    param([string]$Path = "")

    if (-not $Path) {
        $Path = Join-Path $script:MarsDeployRoot "images\mars-windows-amd64.tar"
    }
    $archive = [IO.Path]::GetFullPath($Path)
    if (-not (Test-Path -LiteralPath $archive -PathType Leaf)) {
        throw "离线镜像包不存在：$archive"
    }
    $hashPath = $archive + ".sha256"
    if (-not (Test-Path -LiteralPath $hashPath -PathType Leaf)) {
        throw "离线镜像包缺少 SHA256 文件：$hashPath"
    }
    $hashText = [IO.File]::ReadAllText($hashPath).Trim()
    if ($hashText -notmatch '\A([0-9a-fA-F]{64})(?:[ \t]+[^\r\n]+)?\z') {
        throw "离线镜像包 SHA256 文件格式错误：$hashPath"
    }
    $expectedHash = $Matches[1]
    $actualHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $archive).Hash
    if ($actualHash -ne $expectedHash) {
        throw "离线镜像包 SHA256 校验失败，请重新拷贝镜像包。"
    }
    return $archive
}

function Assert-MarsOfflinePorts {
    param([Parameter(Mandatory = $true)]$Context)

    if ($Context.FrontendPort -ne 3001 -or $Context.BackendPort -ne 8000) {
        throw "离线镜像固定使用前端 3001、后端 8000 端口；请恢复默认端口后重新导出或导入。"
    }
}

function Assert-MarsReadiness {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)]$Health,
        [Parameter(Mandatory = $true)]$Readiness
    )

    foreach ($name in @("status", "distribution")) {
        if ($name -notin $Health.PSObject.Properties.Name) {
            throw "后端健康响应缺少字段：$name"
        }
    }
    if ($Health.status -ne "ok" -or $Health.distribution -ne "v31-wireless") {
        throw "后端已响应，但 V3.1 分发状态不正确。"
    }
    foreach ($name in @("ready", "runtime_mode", "mock_mode", "execution_device", "execution_backend", "checks")) {
        if ($name -notin $Readiness.PSObject.Properties.Name) {
            throw "后端就绪响应缺少字段：$name"
        }
    }
    if ($Readiness.execution_device -ne $Context.ExecutionDevice -or
        $Readiness.runtime_mode -ne $Context.RuntimeMode -or
        $Readiness.mock_mode -ne $Context.MockMode) {
        throw "后端实际运行模式与本次启动配置不一致，请检查端口冲突或旧容器。"
    }
    if ($Context.Production -and $Readiness.execution_backend -eq "mock") {
        throw "生产模式不允许使用 mock 执行器。"
    }
    if ($Readiness.ready -isnot [bool] -or -not $Readiness.ready) {
        $messages = @($Readiness.checks | Where-Object {
            $_.severity -eq "blocker" -and -not $_.ready
        } | ForEach-Object { $_.message })
        $detail = if ($messages.Count) { $messages -join "; " } else { "缺少通过的就绪检查" }
        throw "服务已启动，但尚不能创建任务：$detail"
    }
}

function Wait-MarsHttpEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][datetime]$Deadline
    )

    while ((Get-Date) -lt $Deadline) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                return
            }
        }
        catch {
            Start-Sleep -Seconds 2
        }
    }
    throw "$Name 未在限定时间内就绪：$Uri"
}
