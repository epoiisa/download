# Install or update download for the current user; no administrator is needed.
# Manual equivalent: copy download.cmd, download.py and catalogue.json together
# to %LOCALAPPDATA%\Programs\download, then add that directory to your user PATH.
# Python 3.8+ must already be available through py -3.

# A script block keeps preferences and working variables out of the caller's scope.
& {
    $ErrorActionPreference = 'Stop'
    $ProgressPreference = 'SilentlyContinue'
    if ($env:OS -ne 'Windows_NT') {
        throw 'Use install.sh on macOS or Linux.'
    }
    if (-not (Get-Command py -ErrorAction SilentlyContinue)) {
        throw 'Python 3.8+ with py -3 is required: https://docs.python.org/3/using/windows.html'
    }
    & py -3 -c 'import sys; sys.exit(sys.version_info < (3, 8))'
    if ($LASTEXITCODE -ne 0) {
        throw 'Python 3.8+ with py -3 is required: https://docs.python.org/3/using/windows.html'
    }

    $downloadDir = Join-Path $env:LOCALAPPDATA 'Programs\download'
    [IO.Directory]::CreateDirectory($downloadDir) | Out-Null
    $downloadStage = Join-Path $downloadDir ('.download-install-' + [Guid]::NewGuid().ToString('N'))
    [IO.Directory]::CreateDirectory($downloadStage) | Out-Null
    $downloadChanged = @()
    $downloadComplete = $false
    $downloadKeepRecovery = $false
    try {
        # One archive supplies matching code and catalogue, even if main changes.
        Write-Host 'Downloading download...'
        $downloadArchive = Join-Path $downloadStage 'source.zip'
        Invoke-WebRequest -UseBasicParsing -TimeoutSec 120 `
            -Uri 'https://github.com/epoiisa/download/archive/refs/heads/main.zip' `
            -OutFile $downloadArchive
        # .NET treats brackets in the user's directory name literally.
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($downloadArchive, $downloadStage)
        $downloadSource = Join-Path $downloadStage 'download-main'
        $downloadNames = @('download.cmd', 'download.py', 'catalogue.json')
        foreach ($downloadName in $downloadNames) {
            $downloadFile = Join-Path $downloadSource $downloadName
            if (-not (Test-Path -LiteralPath $downloadFile -PathType Leaf)) {
                throw "Archive is missing a runtime file: $downloadName"
            }
            $downloadTarget = Join-Path $downloadDir $downloadName
            if (Test-Path -LiteralPath $downloadTarget) {
                $downloadItem = Get-Item -LiteralPath $downloadTarget -Force
                if ($downloadItem.PSIsContainer -or
                    ($downloadItem.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                    throw "Installation target is not a regular file: $downloadTarget"
                }
            }
        }
        # Empty input validates startup and the catalogue without downloading icons.
        '' | & py -3 -B (Join-Path $downloadSource 'download.py')
        if ($LASTEXITCODE -ne 0) {
            throw 'The downloaded runtime failed validation; installation was not changed.'
        }

        # Replace existing files atomically, retaining backups until all succeed.
        foreach ($downloadName in $downloadNames) {
            $downloadFile = Join-Path $downloadSource $downloadName
            $downloadTarget = Join-Path $downloadDir $downloadName
            $downloadBackup = Join-Path $downloadStage ($downloadName + '.backup')
            if ([IO.File]::Exists($downloadTarget)) {
                [IO.File]::Replace($downloadFile, $downloadTarget, $downloadBackup)
            } else {
                [IO.File]::Move($downloadFile, $downloadTarget)
            }
            $downloadChanged += $downloadName
        }
        $downloadComplete = $true

        # Preserve user PATH entries and recognise equivalent existing entries.
        $downloadUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
        $downloadHasPath = $false
        foreach ($downloadEntry in ($downloadUserPath -split ';')) {
            $downloadExpanded = [Environment]::ExpandEnvironmentVariables($downloadEntry.Trim().Trim('"'))
            if ($downloadExpanded.Replace('/', '\').TrimEnd('\') -ieq $downloadDir.Replace('/', '\').TrimEnd('\')) {
                $downloadHasPath = $true
            }
        }
        if (-not $downloadHasPath) {
            $downloadNewPath = $downloadDir
            if (-not [string]::IsNullOrEmpty($downloadUserPath)) {
                $downloadNewPath = "$downloadUserPath;$downloadDir"
            }
            [Environment]::SetEnvironmentVariable('Path', $downloadNewPath, 'User')
            Write-Host 'Updated user PATH.'
        }
        Write-Host "Installed download in $downloadDir"
        Write-Host 'Reopen your terminal application, then run: download --help'
    } catch {
        $downloadFailure = $_
        if (-not $downloadComplete) {
            foreach ($downloadName in $downloadChanged) {
                try {
                    $downloadTarget = Join-Path $downloadDir $downloadName
                    $downloadBackup = Join-Path $downloadStage ($downloadName + '.backup')
                    if ([IO.File]::Exists($downloadBackup)) {
                        $downloadDiscard = Join-Path $downloadStage ($downloadName + '.discard')
                        [IO.File]::Replace($downloadBackup, $downloadTarget, $downloadDiscard)
                    } else {
                        [IO.File]::Delete($downloadTarget)
                    }
                } catch {
                    $downloadKeepRecovery = $true
                }
            }
        }
        if ($downloadKeepRecovery) {
            throw "Installation failed and recovery was incomplete. Recovery files: $downloadStage. $downloadFailure"
        }
        throw $downloadFailure
    } finally {
        if (-not $downloadKeepRecovery) {
            Remove-Item -LiteralPath $downloadStage -Recurse -Force
        }
    }
}
