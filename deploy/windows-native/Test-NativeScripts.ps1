param([string]$Root = ([IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))))
. (Join-Path $PSScriptRoot 'Common.ps1')
function Assert-True { param([bool]$Value, [string]$Label) if (-not $Value) { throw $Label } }
foreach ($file in Get-ChildItem -LiteralPath $PSScriptRoot -Filter '*.ps1') {
    $tokens = $null
    $errors = $null
    $null = [Management.Automation.Language.Parser]::ParseFile($file.FullName, [ref]$tokens, [ref]$errors)
    Assert-True ($errors.Count -eq 0) "Syntax error in $($file.Name): $errors"
}
$config = Read-MarsNativeConfig (Join-Path $Root 'configs/windows_native.yaml')
Assert-True ($config.python_version -eq '3.11.14') 'Python runtime pin missing.'
$argsText = ConvertTo-MarsArgumentString @('D:\research folder\MARS', 'a"b', 'D:\tail\')
Assert-True ($argsText -eq '"D:\research folder\MARS" "a\"b" "D:\tail\\"') 'Windows argument quoting failed.'
$own = [Diagnostics.Process]::GetCurrentProcess()
$receipt = Get-MarsProcessReceipt $own
Assert-True ($null -ne (Get-MarsOwnedProcess $receipt)) 'Current process identity was not recognized.'
$receipt.started = '0'
Assert-True ($null -eq (Get-MarsOwnedProcess $receipt)) 'Changed process identity was accepted.'
$temp = Join-Path ([IO.Path]::GetTempPath()) ('mars-native-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $temp | Out-Null
try {
    $bad = Join-Path $temp 'bad.yaml'
    [IO.File]::WriteAllText($bad, "backend_port: 8010`nfrontend_port: 8010`n")
    $rejected = $false
    try { Read-MarsNativeConfig $bad | Out-Null } catch { $rejected = $true }
    Assert-True $rejected 'Port collision was accepted.'
    $downloads = Join-Path $temp 'downloads'
    New-Item -ItemType Directory -Path $downloads | Out-Null
    $wrongHash = '0' * 64
    [IO.File]::WriteAllText((Join-Path $downloads "uv-$wrongHash.zip"), 'tampered archive fixture')
    $rejected = $false
    try { Install-MarsArchive 'uv' @{uv_url='https://example.invalid/never-requested';uv_sha256=$wrongHash;uv_executable='uv.exe'} $temp | Out-Null }
    catch { $rejected = $_.Exception.Message -match 'checksum mismatch' }
    Assert-True $rejected 'A corrupt cached download was accepted.'
} finally { Remove-Item -LiteralPath $temp -Recurse -Force }
Write-Host 'Native launcher syntax, configuration, quoting, process identity and checksum contracts passed.'
