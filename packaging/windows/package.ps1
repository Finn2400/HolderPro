param(
    [Parameter(Mandatory = $true)][string]$AppDirectory,
    [Parameter(Mandatory = $true)][string]$OutputDirectory,
    [Parameter(Mandatory = $true)][string]$Version
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "../..")).Path
$app = (Resolve-Path $AppDirectory).Path
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$output = (Resolve-Path $OutputDirectory).Path

if (-not (Test-Path (Join-Path $app "HolderPro.exe"))) {
    throw "PyInstaller application is missing HolderPro.exe: $app"
}
if (-not (Get-Command iscc.exe -ErrorAction SilentlyContinue)) {
    throw "Inno Setup 6 is required (iscc.exe was not found)"
}

$portable = Join-Path $output "HolderPro-$Version-windows-x86_64-portable.zip"
Compress-Archive -Path (Join-Path $app "*") -DestinationPath $portable -CompressionLevel Optimal -Force

& iscc.exe "/DSourceDir=$app" "/DOutputDir=$output" "/DAppVersion=$Version" `
    (Join-Path $root "packaging/windows/HolderPro.iss")
if ($LASTEXITCODE -ne 0) {
    throw "Inno Setup failed with exit code $LASTEXITCODE"
}
