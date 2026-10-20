Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Read-MarsNativeConfig {
    param([string]$Path)
    $result = @{}
    foreach ($line in [IO.File]::ReadAllLines($Path)) {
        if ($line -match '^\s*(#.*)?$') { continue }
        if ($line -notmatch '^([a-z][a-z0-9_]*):\s+([^\s].*?)\s*$') {
            throw "Expected a flat YAML scalar in $Path"
        }
        if ($result.ContainsKey($Matches[1])) { throw 'Duplicate native configuration key.' }
        $result[$Matches[1]] = $Matches[2]
    }
    foreach ($name in @('backend_port', 'frontend_port')) {
        $value = 0
        if (-not [int]::TryParse($result[$name], [ref]$value) -or $value -lt 1024 -or $value -gt 65535) {
            throw "Invalid $name (expected 1024..65535)."
        }
    }
    if ($result.backend_port -eq $result.frontend_port) { throw 'Frontend/backend ports must differ.' }
    return $result
}

function Invoke-MarsNativeCommand {
    param([string]$Executable, [string[]]$Arguments, [string]$Directory)
    Push-Location -LiteralPath $Directory
    try {
        & $Executable @Arguments
        if ($LASTEXITCODE -ne 0) { throw "Command failed (exit $LASTEXITCODE): $Executable" }
    } finally { Pop-Location }
}

function ConvertTo-MarsArgumentString {
    param([string[]]$Arguments)
    # Windows CommandLineToArgvW quoting, including spaces and trailing slashes.
    return (($Arguments | ForEach-Object {
        $quoted = [regex]::Replace($_, '(\\*)"', '$1$1\"')
        $quoted = [regex]::Replace($quoted, '(\\+)$', '$1$1')
        '"' + $quoted + '"'
    }) -join ' ')
}

function Install-MarsArchive {
    param([string]$Name, [hashtable]$Config, [string]$Runtime)
    $hash = $Config["${Name}_sha256"]
    $url = $Config["${Name}_url"]
    if ($hash -notmatch '^[a-f0-9]{64}$' -or -not $url.StartsWith('https://')) {
        throw "Invalid download manifest for $Name"
    }
    $target = Join-Path $Runtime "tools/$Name"
    $executable = Join-Path $target $Config["${Name}_executable"]
    $receipt = Join-Path $target 'archive.sha256'
    if ((Test-Path -LiteralPath $executable) -and (Test-Path -LiteralPath $receipt) -and
        ([IO.File]::ReadAllText($receipt).Trim() -eq $hash)) { return $executable }
    $downloads = Join-Path $Runtime 'downloads'
    New-Item -ItemType Directory -Force -Path $downloads | Out-Null
    $archive = Join-Path $downloads "$Name-$hash.zip"
    if (-not (Test-Path -LiteralPath $archive)) {
        Write-Host "Downloading $Name from official distribution..."
        $part = "$archive.part"
        Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $part -TimeoutSec 600
        if ((Get-FileHash -LiteralPath $part -Algorithm SHA256).Hash -ne $hash) {
            Remove-Item -LiteralPath $part -Force
            throw "$Name download checksum mismatch."
        }
        Move-Item -LiteralPath $part -Destination $archive -Force
    }
    if ((Get-FileHash -LiteralPath $archive -Algorithm SHA256).Hash -ne $hash) {
        throw "Cached archive checksum mismatch: $archive"
    }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    Expand-Archive -LiteralPath $archive -DestinationPath $target -Force
    if (-not (Test-Path -LiteralPath $executable)) { throw "Missing executable after extracting $Name" }
    [IO.File]::WriteAllText($receipt, $hash)
    return $executable
}

function Test-MarsPort {
    param([int]$Port)
    $client = New-Object Net.Sockets.TcpClient
    try {
        $task = $client.ConnectAsync('127.0.0.1', $Port)
        return ($task.Wait(500) -and $client.Connected)
    } catch { return $false } finally { $client.Dispose() }
}

function Get-MarsProcessReceipt {
    param([Diagnostics.Process]$Process)
    $Process.Refresh()
    return @{ pid = $Process.Id; started = $Process.StartTime.ToUniversalTime().Ticks.ToString(); executable = $Process.Path }
}

function Get-MarsOwnedProcess {
    param($Receipt)
    if ($null -eq $Receipt) { return $null }
    $process = Get-Process -Id ([int]$Receipt.pid) -ErrorAction SilentlyContinue
    if ($null -eq $process) { return $null }
    try {
        if ($process.StartTime.ToUniversalTime().Ticks.ToString() -ne $Receipt.started -or
            $process.Path -ne $Receipt.executable) { return $null }
    } catch { return $null }
    return $process
}

function Get-MarsDependencyFingerprint {
    param([string]$Root)
    $result = @{}
    foreach ($file in @('uv.lock', 'frontend/package-lock.json')) {
        $result[$file] = (Get-FileHash -LiteralPath (Join-Path $Root $file) -Algorithm SHA256).Hash.ToLowerInvariant()
    }
    $native = Read-MarsNativeConfig (Join-Path $Root 'configs/windows_native.yaml')
    foreach ($key in @('python_version', 'uv_sha256', 'node_sha256', 'git_sha256')) {
        $result["windows.$key"] = ([string]$native[$key]).ToLowerInvariant()
    }
    return $result
}

function Get-MarsWindowsLayout {
    param([string]$Root)
    $modern = Join-Path $Root 'dependencies/windows'
    $legacy = Join-Path $Root 'local/windows'
    $runtime = $modern
    if (-not (Test-Path -LiteralPath $modern) -and
        ((Test-Path -LiteralPath (Join-Path $legacy 'installed.json')) -or
         (Test-Path -LiteralPath (Join-Path $legacy 'tools')))) { $runtime = $legacy }
    $offline = Join-Path $Root 'dependencies/offline'
    $marker = Join-Path $Root 'WINDOWS-OFFLINE.json'
    if (Test-Path -LiteralPath $marker) {
        $receipt = Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json
        if ('dependency_directory' -in $receipt.PSObject.Properties.Name) {
            if ($receipt.dependency_directory -notin @('dependencies/offline', 'offline')) {
                throw 'Unsupported offline dependency directory.'
            }
            $offline = Join-Path $Root $receipt.dependency_directory
        } elseif (-not (Test-Path -LiteralPath $offline)) { $offline = Join-Path $Root 'offline' }
    } elseif (-not (Test-Path -LiteralPath $offline)) { $offline = Join-Path $Root 'offline' }
    return @{ Runtime = $runtime; State = $legacy; Offline = $offline;
        Frontend = (Join-Path $Root 'dependencies/frontend') }
}

function Get-MarsFrontendFingerprint {
    param([string]$Root)
    $result = @{}
    $frontend = Join-Path $Root 'frontend'
    foreach ($directory in @('src', 'public', 'scripts')) {
        $path = Join-Path $frontend $directory
        if (-not (Test-Path -LiteralPath $path)) { continue }
        foreach ($file in Get-ChildItem -LiteralPath $path -Recurse -File) {
            if (($file.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
                throw 'Frontend source fingerprint cannot follow a symbolic file.'
            }
            $relative = $file.FullName.Substring($frontend.Length + 1).Replace('\', '/')
            $result[$relative] = (Get-FileHash -LiteralPath $file.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    foreach ($name in @('package.json', 'package-lock.json', 'next.config.js', 'next.config.mjs', 'next.config.ts',
        'tsconfig.json', 'tailwind.config.js', 'tailwind.config.ts', 'postcss.config.js', 'postcss.config.mjs', '.eslintrc.json')) {
        $path = Join-Path $frontend $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $result[$name] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    foreach ($name in @('configs/frontend.yaml', 'configs/local_runtime.yaml')) {
        $path = Join-Path $Root $name
        if (Test-Path -LiteralPath $path -PathType Leaf) {
            $result[$name] = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    }
    $native = Read-MarsNativeConfig (Join-Path $Root 'configs/windows_native.yaml')
    $result['windows.backend_port'] = $native.backend_port
    $result['windows.frontend_port'] = $native.frontend_port
    return $result
}

function Test-MarsFingerprint {
    param($Saved, [hashtable]$Current)
    if ($null -eq $Saved -or @($Saved.PSObject.Properties.Name).Count -ne $Current.Count) { return $false }
    foreach ($key in $Current.Keys) {
        if ($key -notin $Saved.PSObject.Properties.Name -or $Saved.$key -cne $Current[$key]) { return $false }
    }
    return $true
}

function Test-MarsInstalledFingerprint {
    param($Saved, [string]$Root)
    if ($null -eq $Saved) { return $false }
    $current = Get-MarsDependencyFingerprint $Root
    $names = @($Saved.PSObject.Properties.Name)
    if ('windows.python_version' -notin $names) {
        # Legacy receipts bind the entire native manifest. Accept unchanged
        # locked dependencies only when that stronger original binding agrees.
        foreach ($key in @('uv.lock', 'frontend/package-lock.json')) {
            if ($key -notin $names -or $Saved.$key -ne $current[$key]) { return $false }
        }
        $nativeHash = (Get-FileHash -LiteralPath (Join-Path $Root 'configs/windows_native.yaml') -Algorithm SHA256).Hash
        return ('configs/windows_native.yaml' -in $names -and $Saved.'configs/windows_native.yaml' -eq $nativeHash)
    }
    foreach ($key in $current.Keys) {
        if ($key -notin $names -or $Saved.$key -ne $current[$key]) { return $false }
    }
    return $true
}

function Connect-MarsFrontendDependencies {
    param([string]$Root)
    $layout = Get-MarsWindowsLayout $Root
    $target = Join-Path $layout.Frontend 'node_modules'
    $link = Join-Path $Root 'frontend/node_modules'
    New-Item -ItemType Directory -Force -Path $layout.Frontend | Out-Null
    if (Test-Path -LiteralPath $link) {
        $item = Get-Item -LiteralPath $link -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            $resolved = [IO.Path]::GetFullPath([string]@($item.Target)[0]).TrimEnd('\')
            if ($resolved -ne [IO.Path]::GetFullPath($target).TrimEnd('\')) {
                throw 'Frontend node_modules points to another dependency folder.'
            }
            return
        }
        if (Test-Path -LiteralPath $target) {
            throw 'Two frontend dependency folders exist; neither was overwritten.'
        }
        Move-Item -LiteralPath $link -Destination $target
    }
    New-Item -ItemType Directory -Force -Path $target | Out-Null
    New-Item -ItemType Junction -Path $link -Target $target | Out-Null
}
