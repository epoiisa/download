# download

A Python standard-library command-line tool for downloading Albion Online item and spell icons. Support Python 3.8+. Keep changes focused and preserve existing command-line, interactive and batch behaviour.

## Project files

- `download` is the macOS/Linux entry point; `download.cmd` is the native Windows launcher using `py -3`. Keep both thin; `download.py` contains the shared implementation and remains directly runnable with Python.
- `catalogue.json` is the only runtime source of item names, aliases, spell IDs and supported item variations. Load it relative to `download.py`, independent of the working directory. Install the platform's launcher, `download.py` and `catalogue.json` together.
- `lists/` contains the curated source lists. All catalogue maintenance belongs in this repository.
- `CATALOGUE.md` documents catalogue scope, format and source provenance.
- `scripts/update_spells.py` updates only curated spell mappings from a specified local game-data dump.
- `test_download.py` tests the downloader, catalogue and spell updater.
- `install.sh` and `install.ps1` install or update the platform's three runtime files from one `main` archive, validate them before replacement, and configure the user's PATH. Keep the manual-install summary in each script and the README installation section brief.
- `test_installers.py` checks installation, updates, PATH handling and failure recovery using temporary directories and local archives. Never use the user's real installation, shell profiles or registry for these tests.

## Catalogue authority

- The text lists are authoritative for inclusion and exclusion. Preserve manual edits, review annotations, grouping and ordering. Never regenerate the lists from JSON or expand their scope from external game data.
- Item lists contain names only, separated into groups by blank lines. Comma-separated names belong to one family; alternate names go in `aliases`. Potion tiers select Minor, standard and Major variants.
- `lists/miscellaneous.txt` includes the five gathering journals and Siphoned Energy. Journal entries use empty journal IDs at tiers 2–8, with enchantment 0 and quality 1. Siphoned Energy uses `UNIQUE_GVGTOKEN_GENERIC`, tier 1, enchantment 0 and quality 1.
- Spell and passive entries are limited to weapons and head, chest and feet armour, including gathering armour and shapeshifter abilities. Exclude abilities exclusive to off-hands, mounts, consumables, bags, capes, backpacks, tracking kits and gathering tools.
- Grouped casts such as `Rending Rage (second cast, Raging Leap)` include the base icon and each labelled cast. A separate ability such as `Hush (passive)` retains its own entry.
- Use proper stage names from the game's English tooltips and official item qualifiers inside parentheses, preserving their capitalisation. Keep descriptive labels lowercase when there is no distinct name, such as `second cast`, `dash` and `passive`.
- Keep tree order clockwise. Group weapon spells as Q, W, passives, then item-specific E spells; group armour spells as common spells, passives, then item-specific spells.
- During list review, JSON may temporarily differ. Update JSON after the user authorizes applying the reviewed scope. For an authorized catalogue change, update the relevant lists and JSON together.
- Preserve the compact family-level JSON format. Use game data and the Render Service to verify names, identifiers and supported variations within the curated scope. Retain aliases and reviewed spell-ID choices.
- Do not restore embedded tables, automatically merge older catalogues, or reinstate the legacy `ALBION_ITEM_CATALOG_PATH` override.

## Runtime behaviour

- Validate item-specific tiers, enchantments and quality before downloading. Honour `min_enchant`, `enchant_by_tier` and `id_by_enchant`.
- Add a tier prefix except for `UNIQUE_` and `QUESTITEM_` identifiers. Append nonzero enchantments after selecting the correct stem.
- Omitted enchantment selects the lowest allowed level; explicit unsupported values fail. Omitted tier is allowed only for an item with one available tier.
- Preserve case-insensitive names, whitespace normalization, aliases, curated spell labels and access to listed spell IDs. Exact uppercase spell IDs take precedence over coincident English names.
- Save images in the current working directory. Preserve input-based filenames, atomic image writes, and atomic batch-file rewrites that leave failed requests in their original order.
- Accept UTF-8 batch files with or without a BOM, and UTF-16 LE or BE batch files with a BOM; support LF and CRLF endings. Leave files with invalid encoding or an unsupported UTF-32 BOM unchanged. Rewrite failed lines as UTF-8 without a BOM and LF endings. Keep temporary files closed before replacement, preserve Unix permission bits on Unix, and leave Windows read-only destinations intact on replacement failure.
- Keep interactive startup silent. Request failures must not end the interactive session.
- When input and output are terminals, offer a numbered selection for bare spell names with multiple curated icons. Explicit labels and IDs select directly; batch files and redirected input/output retain exact-name lookup. Preserve the entered spelling and append the selected variant label to its filename. Cancellation fails only that request; command and interactive Ctrl+C return status 130 without a traceback.
- Preserve launcher arguments, standard input/output, exit status and the caller's working directory, including installation paths with spaces. Document Ctrl+D for macOS/Linux and Ctrl+Z then Enter for Windows; `exit`, `quit` and Ctrl+C remain available on both.
- Missing or invalid catalogue data must produce a clear failure, without a fallback catalogue or traceback. Help must work without loading the catalogue.

## Verification and maintenance

Run the tests from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v
```

On native Windows PowerShell, use:

```powershell
py -3 -B -m unittest -v
```

Use `sys.executable` for direct Python subprocesses, portable temporary paths and platform-appropriate permission assertions. Retain Unix launcher checks. Windows launcher, PowerShell PATH discovery, console input and interrupt checks require actual Windows; report the OS, PowerShell and Python versions tested and any unavailable checks.

PowerShell installer tests run when `pwsh` or `powershell` is available; `DOWNLOAD_TEST_POWERSHELL` can specify a portable executable. These tests use the real PowerShell and filesystem code with the network, `py` launcher and persistent user PATH isolated. They do not replace native Windows verification.

Verification on 20 September 2026: macOS 26.6.2 with Python 3.14.4 and 3.9.6 each ran 67 tests: 64 passed and 3 Windows-only tests were skipped. The installer tests included PowerShell 7.6.6 running on macOS. A temporary installation fetched the live repository archive and passed 6 live PNG downloads across command, interactive and BOM/CRLF batch modes, including output replacement and paths with spaces and brackets. Earlier runtime verification that day also passed interactive terminal checks. Native Windows testing remains pending, including Windows PowerShell 5.1, launcher arguments and exit status, installation/PATH discovery, Ctrl+C and Ctrl+Z, Windows-created batch files and read-only replacement failures. Linux and a Python 3.8 runtime were not tested in that run.

Check spell mappings against a local dump without writing:

```bash
python3 -B scripts/update_spells.py /path/to/ao-bin-dumps --revision COMMIT_SHA --check
```

For an authorized update, omit `--check`. Use the full commit SHA matching the supplied dump. The updater preserves reviewed mappings and fails when an ID or labelled icon needs review; resolve those cases deliberately. It never rewrites the lists or Python source. Record verified source revisions and dates in `CATALOGUE.md`.

For catalogue or installation changes, verify representative identifier exceptions, all input modes, and the installed three-file runtime for the relevant platform from another working directory, including paths with spaces. Use temporary installations for verification; keep downloaded test images and game-data dumps outside the repository.

## Documentation and Git

- `README.md` contains only concrete installation and usage instructions, preceded by one line stating that the code is written and maintained using AI (Codex), and one line linking to the data dump repository. Keep catalogue internals and maintenance instructions out of it.
- Keep agent guidance here and catalogue reference material in `CATALOGUE.md`.
- Inspect the working tree before editing and preserve unrelated user changes. Do not delete the original external catalogue folder without explicit authorization.
- Commit, push, publication and installation into the user's PATH require explicit authorization beyond local implementation.
- Infer the GitHub identity from the repository owner, remotes and effective configuration. Before committing, verify author and signing identities; before GitHub operations, verify the owner and authenticated account. Stop on a mismatch. Preserve existing remotes and credential configuration; never change global Git configuration or expose secrets.
