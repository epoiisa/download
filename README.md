This code is written and maintained using AI (Codex).

Data source: [ao-data/ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps).

# Download icons for Albion Online

## Install or update

Requires Python 3.8+ and no third-party packages.

### macOS and Linux

From this repository's directory, run:

```bash
mkdir -p ~/.local/bin
cp download download.py catalogue.json ~/.local/bin/
chmod +x ~/.local/bin/download
export PATH="$HOME/.local/bin:$PATH"
```

Keep all three files together and copy all three when updating. To keep the command available in new terminals, add the `export PATH` line to your shell's startup file once (`~/.zshrc` for zsh).

To run without installing, use `python3 ./download.py` in place of `download` from this directory.

### Windows PowerShell

Check that Python 3.8 or newer is available:

```powershell
py -3 --version
```

If this command is unavailable or reports an older version, install Python using the [official Windows installation instructions](https://docs.python.org/3/using/windows.html), then reopen PowerShell and check again. The Windows launcher uses `py -3`, supported by both the Python install manager and the older Python launcher.

From this repository's directory, copy the three Windows runtime files:

```powershell
$downloadDir = Join-Path $env:LOCALAPPDATA 'Programs\download'
New-Item -ItemType Directory -Path $downloadDir -Force | Out-Null
Copy-Item -LiteralPath .\download.cmd, .\download.py, .\catalogue.json -Destination $downloadDir -Force
```

Keep these three files together and repeat the copy when updating. Once, add the directory to your [user PATH](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_environment_variables#use-the-systemenvironment-methods), preserving its existing entries:

```powershell
$downloadUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($downloadUserPath -split ';') -notcontains $downloadDir) {
    if ([string]::IsNullOrEmpty($downloadUserPath)) {
        $downloadUserPath = $downloadDir
    } else {
        $downloadUserPath = "$downloadUserPath;$downloadDir"
    }
    [Environment]::SetEnvironmentVariable('Path', $downloadUserPath, 'User')
}
```

Close and reopen your terminal application, then check:

```powershell
Get-Command download
download --help
```

`Get-Command` should show `download.cmd` in the installation directory. Administrator access and PowerShell execution-policy changes are unnecessary.

To run without installing, use `py -3 .\download.py` or `.\download.cmd` in place of `download` from this directory.

## Usage

Run the command from the directory where you want the PNGs saved:

```text
download Hunter Shoes 8 1 4
download Heroic Cleave
download "Hush (Passive)"
download "Rending Rage (Second cast)"
download "Lumberjack's Journal" 8
download Siphoned Energy
download --help
```

These requests work in macOS/Linux shells and Windows PowerShell. To choose another output directory, create it and change into it first, for example:

```bash
# macOS/Linux
mkdir -p "$HOME/Albion Icons"
cd "$HOME/Albion Icons"
download Siphoned Energy
```

```powershell
# Windows PowerShell
$iconDir = Join-Path $env:USERPROFILE 'Albion Icons'
New-Item -ItemType Directory -Path $iconDir -Force | Out-Null
Set-Location -LiteralPath $iconDir
download Siphoned Energy
```

Downloading the same request again replaces its existing PNG.

Item requests use `Name <tier> [enchant] [quality]`. The first integer starts the numeric values. `Hunter Shoes 8 1 4` saves `Hunter Shoes 8.1 Excellent.png`.

- Tier: `1`–`8`; omit it only when the item has one available tier.
- Enchantment: `0`–`4`; defaults to the lowest available level, usually `0`. Items that start at `.1` default to `1`.
- Quality: `1`–`5`; defaults to `1`.

```text
1 Common  2 Good  3 Outstanding  4 Excellent  5 Masterpiece
```

Only each item's supported values are accepted. Look up names and options in [catalogue.json](catalogue.json). The five gathering journal names select empty journal icons.

Spell requests have no numeric values. Use the catalogue's full label for a distinct icon, such as `Hush (Passive)` or `Rending Rage (Second cast)`. Catalogue spell IDs are also accepted; exact uppercase IDs take precedence over names.

Names ignore case and repeated whitespace. Spaces do not need quotes. At the shell, quote names containing special characters such as apostrophes or parentheses, as shown above. In interactive mode and text files, enter those names literally.

### Interactive mode

Run `download` without arguments, then type one request per line. There is no startup message or prompt:

```text
Hunter Shoes 8 1 4
Hush (Passive)
Lumberjack's Journal 8
Siphoned Energy
exit
```

Each request prints its result. Failed requests leave the session running. Type `exit` or `quit`, or press Ctrl+C, to leave. End-of-input is Ctrl+D on macOS/Linux, or Ctrl+Z then Enter on Windows.

### Text files

Create a UTF-8 text file with one request per line, without the initial `download`. UTF-8 with or without a byte-order mark (BOM), and Windows CRLF or Unix LF line endings, are accepted:

```text
Refreshing Sprint
Hunter Shoes 8 1 4
Vendetta's Wrath 8
```

Then run:

```text
download items.txt
```

In PowerShell, create a UTF-8 file explicitly:

```powershell
@'
Refreshing Sprint
Hunter Shoes 8 1 4
Vendetta's Wrath 8
'@ | Set-Content -LiteralPath .\items.txt -Encoding utf8
download .\items.txt
$LASTEXITCODE
```

Use `-Encoding utf8` when creating batch files; Windows PowerShell 5.1's `>` redirection writes UTF-16 instead. See [PowerShell character encoding](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_character_encoding). Quote a batch-file path if it contains spaces, such as `download "my items.txt"`.

Successful lines are removed from the file; failed lines remain for retry, in their original order. The rewritten file uses UTF-8 without a BOM and LF line endings. Blank lines and lines beginning with `#` are ignored in files and interactive mode. The command exits with status `0` on success, `1` if any request fails, or `130` if interactive mode is interrupted with Ctrl+C. Check `$LASTEXITCODE` immediately after the command in PowerShell, or `echo $?` in macOS/Linux shells.
