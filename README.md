# Download Albion Online Item Icons

A small Python script to download Albion Online item icons by human‐readable names.

It includes an embedded mapping from names → item identifiers, so no external data files are needed.

## Quick start
1. Put your requests in `downloads.txt (CSV: Name, tier[, enchant[, quality]])`.
2. Run: `python3 download.py [downloads.txt] [Downloads]`.
3. Find images in the output folder (default `downloads/`).

## Requirements
- Python 3.8+
- requests (`pip install requests`)

## Usage

```bash
python3 download.py [downloads.txt] [downloads]
```

- `downloads.txt` (optional): path to your CSV input file. Default: `downloads.txt`.
- `downloads` (optional): output directory. Default: `downloads`.

The script prints `[OK]/[WARN]/[FAIL]` messages as it processes each line.
Failed lines are left in the input file so you can fix and rerun.

## Input format

CSV-style per line:
```CSV
Name, tier[, enchant[, quality]]
```

- `Name`: Human-readable item name (must exist in the embedded table).
- `tier`: Integer 1–8. Optional when the identifier already has a `TX_` prefix (see “Tiered identifiers” below).
- `enchant`: Integer 0–4, default 0 (unenchanted).
- `quality`: Integer 1–5, default 1 (Common).

Comments (`# ...`) and blank lines are ignored.

### Examples

```
Guardian Helmet, 6
Cleric Robe, 6, 1, 4
Transport Mammoth
```

## Tiered identifiers (important)

Some embedded identifiers already include a tier prefix like `T7_` or `T8_`.

For these items:
- You don’t need to specify a tier in the CSV.
- The downloaded filename omits the tier after the name.

Examples of filename results:
- `Guardian Helmet, 6` → **Guardian Helmet 6.png**
- `Cleric Robe, 6, 1, 4` → **Cleric Robe 6.1 Excellent.png**
- `Transport Mammoth` (ident is `T8_MOUNT_MAMMOTH_TRANSPORT`) → **Transport Mammoth.png**

If you do supply a tier that conflicts with an embedded tier, the embedded tier wins and a warning is printed.

## Output details
- Base filename: `<Name>`
- If the item does not have an embedded `TX_` prefix, the tier is appended: e.g., `Guardian Helmet 6.png`.
- If `enchant` > 0, `.<enchant>` is appended: e.g., `Guardian Helmet 6.1.png`.
- If `quality` > 1, a word is appended: e.g. `Guardian Helmet 6.1 Excellent.png`.
- Extension: `.png`
- Saved in the specified output directory (default `downloads/`).

## Notes and validation
- Enchant must be 0–4; quality must be 1–5. Invalid lines are skipped with a warning.
- Unknown names (not in the embedded table) are reported and left in the input file for correction.
- Network errors (e.g., CDN hiccups) will fail that line; you can rerun after fixing connectivity.

## Example session

### downloads.txt

```
Guardian Helmet, 6
Cleric Robe, 6, 1, 4
Transport Mammoth
```

## Run

```bash
python3 download.py
```

### Resulting files

```
downloads/
  Guardian Helmet 6.png
  Cleric Robe 6.1 Excellent.png
  Transport Mammoth.png
```