This code is written and maintained using AI (Codex).

Data source: [ao-data/ao-bin-dumps](https://github.com/ao-data/ao-bin-dumps).

# Download icons for Albion Online

## Install or update

Requires [Python 3.8+](https://www.python.org/downloads/) (`python3` on macOS/Linux, `py -3` on Windows). No third-party packages or administrator access are needed. Run the command for your platform from any directory; run it again to update.

### macOS and Linux

```bash
curl -fsSL https://raw.githubusercontent.com/epoiisa/download/main/install.sh | sh
```

### Windows PowerShell

```powershell
irm https://raw.githubusercontent.com/epoiisa/download/main/install.ps1 | iex
```

On all platforms, reopen your terminal application, then check:

```text
download --help
```

## Usage

Run the command from the directory where you want the PNGs saved:

```text
download Hunter Shoes 8 1 4
download Heroic Cleave
download Hush
download Rending Rage
download "Lumberjack's Journal" 8
download Siphoned Energy
download --help
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

Spell requests have no numeric values. In a terminal, shared names and spells with multiple casts show a numbered menu:

```text
download Rending Rage
Choose an icon for 'Rending Rage':
  1. Rending Rage
  2. Rending Rage (second cast)
  3. Rending Rage (Raging Leap)
Select 1-3 (Enter to cancel): 2
```

This saves `Rending Rage (second cast).png`. `Hush` offers its active and passive icons. Enter a choice number, or press Enter to cancel the request. Full labels such as `download "Hush (passive)"` and catalogue spell IDs select icons directly; exact uppercase IDs take precedence over names.

Text files and redirected input/output use exact names without a menu: `Hush` selects the active icon and `Rending Rage` selects the base icon. Use full labels or spell IDs to select other icons.

Names ignore case and repeated whitespace. Spaces do not need quotes. At the shell, quote names containing special characters such as apostrophes or parentheses, as shown above. In interactive mode and text files, enter those names literally.

### Interactive mode

Run `download` without arguments, then type one request per line. There is no startup message or prompt:

```text
Hunter Shoes 8 1 4
Hush
2
Lumberjack's Journal 8
Siphoned Energy
exit
```

Each request prints its result. Failed requests leave the session running. Type `exit` or `quit`, or press Ctrl+C, to leave. End-of-input is Ctrl+D on macOS/Linux, or Ctrl+Z then Enter on Windows.

### Text files

Create a plain text `.txt` file with one request per line, without the initial `download`:

```text
Refreshing Sprint
Hunter Shoes 8 1 4
Vendetta's Wrath 8
```

Then run:

```text
download items.txt
```

Quote a batch-file path if it contains spaces, such as `download "my items.txt"`.

Successful lines are removed from the file; failed lines remain for retry, in their original order. Blank lines and lines beginning with `#` are ignored in files and interactive mode. The command exits with status `0` on success, `1` if any request fails or is cancelled, or `130` if a command or interactive session is interrupted with Ctrl+C.
