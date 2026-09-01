Set-StrictMode -Version 3.0

$script:MarsNativeRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$script:MarsRepoRoot = [IO.Path]::GetFullPath((Join-Path $script:MarsNativeRoot "..\.."))
$script:MarsRuntimeRoot = Join-Path $script:MarsNativeRoot "runtime"
$script:MarsStatePath = Join-Path $script:MarsRuntimeRoot "processes.json"

function Read-MarsNativeEnv {
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

function Get-MarsNativeSetting {
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

function ConvertTo-MarsNativePort {
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

function Initialize-MarsNativeContext {
    $envPath = Join-Path $script:MarsNativeRoot ".env"
    if (-not (Test-Path -LiteralPath $envPath -PathType Leaf)) {
        Copy-Item -LiteralPath (Join-Path $script:MarsNativeRoot "windows.env.example") -Destination $envPath
        Write-Host "已创建本机配置：$envPath" -ForegroundColor Cyan
    }
    $values = Read-MarsNativeEnv -Path $envPath
    $overlaySetting = Get-MarsNativeSetting -Name "MARS_V31_OVERLAY_PATH" -Values $values
    if ($overlaySetting) {
        $overlayPath = [IO.Path]::GetFullPath($overlaySetting)
    }
    else {
        $overlayPath = [IO.Path]::GetFullPath(
            (Join-Path (Split-Path -Parent $script:MarsRepoRoot) "mars_v31_wireless")
        )
    }
    $packManifest = Join-Path $overlayPath "project_packs\pimc\project_pack.yaml"
    $adapterModule = Join-Path $overlayPath "src\mars_v31_wireless\adapter.py"
    if (-not (Test-Path -LiteralPath $packManifest -PathType Leaf) -or
        -not (Test-Path -LiteralPath $adapterModule -PathType Leaf)) {
        throw @"
找不到完整的 mars_v31_wireless Overlay。
请把 mars_v31_wireless 与 mars_v2 放在同一父目录，或在：
$envPath
设置 MARS_V31_OVERLAY_PATH 为绝对 Windows 路径。
"@
    }
    $frontendPort = ConvertTo-MarsNativePort -Name "MARS_FRONTEND_PORT" -Value (
        Get-MarsNativeSetting -Name "MARS_FRONTEND_PORT" -Values $values -Default "3001"
    )
    $backendPort = ConvertTo-MarsNativePort -Name "MARS_BACKEND_PORT" -Value (
        Get-MarsNativeSetting -Name "MARS_BACKEND_PORT" -Values $values -Default "8000"
    )
    if ($frontendPort -eq $backendPort) {
        throw "前端和后端不能使用相同端口：$frontendPort"
    }
    New-Item -ItemType Directory -Force -Path $script:MarsRuntimeRoot | Out-Null
    return [PSCustomObject]@{
        BackendPort = $backendPort
        EnvPath = $envPath
        FrontendPort = $frontendPort
        OverlayPath = $overlayPath
        RepoRoot = $script:MarsRepoRoot
        RuntimeRoot = $script:MarsRuntimeRoot
        Settings = $values
        StatePath = $script:MarsStatePath
    }
}

function Resolve-MarsBasePython {
    $py = Get-Command "py.exe" -ErrorAction SilentlyContinue
    if ($null -ne $py) {
        & $py.Source -3.11 -c "import sys; assert sys.version_info[:2] == (3, 11)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{ FilePath = $py.Source; Prefix = @("-3.11") }
        }
    }
    $python = Get-Command "python.exe" -ErrorAction SilentlyContinue
    if ($null -ne $python) {
        & $python.Source -c "import sys; assert sys.version_info[:2] == (3, 11)" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return [PSCustomObject]@{ FilePath = $python.Source; Prefix = @() }
        }
    }
    throw "未找到 Python 3.11 x64。请通过公司软件中心安装后重新启动。"
}

function Resolve-MarsNode {
    $node = Get-Command "node.exe" -ErrorAction SilentlyContinue
    $npm = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
    if ($null -eq $node -or $null -eq $npm) {
        throw "未找到 Node.js 20 x64。请通过公司软件中心安装后重新启动。"
    }
    $majorText = (& $node.Source -p "process.versions.node.split('.')[0]").Trim()
    $major = 0
    if (-not [int]::TryParse($majorText, [ref]$major) -or $major -lt 20) {
        throw "MARS 要求 Node.js 20 或更高版本，当前主版本：$majorText"
    }
    return [PSCustomObject]@{ Node = $node.Source; Npm = $npm.Source }
}

function Invoke-MarsNativeChecked {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$Description,
        [string]$WorkingDirectory = $script:MarsRepoRoot
    )

    Push-Location $WorkingDirectory
    try {
        & $FilePath @Arguments
        if ($LASTEXITCODE -ne 0) {
            throw "$Description 失败，退出码：$LASTEXITCODE"
        }
    }
    finally {
        Pop-Location
    }
}

function Find-MarsStandaloneServer {
    param([Parameter(Mandatory = $true)][string]$Root)

    if (-not (Test-Path -LiteralPath $Root -PathType Container)) {
        return $null
    }
    $candidates = Get-ChildItem -LiteralPath $Root -Filter "server.js" -File -Recurse |
        Where-Object {
            Test-Path -LiteralPath (Join-Path $_.DirectoryName ".next") -PathType Container
        } |
        Sort-Object { $_.FullName.Length }
    return ($candidates | Select-Object -First 1)
}

function Install-MarsNativeDependencies {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [switch]$Offline,
        [switch]$ForceRebuild
    )

    $venvRoot = Join-Path $Context.RepoRoot ".venv-windows"
    $python = Join-Path $venvRoot "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $python -PathType Leaf)) {
        $base = Resolve-MarsBasePython
        Invoke-MarsNativeChecked -FilePath $base.FilePath -Arguments (
            @($base.Prefix) + @("-m", "venv", $venvRoot)
        ) -Description "创建 Python 虚拟环境"
    }

    & $python -c "import fastapi, uvicorn, socketio, pydantic, chromadb" 2>$null
    $backendReady = $LASTEXITCODE -eq 0
    if (-not $backendReady -or $ForceRebuild) {
        $pipArguments = @("-m", "pip", "install", "--disable-pip-version-check")
        $wheelRoot = Join-Path $script:MarsNativeRoot "offline\wheels"
        if ($Offline) {
            if (-not (Test-Path -LiteralPath $wheelRoot -PathType Container)) {
                throw "没有离线 Python wheel 包：$wheelRoot"
            }
            $pipArguments += @("--no-index", "--find-links", $wheelRoot)
        }
        else {
            $indexUrl = Get-MarsNativeSetting -Name "MARS_PYTHON_INDEX_URL" -Values $Context.Settings
            if ($indexUrl) {
                $pipArguments += @("--index-url", $indexUrl)
            }
        }
        if ($Offline) {
            $pipArguments += @("mars==0.1.0")
        }
        else {
            $pipArguments += @("-e", $Context.RepoRoot)
        }
        Invoke-MarsNativeChecked -FilePath $python -Arguments $pipArguments -Description "安装后端依赖"
    }

    $offlineFrontend = Join-Path $script:MarsNativeRoot "offline\frontend"
    if ($Offline) {
        $serverFile = Find-MarsStandaloneServer -Root $offlineFrontend
        if ($null -eq $serverFile) {
            throw "没有离线前端产物：$offlineFrontend"
        }
        return [PSCustomObject]@{
            FrontendServer = $serverFile.FullName
            Node = (Resolve-MarsNode).Node
            Python = $python
        }
    }

    $nodeTools = Resolve-MarsNode
    $frontendRoot = Join-Path $Context.RepoRoot "frontend"
    $nodeModules = Join-Path $frontendRoot "node_modules"
    if (-not (Test-Path -LiteralPath $nodeModules -PathType Container) -or $ForceRebuild) {
        $registry = Get-MarsNativeSetting -Name "MARS_NPM_REGISTRY" -Values $Context.Settings
        $npmArguments = @("ci", "--legacy-peer-deps")
        if ($registry) {
            $npmArguments += @("--registry", $registry)
        }
        Invoke-MarsNativeChecked -FilePath $nodeTools.Npm -Arguments $npmArguments `
            -Description "安装前端依赖" -WorkingDirectory $frontendRoot
    }

    Set-MarsNativeEnvironment -Context $Context -Python $python
    $standaloneRoot = Join-Path $frontendRoot ".next\standalone"
    $serverFile = Find-MarsStandaloneServer -Root $standaloneRoot
    if ($null -eq $serverFile -or $ForceRebuild) {
        Invoke-MarsNativeChecked -FilePath $nodeTools.Npm -Arguments @("run", "build") `
            -Description "构建前端" -WorkingDirectory $frontendRoot
        $serverFile = Find-MarsStandaloneServer -Root $standaloneRoot
    }
    if ($null -eq $serverFile) {
        throw "前端构建没有生成 standalone server.js"
    }
    $serverRoot = Split-Path -Parent $serverFile.FullName
    $staticSource = Join-Path $frontendRoot ".next\static"
    $staticTarget = Join-Path $serverRoot ".next\static"
    if (Test-Path -LiteralPath $staticTarget) {
        Remove-Item -LiteralPath $staticTarget -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $staticTarget) | Out-Null
    Copy-Item -LiteralPath $staticSource -Destination $staticTarget -Recurse
    $publicSource = Join-Path $frontendRoot "public"
    if (Test-Path -LiteralPath $publicSource -PathType Container) {
        $publicTarget = Join-Path $serverRoot "public"
        if (Test-Path -LiteralPath $publicTarget) {
            Remove-Item -LiteralPath $publicTarget -Recurse -Force
        }
        Copy-Item -LiteralPath $publicSource -Destination $publicTarget -Recurse
    }
    return [PSCustomObject]@{
        FrontendServer = $serverFile.FullName
        Node = $nodeTools.Node
        Python = $python
    }
}

function Set-MarsNativeEnvironment {
    param(
        [Parameter(Mandatory = $true)]$Context,
        [Parameter(Mandatory = $true)][string]$Python
    )

    foreach ($name in $Context.Settings.Keys) {
        $value = [string]$Context.Settings[$name]
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            [Environment]::SetEnvironmentVariable($name, $value, "Process")
        }
    }
    $backendUrl = "http://127.0.0.1:$($Context.BackendPort)"
    $pythonPath = @(
        (Join-Path $Context.RepoRoot "backend"),
        (Join-Path $Context.OverlayPath "src"),
        (Join-Path $Context.RepoRoot "projects\synthetic_regression\src")
    ) -join [IO.Path]::PathSeparator
    $fixed = @{
        BACKEND_HOST = "127.0.0.1"
        BACKEND_PORT = [string]$Context.BackendPort
        FRONTEND_PORT = [string]$Context.FrontendPort
        MARS_CORS_ORIGINS = "http://127.0.0.1:$($Context.FrontendPort),http://localhost:$($Context.FrontendPort)"
        MARS_DISTRIBUTION = "v31-wireless"
        MARS_PROJECT_PACK_PATHS = (Join-Path $Context.OverlayPath "project_packs")
        MARS_PAPER_STATIC_PYTHON = $Python
        NEXT_PUBLIC_BACKEND_URL = $backendUrl
        NEXT_PUBLIC_WS_URL = $backendUrl.Replace("http://", "ws://")
        BACKEND_URL = $backendUrl
        PYTHONPATH = $pythonPath
        REDIS_URL = ""
        NEXT_TELEMETRY_DISABLED = "1"
    }
    foreach ($name in $fixed.Keys) {
        [Environment]::SetEnvironmentVariable($name, [string]$fixed[$name], "Process")
    }
}

function ConvertTo-MarsQuotedArgument {
    param([Parameter(Mandatory = $true)][string]$Value)
    return '"' + $Value.Replace('"', '\"') + '"'
}

function Start-MarsNativeProcess {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$StdoutPath,
        [Parameter(Mandatory = $true)][string]$StderrPath
    )

    $argumentLine = (($Arguments | ForEach-Object { ConvertTo-MarsQuotedArgument $_ }) -join " ")
    $process = Start-Process -FilePath $FilePath -ArgumentList $argumentLine `
        -WorkingDirectory $WorkingDirectory -RedirectStandardOutput $StdoutPath `
        -RedirectStandardError $StderrPath -WindowStyle Hidden -PassThru
    return $process
}

function Wait-MarsNativeEndpoint {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Uri,
        [Parameter(Mandatory = $true)][datetime]$Deadline,
        [Diagnostics.Process]$Process = $null
    )

    $lastError = "尚未响应"
    while ((Get-Date) -lt $Deadline) {
        if ($null -ne $Process -and $Process.HasExited) {
            throw "$Name 进程提前退出，退出码：$($Process.ExitCode)"
        }
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Uri -TimeoutSec 5
            if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 400) {
                Write-Host "$Name 已就绪：$Uri" -ForegroundColor Green
                return
            }
            $lastError = "HTTP $($response.StatusCode)"
        }
        catch {
            $lastError = $_.Exception.Message
        }
        Start-Sleep -Seconds 2
    }
    throw "$Name 启动超时：$lastError"
}

function Read-MarsNativeState {
    if (-not (Test-Path -LiteralPath $script:MarsStatePath -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $script:MarsStatePath -Raw | ConvertFrom-Json
    }
    catch {
        throw "进程状态文件损坏：$script:MarsStatePath"
    }
}

function Test-MarsNativeProcess {
    param([int]$ProcessId)
    if ($ProcessId -le 0) { return $false }
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Stop-MarsNativeProcess {
    param([int]$ProcessId)
    if (-not (Test-MarsNativeProcess -ProcessId $ProcessId)) { return }
    & taskkill.exe /PID $ProcessId /T /F | Out-Null
}
