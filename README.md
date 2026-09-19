# Download icons for Albion Online

Small local Python tool that downloads Albion Online item and spell PNGs into the current working directory. Requires Python 3.8+ and no third-party packages.

## Install or update

Keep `download` and `download.py` together. Run these commands from the project directory to install or update `download` in a directory on your `PATH`:

```bash
mkdir -p ~/.local/bin
cp download download.py ~/.local/bin/
chmod +x ~/.local/bin/download
```

The Python implementation can also be run directly from this directory:

```bash
./download.py Hunter Shoes 8 1 4
```

## Usage

All images are written to the current working directory.

```bash
download
download Heroic Cleave
download Hunter Shoes 8 1 4
download items.txt
download --help
```

Running `download` with no arguments starts interactive mode with no visible prompt or startup message. Type one request per line and press Enter to download it:

```text
Hunter Shoes 8 1 4
Bow of Badon 8
Heroic Cleave
exit
```

Each request prints its result before accepting the next line. Failed requests leave the session running. Type `exit` or `quit`, or press Ctrl+D or Ctrl+C, to leave.

Names containing spaces do not need quotes in any input method. Item requests use `Name <tier> [enchant] [quality]`; the first integer starts the numeric values. For example, `download Hunter Shoes 8 1 4` saves `Hunter Shoes 8.1 Excellent.png`.

Apostrophes and hyphens can be typed literally in interactive mode and text files. At the normal shell, shell-special characters still need escaping, for example `download Vendetta\'s Wrath 8`. Previously quoted names remain supported.

Spell names are matched case-insensitively against the embedded English catalogue, then downloaded by ID. For example, `download Heroic Cleave` uses `CLEAVE` and saves `Heroic Cleave.png`. Known spell IDs are also accepted; exact uppercase IDs take precedence over names. Unknown names fail; ambiguous names list the IDs to choose from.

The spell catalogue covers 469 English names for player weapon and armour abilities and passives, including gathering armour, shapeshifter forms, and recasts. Its 631 IDs include variants shared across items and tiers, from the [8 September 2026 game-data dump](https://github.com/ao-data/ao-bin-dumps/tree/0be6a5e74f30fc1312118be3d017f3832f027cef). Cape, mount, consumable, tool, vanity, and mob spells are excluded.

Item values are: tier `1`–`8`, enchant `0`–`4` (default `0`), and quality `1`–`5` (default `1`):

```text
1 Common  2 Good  3 Outstanding  4 Excellent  5 Masterpiece
```

Run `download items.txt` to process a text file containing one request per line, without the initial `download` or quotes:

```text
Refreshing Sprint
Hunter Shoes 8 1 4
Vendetta's Wrath 8
```

Blank lines and lines beginning with `#` are ignored in interactive mode and batch files. Successful batch lines are removed from the file; failed lines remain for retry. The command exits with status `1` if any request fails, or `130` when interactive mode is interrupted with Ctrl+C.
