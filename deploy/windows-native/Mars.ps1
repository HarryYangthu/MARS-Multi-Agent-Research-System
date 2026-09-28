[CmdletBinding()]
param(
    [ValidateSet('Menu', 'Install', 'Start', 'Configure', 'Stop', 'Status', 'TestApi')]
    [string]$Action = 'Menu',
    [switch]$NoBrowser,
    [switch]$SkipConfigure
)
. (Join-Path $PSScriptRoot 'Common.ps1')
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$runtime = Join-Path $root 'local/windows'
$config = Read-MarsNativeConfig (Join-Path $root 'configs/windows_native.yaml')
$statePath = Join-Path $runtime 'services.json'
$python = Join-Path $runtime 'venv/Scripts/python.exe'
$uv = Join-Path $runtime ('tools/uv/' + $config.uv_executable)
$node = Join-Path $runtime ('tools/node/' + $config.node_executable)
$git = Join-Path $runtime ('tools/git/' + $config.git_executable)
$backendUrl = 'http://127.0.0.1:' + $config.backend_port
$frontendUrl = 'http://127.0.0.1:' + $config.frontend_port

function Read-ServiceState {
    if (Test-Path -LiteralPath $statePath) { return (Get-Content -LiteralPath $statePath -Raw | ConvertFrom-Json) }
    return $null
}

function Assert-ServicesStopped {
    $state = Read-ServiceState
    if ($null -ne $state) {
        foreach ($role in @('backend', 'frontend')) {
            if ($null -ne (Get-MarsOwnedProcess $state.$role)) {
                throw 'MARS is running. Use Stop before installing or changing API configuration.'
            }
        }
    }
}

function Set-NativeEnvironment {
    $env:PATH = (Split-Path $node) + ';' + (Split-Path $git) + ';' + (Split-Path $python) + ';' + $env:PATH
    $env:UV_CACHE_DIR = Join-Path $runtime 'cache/uv'
    $env:UV_PYTHON_INSTALL_DIR = Join-Path $runtime 'tools/python'
    $env:UV_PYTHON_BIN_DIR = Join-Path $runtime 'tools/python-bin'
    $env:UV_NO_MODIFY_PATH = '1'
    $env:PYTHONUTF8 = '1'
    $env:PYTHONIOENCODING = 'utf-8'
    $env:PYTHONUNBUFFERED = '1'
    $env:PYTHONPATH = (Join-Path $root 'backend') + ';' + (Join-Path $root 'posttrain/src')
    $env:MARS_MOCK_MODE = 'never'
    $env:MARS_RUNTIME_MODE = 'development'
    $env:MARS_DISTRIBUTION = 'v30-core'
    $env:MARS_EXECUTION_DEVICE = 'cpu'
    $env:MARS_PAPER_STATIC_PYTHON = $python
    $env:BACKEND_HOST = '127.0.0.1'
    $env:BACKEND_PORT = $config.backend_port
    $env:FRONTEND_PORT = $config.frontend_port
    $env:MARS_CORS_ORIGINS = "$frontendUrl,http://localhost:$($config.frontend_port)"
    $env:BACKEND_URL = $backendUrl
    $env:NEXT_PUBLIC_BACKEND_URL = $backendUrl
    $env:NEXT_PUBLIC_WS_URL = $backendUrl
    $env:NEXT_TELEMETRY_DISABLED = '1'
}

function Install-NativeDependencies {
    Assert-ServicesStopped
    # A failed repair must not leave a prior success receipt behind.
    Remove-Item -LiteralPath (Join-Path $runtime 'installed.json') -Force -ErrorAction SilentlyContinue
    Write-Host 'Installing MARS dependencies (first install needs Internet access)...'
    $script:uv = Install-MarsArchive 'uv' $config $runtime
    $script:node = Install-MarsArchive 'node' $config $runtime
    $script:git = Install-MarsArchive 'git' $config $runtime
    Set-NativeEnvironment
    if (-not (Test-Path "$env:WINDIR/System32/vcruntime140_1.dll")) {
        $vc = Join-Path $runtime 'downloads/vc_redist.x64.exe'
        Invoke-WebRequest -UseBasicParsing -Uri $config.vc_runtime_url -OutFile $vc -TimeoutSec 600
        $signature = Get-AuthenticodeSignature -FilePath $vc
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'O=Microsoft Corporation') {
            throw 'Microsoft C++ runtime signature verification failed.'
        }
        Write-Host 'Installing Microsoft C++ runtime. Windows may ask for administrator approval.'
        $vcProcess = Start-Process -FilePath $vc -ArgumentList '/install /passive /norestart' -Wait -PassThru
        if ($vcProcess.ExitCode -notin @(0, 1638, 3010)) { throw "C++ runtime installation failed: $($vcProcess.ExitCode)" }
        if ($vcProcess.ExitCode -eq 3010) { throw 'Restart Windows to finish C++ runtime installation, then run Install again.' }
    }
    Invoke-MarsNativeCommand $uv @('python', 'install', $config.python_version) $root
    Invoke-MarsNativeCommand $uv @('venv', '--python', $config.python_version, '--clear', (Join-Path $runtime 'venv')) $root
    $requirements = Join-Path $runtime 'requirements.lock.txt'
    Invoke-MarsNativeCommand $uv @('export', '--frozen', '--all-extras', '--no-hashes', '--no-emit-project', '--no-emit-package', 'torch', '--output-file', $requirements) $root
    Invoke-MarsNativeCommand $uv @('pip', 'install', '--python', $python, '--only-binary', ':all:', '-r', $requirements) $root
    $torchVersion = & $python -c "import tomllib; print(next(p['version'] for p in tomllib.load(open(__import__('sys').argv[1],'rb'))['package'] if p['name']=='torch'))" (Join-Path $root 'uv.lock')
    if ($LASTEXITCODE -ne 0 -or $torchVersion -notmatch '^\d+\.\d+\.\d+$') { throw 'Cannot resolve locked PyTorch version.' }
    Invoke-MarsNativeCommand $uv @('pip', 'install', '--python', $python, '--no-deps', '--index-url', $config.torch_index, "torch==$torchVersion") $root
    Invoke-MarsNativeCommand $uv @('pip', 'install', '--python', $python, '--no-deps', '-e', '.') $root
    Invoke-MarsNativeCommand $uv @('pip', 'check', '--python', $python) $root
    Invoke-MarsNativeCommand $python @('-c', 'import torch, scipy, matplotlib, tensorboard, chromadb, app.main; assert torch.ones(2).sum().item() == 2') $root
    $npm = Join-Path (Split-Path $node) 'node_modules/npm/bin/npm-cli.js'
    Invoke-MarsNativeCommand $node @($npm, 'ci', '--include=dev', '--no-audit', '--no-fund') (Join-Path $root 'frontend')
    Invoke-MarsNativeCommand $node @($npm, 'run', 'typecheck') (Join-Path $root 'frontend')
    $fingerprint = Get-MarsDependencyFingerprint $root
    $fingerprint['root'] = $root
    $fingerprint | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $runtime 'installed.json') -Encoding UTF8
    Write-Host 'Dependencies installed and import/type checks passed.' -ForegroundColor Green
}

function Test-InstallationCurrent {
    $stamp = Join-Path $runtime 'installed.json'
    if (-not (Test-Path -LiteralPath $stamp)) { return $false }
    foreach ($file in @($python, $node, $git, $uv, (Join-Path $root 'frontend/node_modules/next/dist/bin/next'))) {
        if (-not (Test-Path -LiteralPath $file)) { return $false }
    }
    try { $old = Get-Content -LiteralPath $stamp -Raw | ConvertFrom-Json }
    catch { return $false }
    if ($null -eq $old -or 'root' -notin $old.PSObject.Properties.Name -or $old.root -ne $root) { return $false }
    $current = Get-MarsDependencyFingerprint $root
    foreach ($key in $current.Keys) {
        if ($key -notin $old.PSObject.Properties.Name -or $old.$key -ne $current[$key]) { return $false }
    }
    return $true
}

function Configure-NativeApi {
    Assert-ServicesStopped
    if (-not (Test-InstallationCurrent)) { Install-NativeDependencies }
    Set-NativeEnvironment
    Invoke-MarsNativeCommand $python @((Join-Path $PSScriptRoot 'configure_api.py')) $root
    $secretFile = Join-Path $root '.env.local'
    if (Test-Path -LiteralPath $secretFile) {
        $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
        & icacls.exe $secretFile '/inheritance:r' '/grant:r' "*${sid}:(F)" '*S-1-5-18:(F)' | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not restrict .env.local permissions to this Windows user.' }
    }
}

function Start-NativeServices {
    $state = Read-ServiceState
    if ($null -ne $state -and $null -ne (Get-MarsOwnedProcess $state.backend) -and
        $null -ne (Get-MarsOwnedProcess $state.frontend)) {
        try {
            $health = Invoke-RestMethod "$($state.backend_url)/health" -TimeoutSec 3
            $page = Invoke-WebRequest -UseBasicParsing "$($state.frontend_url)/config/agents" -TimeoutSec 10
            $null = Invoke-RestMethod "$($state.frontend_url)/api/system/version" -TimeoutSec 3
            if ($health.status -ne 'ok' -or $health.service -ne 'mars-backend' -or $page.StatusCode -ne 200) {
                throw 'Unhealthy response.'
            }
        } catch { throw 'MARS processes exist but health checks failed. Check local/windows/logs, then Stop and Start.' }
        Write-Host "MARS is already running and healthy: $($state.frontend_url)"
        if (-not $NoBrowser) { Start-Process $state.frontend_url }
        return
    }
    Assert-ServicesStopped
    if (-not (Test-InstallationCurrent)) { Install-NativeDependencies }
    if (-not $SkipConfigure -and -not (Test-Path (Join-Path $runtime 'api-configured.json'))) { Configure-NativeApi }
    Set-NativeEnvironment
    foreach ($port in @([int]$config.backend_port, [int]$config.frontend_port)) {
        if (Test-MarsPort $port) { throw "Port $port is occupied. Stop its owner or edit configs/windows_native.yaml." }
    }
    $logs = Join-Path $runtime 'logs'
    New-Item -ItemType Directory -Force -Path $logs | Out-Null
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $shutdown = Join-Path $runtime ('shutdown-' + [guid]::NewGuid().ToString('N'))
    $backend = $null
    $frontend = $null
    try {
        $arguments = ConvertTo-MarsArgumentString @((Join-Path $PSScriptRoot 'serve_backend.py'), '--port', $config.backend_port, '--shutdown-file', $shutdown)
        $backend = Start-Process -FilePath $python -ArgumentList $arguments -WorkingDirectory $root -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logs "backend-$stamp.out.log") -RedirectStandardError (Join-Path $logs "backend-$stamp.err.log")
        $deadline = (Get-Date).AddSeconds([int]$config.startup_timeout_seconds)
        do {
            if ($backend.HasExited) { throw "Backend exited. See $logs" }
            try {
                $health = Invoke-RestMethod "$backendUrl/health" -TimeoutSec 2
                if ($health.status -eq 'ok' -and $health.service -eq 'mars-backend') { break }
            } catch { }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if ((Get-Date) -ge $deadline) { throw "Backend health timeout. See $logs" }
        $arguments = ConvertTo-MarsArgumentString @((Join-Path $root 'frontend/node_modules/next/dist/bin/next'), 'dev', '--hostname', '127.0.0.1', '--port', $config.frontend_port)
        $frontend = Start-Process -FilePath $node -ArgumentList $arguments -WorkingDirectory (Join-Path $root 'frontend') -WindowStyle Hidden -PassThru `
            -RedirectStandardOutput (Join-Path $logs "frontend-$stamp.out.log") -RedirectStandardError (Join-Path $logs "frontend-$stamp.err.log")
        $deadline = (Get-Date).AddSeconds([int]$config.startup_timeout_seconds)
        do {
            if ($frontend.HasExited -or $backend.HasExited) { throw "A service exited. See $logs" }
            try {
                $response = Invoke-WebRequest -UseBasicParsing "$frontendUrl/config/agents" -TimeoutSec 5
                $null = Invoke-RestMethod "$frontendUrl/api/system/version" -TimeoutSec 5
                if ($response.StatusCode -eq 200) { break }
            } catch { }
            Start-Sleep -Milliseconds 500
        } while ((Get-Date) -lt $deadline)
        if ((Get-Date) -ge $deadline) { throw "Frontend/proxy health timeout. See $logs" }
        @{ backend = (Get-MarsProcessReceipt $backend); frontend = (Get-MarsProcessReceipt $frontend);
           shutdown_file = $shutdown; backend_url = $backendUrl; frontend_url = $frontendUrl } |
            ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $statePath -Encoding UTF8
    } catch {
        if ($null -ne $backend -and -not $backend.HasExited) {
            [IO.File]::WriteAllText($shutdown, 'stop')
            if (-not $backend.WaitForExit(25000)) { & taskkill.exe /PID $backend.Id /T /F | Out-Null }
        }
        if ($null -ne $frontend -and -not $frontend.HasExited) { & taskkill.exe /PID $frontend.Id /T /F | Out-Null }
        throw
    }
    Write-Host "MARS ready: $frontendUrl" -ForegroundColor Green
    Write-Host "API settings: $frontendUrl/config/agents"
    Write-Host "Backend API docs: $backendUrl/docs"
    Write-Host "Logs: $logs"
    if (-not $NoBrowser) { Start-Process $frontendUrl }
}

function Stop-NativeServices {
    $state = Read-ServiceState
    if ($null -eq $state) { Write-Host 'No MARS service record.'; return }
    $backend = Get-MarsOwnedProcess $state.backend
    if ($null -ne $backend) {
        [IO.File]::WriteAllText($state.shutdown_file, 'stop')
        if (-not $backend.WaitForExit(30000)) {
            throw 'Backend is still shutting down. Its tasks were not force-killed; check the logs and retry Stop.'
        }
    }
    $frontend = Get-MarsOwnedProcess $state.frontend
    if ($null -ne $frontend) {
        & taskkill.exe /PID $frontend.Id /T /F | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'Could not stop the owned frontend process.' }
    }
    Remove-Item -LiteralPath $statePath -Force
    Remove-Item -LiteralPath $state.shutdown_file -Force -ErrorAction SilentlyContinue
    Write-Host 'MARS stopped. Configuration, research runs and data are preserved.'
}

function Show-NativeStatus {
    $state = Read-ServiceState
    if ($null -eq $state) { Write-Host 'MARS has not been started by this launcher.'; return }
    foreach ($role in @('backend', 'frontend')) {
        $owned = Get-MarsOwnedProcess $state.$role
        Write-Host "$role owned process running: $($null -ne $owned)"
    }
    try { Invoke-RestMethod "$($state.backend_url)/health" -TimeoutSec 3 | Format-List }
    catch { Write-Warning 'Backend health request failed.' }
}

$mutex = $null
$locked = $false
try {
    if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT -or -not [Environment]::Is64BitProcess -or
        $env:PROCESSOR_ARCHITECTURE -ne 'AMD64') { throw 'This launcher requires Windows 10/11 x64 and 64-bit PowerShell.' }
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $ProgressPreference = 'SilentlyContinue'
    $sha = [Security.Cryptography.SHA256]::Create()
    try { $mutexName = 'Local\MARS-native-' + ([BitConverter]::ToString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes($root.ToLowerInvariant())))).Replace('-', '') }
    finally { $sha.Dispose() }
    $mutex = New-Object Threading.Mutex($false, $mutexName)
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { throw 'Another MARS launcher is already installing or starting this folder.' }
    New-Item -ItemType Directory -Force -Path $runtime | Out-Null
    if ($Action -eq 'Menu') {
        Write-Host 'MARS Windows: 1 Start (auto install) | 2 Install/repair | 3 Configure API | 4 Stop | 5 Status | 6 Test API | 0 Exit'
        $choice = Read-Host 'Choose [1]'
        if ($choice -eq '') { $choice = '1' }
        $actions = @{ '1' = 'Start'; '2' = 'Install'; '3' = 'Configure'; '4' = 'Stop'; '5' = 'Status'; '6' = 'TestApi' }
        if ($choice -eq '0') { exit 0 }
        if (-not $actions.ContainsKey($choice)) { throw 'Unknown menu choice.' }
        $Action = $actions[$choice]
    }
    switch ($Action) {
        'Install' { Install-NativeDependencies }
        'Configure' { Configure-NativeApi }
        'Start' { Start-NativeServices }
        'Stop' { Stop-NativeServices }
        'Status' { Show-NativeStatus }
        'TestApi' {
            if (-not (Test-InstallationCurrent)) { throw 'Install dependencies before testing the API.' }
            Set-NativeEnvironment
            Invoke-MarsNativeCommand $python @((Join-Path $PSScriptRoot 'test_api.py')) $root
        }
    }
} catch {
    Write-Host ('MARS error: ' + $_.Exception.Message) -ForegroundColor Red
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
}
