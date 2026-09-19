# Download icons for Albion Online

Small local Python tool that downloads Albion Online item and spell PNGs into the current working directory. Requires Python 3.8+ and no third-party packages.

## Install

Keep `download` and `download.py` together. To make `download` available on your `PATH`, copy both files to a directory on your `PATH`:

```bash
mkdir -p ~/.local/bin
cp download download.py ~/.local/bin/
chmod +x ~/.local/bin/download
```

The Python implementation can also be run directly from this directory:

```bash
./download.py "Guardian Armor" 6 1 4
```

## Usage

All images are written to the current working directory.

```bash
download "Spell Name"
download "Item Name" <tier> [enchant] [quality]
download requests.txt
```

Spell names are matched case-insensitively against the embedded English catalogue, then downloaded by ID. For example, `download "Heroic Cleave"` uses `CLEAVE` and saves `Heroic Cleave.png`. Known spell IDs are also accepted; exact uppercase IDs take precedence over names. Unknown names fail; ambiguous names list the IDs to choose from.

The spell catalogue covers 469 English names for player weapon and armour abilities and passives, including gathering armour, shapeshifter forms, and recasts. Its 631 IDs include variants shared across items and tiers, from the [8 September 2026 game-data dump](https://github.com/ao-data/ao-bin-dumps/tree/0be6a5e74f30fc1312118be3d017f3832f027cef). Cape, mount, consumable, tool, vanity, and mob spells are excluded.

Item values are: tier `1`–`8`, enchant `0`–`4` (default `0`), and quality `1`–`5` (default `1`):

```text
1 Common  2 Good  3 Outstanding  4 Excellent  5 Masterpiece
```

Batch files use the same argument format as the command line, without the initial `download`. Quote names containing spaces:

```text
"Refreshing Sprint"
"Guardian Armor" 6 1 4
```

Blank lines and lines beginning with `#` are ignored. Successful lines are removed from the file; failed lines remain for retry. The command exits with status `1` if any request fails.
