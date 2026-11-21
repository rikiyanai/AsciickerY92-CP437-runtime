param(
    [switch]$NoLaunch,
    [string]$SourceDirectory = '',
    [string]$SourceRef = 'main',
    [string]$Distro = 'Ubuntu-24.04',
    [string]$UserName = '',
    [switch]$BootstrapWsl
)

$ErrorActionPreference = 'Stop'
$repository = 'https://raw.githubusercontent.com/rikiyanai/AsciickerY92-CP437-runtime'
$installRoot = Join-Path $env:LOCALAPPDATA 'AsciickerY92'
$cachedInstaller = Join-Path $installRoot 'install-windows.ps1'
$runOnceKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce'
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'

function Get-InstalledDistros {
    try { $lines = @(& $wsl --list --quiet 2>$null) }
    catch { return @() }
    if ($LASTEXITCODE -ne 0) { return @() }
    return @($lines | ForEach-Object { ([string]$_).Replace([string][char]0, '').Trim() } | Where-Object { $_ })
}

if ($Distro -notmatch '^[A-Za-z0-9._-]+$' -or $SourceRef -notmatch '^[A-Za-z0-9._/-]+$') {
    throw 'Invalid distribution or source reference.'
}
if ($SourceDirectory -match '["%\r\n]' -or ($SourceDirectory -and !$SourceDirectory.StartsWith('/'))) {
    throw 'SourceDirectory must be an absolute Linux path without quotes, percent signs or newlines.'
}
if ($env:PROCESSOR_ARCHITECTURE -ne 'AMD64' -and $env:PROCESSOR_ARCHITEW6432 -ne 'AMD64') {
    throw 'The Windows player installer currently supports x64 PCs.'
}
New-Item -ItemType Directory -Path $installRoot -Force | Out-Null
if ($BootstrapWsl) {
    & $wsl --install --distribution $Distro --no-launch
    exit $LASTEXITCODE
}

# Keep a resumable copy, including when invoked with irm ... | iex.
if ($PSCommandPath) {
    if (![string]::Equals($PSCommandPath, $cachedInstaller, [StringComparison]::OrdinalIgnoreCase)) {
        Copy-Item -LiteralPath $PSCommandPath -Destination $cachedInstaller -Force
    }
} else {
    Invoke-WebRequest -UseBasicParsing -Uri "$repository/$SourceRef/scripts/install-windows.ps1" -OutFile $cachedInstaller
}

if (!(Get-InstalledDistros | Where-Object { $_ -eq $Distro })) {
    Write-Host 'Installing Ubuntu and WSL. Windows may ask for administrator approval.'
    New-Item -Path $runOnceKey -Force | Out-Null
    $resume = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$cachedInstaller`""
    New-ItemProperty -Path $runOnceKey -Name 'AsciickerSetup' -Value $resume -PropertyType String -Force | Out-Null
    try {
        $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$cachedInstaller`" -BootstrapWsl -Distro `"$Distro`""
        $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Verb RunAs -WindowStyle Hidden -Wait -PassThru
        if ($process.ExitCode -notin @(0, 3010)) { throw "WSL installation failed with code $($process.ExitCode)." }
        if (!(Get-InstalledDistros | Where-Object { $_ -eq $Distro })) {
            Write-Host 'Restart Windows. Asciicker installation will resume automatically at your next sign-in.'
            return
        }
    } catch {
        Remove-ItemProperty -Path $runOnceKey -Name 'AsciickerSetup' -ErrorAction SilentlyContinue
        throw
    }
}
Remove-ItemProperty -Path $runOnceKey -Name 'AsciickerSetup' -ErrorAction SilentlyContinue

$kernel = ((& $wsl -d $Distro -e uname -r) | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Ubuntu could not start. Restart Windows and run the installer again.' }
if ($kernel -notmatch '(?i)wsl2') {
    & $wsl --set-version $Distro 2
    if ($LASTEXITCODE -ne 0) { throw 'The player requires WSL 2.' }
}
if (!$UserName) {
    $UserName = ((& $wsl -d $Distro -e id -un) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) { throw 'Could not determine the Ubuntu user.' }
    if ($UserName -eq 'root') { $UserName = 'asciicker' }
}
if ($UserName -notmatch '^[a-z_][a-z0-9_-]*$') { throw 'Invalid Ubuntu username.' }

$linuxBootstrapFile = Join-Path $installRoot 'install-player.sh'
if ($SourceDirectory -and $PSScriptRoot -and (Test-Path -LiteralPath (Join-Path $PSScriptRoot 'install-player.sh'))) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'install-player.sh') -Destination $linuxBootstrapFile -Force
} else {
    Invoke-WebRequest -UseBasicParsing -Uri "$repository/$SourceRef/scripts/install-player.sh" -OutFile $linuxBootstrapFile
}
$linuxBootstrap = ((& $wsl -d $Distro -u root -e wslpath -a -u $linuxBootstrapFile) | Out-String).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Could not prepare the Ubuntu installer.' }
$wslArguments = @('-d', $Distro, '-u', 'root', '-e', 'bash', $linuxBootstrap, '--user', $UserName, '--ref', $SourceRef)
if ($SourceDirectory) { $wslArguments += @('--source', $SourceDirectory) }
& $wsl @wslArguments
if ($LASTEXITCODE -ne 0) { throw "Asciicker installation failed with code $LASTEXITCODE. Run the same command to retry." }

$linuxHome = ((& $wsl -d $Distro -u $UserName -e printenv HOME) | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $linuxHome -notmatch '^/[A-Za-z0-9_./-]+$') { throw 'Could not locate a supported player home directory.' }
if (!$SourceDirectory) { $SourceDirectory = "$linuxHome/.local/share/asciicker/source" }
$commandPath = Join-Path $installRoot 'asciicker.cmd'
$command = @"
@echo off
setlocal DisableDelayedExpansion
"%SystemRoot%\System32\wsl.exe" -d $Distro -u $UserName --cd ~ -e $linuxHome/.local/bin/asciicker %*
exit /b %ERRORLEVEL%
"@
[IO.File]::WriteAllText($commandPath, ($command -replace "`r?`n", "`r`n") + "`r`n", [Text.Encoding]::ASCII)
$userPath = [string][Environment]::GetEnvironmentVariable('Path', 'User')
if (@($userPath -split ';') -notcontains $installRoot) {
    [Environment]::SetEnvironmentVariable('Path', ($userPath.TrimEnd(';') + ';' + $installRoot).TrimStart(';'), 'User')
}
if (@($env:Path -split ';') -notcontains $installRoot) { $env:Path += ';' + $installRoot }
Write-Host 'Installed. Use asciicker to play, or asciicker launcher for the menu.'
if (!$NoLaunch) {
    & $commandPath
    if ($LASTEXITCODE -ne 0) { throw "Classic exited with code $LASTEXITCODE." }
}
