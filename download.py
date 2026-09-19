#!/usr/bin/env python3
"""
Albion Online item and spell icon downloader.

Examples:
    download
    download Hunter Shoes 8 1 4
    download downloads.txt
"""

from __future__ import annotations

import csv
import json
import os
import re
import shlex
import stat
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from functools import lru_cache
from io import StringIO
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

BASE_URL = "https://render.albiononline.com/v1/item/"
SPELL_BASE_URL = "https://render.albiononline.com/v1/spell/"
TIMEOUT = 15.0
DEFAULT_WORKERS = 6
CUSTOM_CATALOG_ENV = "ALBION_ITEM_CATALOG_PATH"

QUALITY_WORD = {
    1: "Common",
    2: "Good",
    3: "Outstanding",
    4: "Excellent",
    5: "Masterpiece",
}

USAGE = """Usage:
  download
  download Spell Name
  download Item Name <tier> [enchant] [quality]
  download <file.txt>
  download --help

Examples:
  download Refreshing Sprint
  download Hunter Shoes 8 1 4
  download Guardian Armor 6
  download downloads.txt

Notes:
  download with no arguments reads one request per line, with no visible prompt
  exit, quit, Ctrl+D, or Ctrl+C leaves interactive mode
  names containing spaces do not need quotes
  interactive and file lines use: Name [tier [enchant [quality]]]
  spell names are resolved to IDs using the embedded English spell catalog
  known spell IDs are also accepted; ambiguous names list the matching IDs
  a name without item values is treated as a spell unless it is a known tierless item
  enchant defaults to 0
  quality defaults to 1
  successful file entries are removed from the file
  failed file entries stay in the file
  set ALBION_ITEM_CATALOG_PATH to merge in a custom item catalog
"""

_NAME_SPACE_RX = re.compile(r"\s+")
_INTEGER_RX = re.compile(r"[+-]?[0-9]+")
_TIER_PREFIX_RX = re.compile(r"^T([1-8])_")
_ENCHANT_SUFFIX_RX = re.compile(r"@([1-4])$")


@dataclass(frozen=True)
class ItemEntry:
    name: str
    identifiers_by_tier: Mapping[int, str]
    base_identifier: str = ""
    fixed_identifier: str = ""


@dataclass(frozen=True)
class BatchRequest:
    name: str
    tier: int
    enchant: int
    quality: int
    original_line: str
    line_number: int


def print_usage() -> None:
    print(USAGE.rstrip())


def norm_name(name: str) -> str:
    return _NAME_SPACE_RX.sub(" ", name.strip()).casefold()


def safe_file_stem(text: str) -> str:
    return text.replace("/", "-").replace("\\", "-")


def parse_embedded_items(csv_text: str) -> Dict[str, ItemEntry]:
    catalog: Dict[str, ItemEntry] = {}
    cleaned: List[str] = []
    for raw in csv_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cleaned.append(line)

    rdr = csv.reader(StringIO("\n".join(cleaned)))
    for row in rdr:
        if len(row) < 2:
            continue
        name = row[0].strip()
        ident = row[1].strip()
        if name and ident:
            catalog[norm_name(name)] = build_embedded_entry(name, ident)
    return catalog


@lru_cache(maxsize=1)
def load_spell_catalog() -> Tuple[Dict[str, Tuple[str, ...]], Dict[str, str]]:
    names: Dict[str, List[str]] = {}
    identifiers: Dict[str, str] = {}
    for row in csv.reader(StringIO(SPELLS_CSV), skipinitialspace=True):
        if not row or row[0].startswith("#"):
            continue
        name, identifier = row[:2]
        identifiers[norm_name(identifier)] = identifier
        # Recasts and item-specific variants remain accessible by their exact IDs.
        if len(row) == 2:
            names.setdefault(norm_name(name), []).append(identifier)
    return {name: tuple(ids) for name, ids in names.items()}, identifiers


def resolve_spell(name: str) -> str:
    names, identifiers = load_spell_catalog()
    key = norm_name(name)
    identifier = identifiers.get(key)
    # An exact uppercase ID takes precedence over a coincident English name.
    if identifier == name.strip():
        return identifier
    matches = names.get(key, ())
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"Spell name '{name}' is ambiguous. Use an ID: {', '.join(matches)}.")
    if identifier:
        return identifier
    raise ValueError(f"'{name}' is not in the spell catalog.")


def build_embedded_entry(name: str, ident: str) -> ItemEntry:
    if has_tier_prefix(ident):
        return ItemEntry(name=name, identifiers_by_tier={extract_tier_from_ident(ident): ident})
    if is_unique_identifier(ident):
        return ItemEntry(name=name, identifiers_by_tier={}, fixed_identifier=ident)
    return ItemEntry(name=name, identifiers_by_tier={}, base_identifier=ident)


def load_item_catalog() -> Dict[str, ItemEntry]:
    catalog = parse_embedded_items(ITEMS_CSV)

    custom_catalog_path = os.environ.get(CUSTOM_CATALOG_ENV)
    if custom_catalog_path:
        if not os.path.isfile(custom_catalog_path):
            raise FileNotFoundError(f"Custom item catalog not found: {custom_catalog_path}")
        catalog.update(load_custom_catalog(custom_catalog_path))

    return catalog


def load_custom_catalog(path: str) -> Dict[str, ItemEntry]:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)

    catalog: Dict[str, ItemEntry] = {}
    if not isinstance(data, list):
        return catalog

    for item in data:
        if not isinstance(item, dict):
            continue

        name = item.get("name")
        identifiers = item.get("identifiersByTier")
        if not isinstance(name, str) or not isinstance(identifiers, dict):
            continue

        by_tier: Dict[int, str] = {}
        for raw_tier, raw_ident in identifiers.items():
            try:
                tier = int(raw_tier)
            except (TypeError, ValueError):
                continue
            if 1 <= tier <= 8 and isinstance(raw_ident, str) and raw_ident.strip():
                by_tier[tier] = raw_ident.strip()

        if by_tier:
            catalog[norm_name(name)] = ItemEntry(name=name.strip(), identifiers_by_tier=by_tier)

    return catalog


def has_tier_prefix(ident: str) -> bool:
    return _TIER_PREFIX_RX.match(ident) is not None


def extract_tier_from_ident(ident: str) -> int:
    match = _TIER_PREFIX_RX.match(ident)
    return int(match.group(1)) if match else -1


def is_unique_identifier(ident: str) -> bool:
    return ident.startswith("UNIQUE")


def get_identifier_enchantment(identifier: str) -> int:
    match = _ENCHANT_SUFFIX_RX.search(identifier)
    return int(match.group(1)) if match else -1


def parse_int(value: str, field_name: str) -> int:
    if not _INTEGER_RX.fullmatch(value):
        raise ValueError(f"Invalid {field_name}: {value!r}.")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}.") from exc


def validate_item_values(name: str, tier: int, enchant: int, quality: int) -> None:
    if not name.strip():
        raise ValueError("Item name cannot be empty.")
    if not (1 <= tier <= 8):
        raise ValueError(f"Invalid tier for '{name}': {tier}. Tier must be between 1 and 8.")
    if not (0 <= enchant <= 4):
        raise ValueError(
            f"Invalid enchantment for '{name}': {enchant}. Enchantment must be between 0 and 4."
        )
    if not (1 <= quality <= 5):
        raise ValueError(f"Invalid quality for '{name}': {quality}. Quality must be between 1 and 5.")


def split_request_line(line: str) -> List[str]:
    line = line.strip()
    # Keep older quoted files working without treating literal apostrophes as quotes.
    if line.startswith(('"', "'")):
        return shlex.split(line, comments=False, posix=True)
    return line.split()


def parse_request(args: Sequence[str]) -> Tuple[str, int, int, int]:
    value_index = len(args)
    for index, value in enumerate(args):
        if _INTEGER_RX.fullmatch(value):
            value_index = index
            break

    name = " ".join(args[:value_index]).strip()
    if not name:
        raise ValueError("Name cannot be empty.")
    values = args[value_index:]
    if len(values) > 3:
        raise ValueError("Expected: Name [tier [enchantment [quality]]].")
    if not values:
        return name, -1, 0, 1

    tier = parse_int(values[0], "tier")
    enchant = parse_int(values[1], "enchantment") if len(values) >= 2 else 0
    quality = parse_int(values[2], "quality") if len(values) >= 3 else 1
    validate_item_values(name, tier, enchant, quality)
    return name, tier, enchant, quality


def build_identifier(base_ident: str, tier: int, enchant: int) -> Tuple[str, int]:
    if has_tier_prefix(base_ident):
        tier_for_name = extract_tier_from_ident(base_ident)
        core = base_ident
    elif is_unique_identifier(base_ident):
        tier_for_name = -1
        core = base_ident
    else:
        if tier == -1:
            raise ValueError("Tier is required for items without an embedded T1_...T8_ identifier.")
        tier_for_name = tier
        core = f"T{tier}_{base_ident}"

    ident = core if enchant == 0 else f"{core}@{enchant}"
    return ident, tier_for_name


def apply_enchantment(identifier: str, name: str, tier: int, enchant: int) -> str:
    inherent_enchant = get_identifier_enchantment(identifier)
    if inherent_enchant != -1:
        if enchant > 0 and enchant != inherent_enchant:
            raise ValueError(
                f"Albion item '{name}' tier {tier} enchantment {enchant} does not match identifier {identifier}."
            )
        return identifier

    return identifier if enchant == 0 else f"{identifier}@{enchant}"


def build_url(identifier: str, quality: int) -> str:
    if quality == 1:
        return f"{BASE_URL}{identifier}.png"
    return f"{BASE_URL}{identifier}.png?quality={quality}"


def build_spell_url(name: str) -> str:
    identifier = resolve_spell(name)
    return f"{SPELL_BASE_URL}{urllib.parse.quote(identifier, safe='')}.png"


def build_filename(name: str, tier: int, enchant: int, quality: int, *, include_tier: bool = True) -> str:
    stem = safe_file_stem(name)
    if include_tier and tier != -1:
        stem += f" {tier}"
    if enchant > 0:
        stem += f".{enchant}"
    if quality > 1:
        stem += f" {QUALITY_WORD.get(quality, str(quality))}"
    return stem + ".png"


def download_one(url: str, filepath: str) -> None:
    with urllib.request.urlopen(url, timeout=TIMEOUT) as response:
        data = response.read()

    output_dir = os.path.dirname(os.path.abspath(filepath))
    prefix = f".{os.path.basename(filepath)}."
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=output_dir, prefix=prefix, suffix=".part", delete=False
        ) as handle:
            tmp_path = handle.name
            handle.write(data)
        try:
            output_mode = stat.S_IMODE(os.stat(filepath).st_mode)
        except FileNotFoundError:
            output_mode = 0o644
        os.chmod(tmp_path, output_mode)
        os.replace(tmp_path, filepath)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def format_download_error(error: BaseException) -> str:
    if isinstance(error, urllib.error.HTTPError):
        return f"server returned HTTP {error.code}"
    if isinstance(error, urllib.error.URLError):
        if isinstance(error.reason, TimeoutError):
            return f"request timed out after {TIMEOUT:g} seconds; try again"
        return str(error.reason)
    if isinstance(error, TimeoutError):
        return f"request timed out after {TIMEOUT:g} seconds; try again"
    return str(error)


def parse_requests_file(path: str) -> Tuple[List[BatchRequest], List[Tuple[int, str]]]:
    requests_to_run: List[BatchRequest] = []
    rejected_lines: List[Tuple[int, str]] = []

    with open(path, "r", encoding="utf-8", newline="") as handle:
        for line_number, raw_line in enumerate(handle, start=1):
            original_line = raw_line.rstrip("\r\n")
            stripped_line = original_line.strip()
            if not stripped_line or stripped_line.startswith("#"):
                continue

            try:
                name, tier, enchant, quality = parse_request(split_request_line(stripped_line))
            except ValueError as exc:
                print(f"[FAIL] Line {line_number}: {exc} Leaving line in file.")
                rejected_lines.append((line_number, original_line))
                continue

            requests_to_run.append(
                BatchRequest(name, tier, enchant, quality, original_line, line_number)
            )

    return requests_to_run, rejected_lines


def resolve_item(
    catalog: Mapping[str, ItemEntry], name: str, tier: int, enchant: int, quality: int
) -> Tuple[str, str]:
    key = norm_name(name)
    if key not in catalog:
        raise ValueError(f"'{name}' is not in the item catalog.")

    entry = catalog[key]
    identifier = ""
    tier_for_filename = tier
    include_tier = tier != -1

    if entry.identifiers_by_tier:
        if tier == -1:
            if len(entry.identifiers_by_tier) != 1:
                tiers = ", ".join(str(value) for value in sorted(entry.identifiers_by_tier))
                raise ValueError(f"Tier is required for '{name}'. Available tiers: {tiers}.")
            tier_for_filename, identifier = next(iter(entry.identifiers_by_tier.items()))
            include_tier = False
        else:
            identifier = entry.identifiers_by_tier.get(tier, "")
            if not identifier:
                tiers = ", ".join(str(value) for value in sorted(entry.identifiers_by_tier))
                raise ValueError(f"'{name}' is not available at tier {tier}. Available tiers: {tiers}.")
    elif entry.fixed_identifier:
        identifier = entry.fixed_identifier
        tier_for_filename = -1
        include_tier = False
        if tier != -1:
            print(f"[WARN] '{name}': supplied tier {tier} ignored; identifier does not use tier prefixes.")
    else:
        identifier, tier_for_filename = build_identifier(entry.base_identifier, tier, 0)
        include_tier = True

    identifier = apply_enchantment(identifier, name, tier_for_filename, enchant)
    if tier == -1 and enchant > 0 and tier_for_filename != -1:
        include_tier = True
    filename = build_filename(name, tier_for_filename, enchant, quality, include_tier=include_tier)
    return identifier, filename


def download_item_to_directory(
    catalog: Mapping[str, ItemEntry], name: str, tier: int, enchant: int, quality: int, output_dir: str
) -> str:
    validate_item_values(name, tier, enchant, quality)
    return download_catalog_item_to_directory(catalog, name, tier, enchant, quality, output_dir)


def download_catalog_item_to_directory(
    catalog: Mapping[str, ItemEntry],
    name: str,
    tier: int,
    enchant: int,
    quality: int,
    output_dir: str,
) -> str:
    identifier, filename = resolve_item(catalog, name, tier, enchant, quality)
    url = build_url(identifier, quality)
    output_path = os.path.join(output_dir, filename)
    download_one(url, output_path)
    return output_path


def rewrite_failed_lines(path: str, failed_lines: Sequence[str]) -> None:
    path = os.path.abspath(path)
    input_mode = stat.S_IMODE(os.stat(path).st_mode)
    directory = os.path.dirname(path)
    prefix = f".{os.path.basename(path)}."
    tmp_path = ""
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=directory,
            prefix=prefix,
            suffix=".tmp",
            delete=False,
        ) as handle:
            tmp_path = handle.name
            for line in failed_lines:
                handle.write(line + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, input_mode)
        os.replace(tmp_path, path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


def bounded_worker_count(entries: Iterable[object]) -> int:
    count = sum(1 for _ in entries)
    if count <= 1:
        return 1
    return min(DEFAULT_WORKERS, count)


def is_tierless_catalog_item(catalog: Mapping[str, ItemEntry], name: str) -> bool:
    entry = catalog.get(norm_name(name))
    return entry is not None and (
        bool(entry.fixed_identifier) or len(entry.identifiers_by_tier) == 1
    )


def run_batch_entry(
    catalog: Mapping[str, ItemEntry], output_dir: str, entry: BatchRequest
) -> Tuple[str, str]:
    name = entry.name
    tier = entry.tier
    enchant = entry.enchant
    quality = entry.quality
    if tier == -1 and enchant == 0 and quality == 1 and not is_tierless_catalog_item(catalog, name):
        filename = safe_file_stem(name) + ".png"
        output_path = os.path.join(output_dir, filename)
        download_one(build_spell_url(name), output_path)
        return name, output_path

    output_path = download_catalog_item_to_directory(
        catalog, name, tier, enchant, quality, output_dir
    )
    return name, output_path


def run_batch_download(catalog: Mapping[str, ItemEntry], input_path: str, output_dir: str) -> int:
    if not os.path.isfile(input_path):
        print(f"[FAIL] File not found: {input_path}")
        return 1

    try:
        entries, failed_lines = parse_requests_file(input_path)
    except (OSError, UnicodeError) as exc:
        print(f"[FAIL] Could not read batch file '{input_path}': {exc}")
        return 1
    success_count = 0
    workers = bounded_worker_count(entries)

    if entries and workers > 1:
        print(f"[INFO] Downloading {len(entries)} icon(s) with {workers} workers.")

    with ThreadPoolExecutor(max_workers=workers) as executor:
        pending = [
            (executor.submit(run_batch_entry, catalog, output_dir, entry), entry)
            for entry in entries
        ]
        for future, entry in pending:
            try:
                name, output_path = future.result()
                success_count += 1
                print(f"[OK] {name} -> {output_path}")
            except Exception as exc:
                print(f"[FAIL] {entry.name}: {format_download_error(exc)}")
                failed_lines.append((entry.line_number, entry.original_line))

    failed_lines.sort(key=lambda failed: failed[0])
    lines_to_keep = [line for _, line in failed_lines]
    try:
        rewrite_failed_lines(input_path, lines_to_keep)
    except OSError as exc:
        print(f"[FAIL] Could not update batch file '{input_path}': {exc}")
        return 1

    if failed_lines:
        print(
            f"[INFO] Downloaded {success_count} icon(s). "
            f"Left {len(failed_lines)} failed line(s) in {input_path}."
        )
        return 1

    print(f"[INFO] Downloaded {success_count} icon(s). {input_path} is now empty.")
    return 0


def run_single_download(
    catalog: Mapping[str, ItemEntry], args: Sequence[str], output_dir: str
) -> int:
    try:
        name, tier, enchant, quality = parse_request(args)
    except ValueError as exc:
        print(f"[FAIL] {exc}")
        return 1

    if tier == -1:
        if is_tierless_catalog_item(catalog, name):
            try:
                output_path = download_catalog_item_to_directory(
                    catalog, name, -1, 0, 1, output_dir
                )
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
                print(f"[FAIL] Download failed for '{name}': {format_download_error(exc)}.")
                return 1
            except Exception as exc:
                print(f"[FAIL] {exc}")
                return 1

            print(f"[OK] Downloaded '{name}' to {output_path}")
            return 0

        output_path = os.path.join(output_dir, safe_file_stem(name) + ".png")
        try:
            download_one(build_spell_url(name), output_path)
        except ValueError as exc:
            print(f"[FAIL] {exc}")
            return 1
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            print(f"[FAIL] Download failed for spell '{name}': {format_download_error(exc)}.")
            return 1
        except Exception as exc:
            print(f"[FAIL] {exc}")
            return 1

        print(f"[OK] Downloaded spell '{name}' to {output_path}")
        return 0

    try:
        output_path = download_item_to_directory(catalog, name, tier, enchant, quality, output_dir)
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
        print(f"[FAIL] Download failed for '{name}': {format_download_error(exc)}.")
        return 1
    except Exception as exc:
        print(f"[FAIL] {exc}")
        return 1

    print(f"[OK] Downloaded '{name}' to {output_path}")
    return 0


def run_interactive(catalog: Mapping[str, ItemEntry], output_dir: str) -> int:
    status = 0
    try:
        while True:
            try:
                line = input().strip()
            except EOFError:
                return status
            if line.casefold() in ("exit", "quit"):
                return status
            if not line or line.startswith("#"):
                continue
            try:
                args = split_request_line(line)
            except ValueError as exc:
                print(f"[FAIL] {exc}")
                status = 1
                continue
            if run_single_download(catalog, args, output_dir):
                status = 1
    except KeyboardInterrupt:
        print()
        return 130


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        print_usage()
        return 0

    try:
        catalog = load_item_catalog()
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"[FAIL] Could not load item catalog: {exc}")
        return 1
    if not catalog:
        print("[FAIL] Item catalog is empty.")
        return 1

    output_dir = os.getcwd()
    os.makedirs(output_dir, exist_ok=True)

    if not args:
        return run_interactive(catalog, output_dir)

    if len(args) == 1 and os.path.isfile(args[0]):
        return run_batch_download(catalog, args[0], output_dir)

    return run_single_download(catalog, args, output_dir)

# =========================
# Embedded item data (CSV)
# =========================
ITEMS_CSV = """
# WEAPONS
Bow, 2H_BOW
Warbow, 2H_WARBOW
Longbow, 2H_LONGBOW
Whispering Bow, 2H_LONGBOW_UNDEAD
Wailing Bow, 2H_BOW_HELL
Bow of Badon, 2H_BOW_KEEPER
Mistpiercer, 2H_BOW_AVALON
Skystrider Bow, 2H_BOW_CRYSTAL
Crossbow, 2H_CROSSBOW
Heavy Crossbow, 2H_CROSSBOWLARGE
Light Crossbow, MAIN_1HCROSSBOW
Weeping Repeater, 2H_REPEATINGCROSSBOW_UNDEAD
Boltcasters, 2H_DUALCROSSBOW_HELL
Siegebow, 2H_CROSSBOWLARGE_MORGANA
Energy Shaper, 2H_CROSSBOW_CANNON_AVALON
Arclight Blasters, 2H_DUALCROSSBOW_CRYSTAL
Cursed Staff, MAIN_CURSEDSTAFF
Great Cursed Staff, 2H_CURSEDSTAFF
Demonic Staff, 2H_DEMONICSTAFF
Lifecurse Staff, MAIN_CURSEDSTAFF_UNDEAD
Cursed Skull, 2H_SKULLORB_HELL
Damnation Staff, 2H_CURSEDSTAFF_MORGANA
Shadowcaller, MAIN_CURSEDSTAFF_AVALON
Rotcaller Staff, MAIN_CURSEDSTAFF_CRYSTAL
Fire Staff, MAIN_FIRESTAFF
Great Fire Staff, 2H_FIRESTAFF
Vendetta's Wrath, 2H_FIRESTAFF
Infernal Staff, 2H_INFERNOSTAFF
Wildfire Staff, MAIN_FIRESTAFF_KEEPER
Brimstone Staff, 2H_FIRESTAFF_HELL
Blazing Staff, 2H_INFERNOSTAFF_MORGANA
Dawnsong, 2H_FIRE_RINGPAIR_AVALON
Flamewalker Staff, MAIN_FIRESTAFF_CRYSTAL
Frost Staff, MAIN_FROSTSTAFF
Great Frost Staff, 2H_FROSTSTAFF
Glacial Staff, 2H_GLACIALSTAFF
Hoarfrost Staff, MAIN_FROSTSTAFF_KEEPER
Icicle Staff, 2H_ICEGAUNTLETS_HELL
Permafrost Prism, 2H_ICECRYSTAL_UNDEAD
Chillhowl, MAIN_FROSTSTAFF_AVALON
Arctic Staff, 2H_FROSTSTAFF_CRYSTAL
Arcane Staff, MAIN_ARCANESTAFF
Great Arcane Staff, 2H_ARCANESTAFF
Enigmatic Staff, 2H_ENIGMATICSTAFF
Witchwork Staff, MAIN_ARCANESTAFF_UNDEAD
Occult Staff, 2H_ARCANESTAFF_HELL
Malevolent Locus, 2H_ENIGMATICORB_MORGANA
Evensong, 2H_ARCANE_RINGPAIR_AVALON
Astral Staff, 2H_ARCANESTAFF_CRYSTAL
Holy Staff, MAIN_HOLYSTAFF
Great Holy Staff, 2H_HOLYSTAFF
Divine Staff, 2H_DIVINESTAFF
Lifetouch Staff, MAIN_HOLYSTAFF_MORGANA
Fallen Staff, 2H_HOLYSTAFF_HELL
Redemption Staff, 2H_HOLYSTAFF_UNDEAD
Hallowfall, MAIN_HOLYSTAFF_AVALON
Exalted Staff, 2H_HOLYSTAFF_CRYSTAL
Nature Staff, MAIN_NATURESTAFF
Great Nature Staff, 2H_NATURESTAFF
Wild Staff, 2H_WILDSTAFF
Druidic Staff, MAIN_NATURESTAFF_KEEPER
Blight Staff, 2H_NATURESTAFF_HELL
Rampant Staff, 2H_NATURESTAFF_KEEPER
Ironroot Staff, MAIN_NATURESTAFF_AVALON
Forgebark Staff, MAIN_NATURESTAFF_CRYSTAL
Dagger, MAIN_DAGGER
Dagger Pair, 2H_DAGGERPAIR
Claws, 2H_CLAWPAIR
Bloodletter, MAIN_RAPIER_MORGANA
Demonfang, MAIN_DAGGER_HELL
Deathgivers, 2H_DUALSICKLE_UNDEAD
Bridled Fury, 2H_DAGGER_KATAR_AVALON
Twin Slayers, 2H_DAGGERPAIR_CRYSTAL
Spear, MAIN_SPEAR
Pike, 2H_SPEAR
Glaive, 2H_GLAIVE
Heron Spear, MAIN_SPEAR_KEEPER
Spirithunter, 2H_HARPOON_HELL
Trinity Spear, 2H_TRIDENT_UNDEAD
Daybreaker, MAIN_SPEAR_LANCE_AVALON
Rift Glaive, 2H_GLAIVE_CRYSTAL
Battleaxe, MAIN_AXE
Greataxe, 2H_AXE
The Hand of Khor, 2H_AXE
Halberd, 2H_HALBERD
Carrioncaller, 2H_HALBERD_MORGANA
Infernal Scythe, 2H_SCYTHE_HELL
Bear Paws, 2H_DUALAXE_KEEPER
Realmbreaker, 2H_AXE_AVALON
Crystal Reaper, 2H_SCYTHE_CRYSTAL
Broadsword, MAIN_SWORD
Claymore, 2H_CLAYMORE
Dual Swords, 2H_DUALSWORD
Clarent Blade, MAIN_SCIMITAR_MORGANA
Carving Sword, 2H_CLEAVER_HELL
Galatine Pair, 2H_DUALSCIMITAR_UNDEAD
Kingmaker, 2H_CLAYMORE_AVALON
Infinity Blade, MAIN_SWORD_CRYSTAL
Quarterstaff, 2H_QUARTERSTAFF
Iron-clad Staff, 2H_IRONCLADEDSTAFF
Double Bladed Staff, 2H_DOUBLEBLADEDSTAFF
Black Monk Stave, 2H_COMBATSTAFF_MORGANA
Soulscythe, 2H_TWINSCYTHE_HELL
Staff of Balance, 2H_ROCKSTAFF_KEEPER
Grailseeker, 2H_QUARTERSTAFF_AVALON
Phantom Twinblade, 2H_DOUBLEBLADEDSTAFF_CRYSTAL
Hammer, MAIN_HAMMER
Polehammer, 2H_POLEHAMMER
Great Hammer, 2H_HAMMER
Tombhammer, 2H_HAMMER_UNDEAD
Forge Hammers, 2H_DUALHAMMER_HELL
Grovekeeper, 2H_RAM_KEEPER
Hand of Justice, 2H_HAMMER_AVALON
Truebolt Hammer, 2H_HAMMER_CRYSTAL
Mace, MAIN_MACE
Heavy Mace, 2H_MACE
Morning Star, 2H_FLAIL
Bedrock Mace, MAIN_ROCKMACE_KEEPER
Incubus Mace, MAIN_MACE_HELL
Camlann Mace, 2H_MACE_MORGANA
Oathkeepers, 2H_DUALMACE_AVALON
Dreadstorm Monarch, MAIN_MACE_CRYSTAL
Brawler Gloves, 2H_KNUCKLES_SET1
Battle Bracers, 2H_KNUCKLES_SET2
Spiked Gauntlets, 2H_KNUCKLES_SET3
Ursine Maulers, 2H_KNUCKLES_KEEPER
Hellfire Hands, 2H_KNUCKLES_HELL
Ravenstrike Cestus, 2H_KNUCKLES_MORGANA
Fists of Avalon, 2H_KNUCKLES_AVALON
Forcepulse Bracers, 2H_KNUCKLES_CRYSTAL
Prowling Staff, 2H_SHAPESHIFTER_SET1
Rootbound Staff, 2H_SHAPESHIFTER_SET2
Primal Staff, 2H_SHAPESHIFTER_SET3
Bloodmoon Staff, 2H_SHAPESHIFTER_MORGANA
Hellspawn Staff, 2H_SHAPESHIFTER_HELL
Earthrune Staff, 2H_SHAPESHIFTER_KEEPER
Lightcaller, 2H_SHAPESHIFTER_AVALON
Stillgaze Staff, 2H_SHAPESHIFTER_CRYSTAL

# OFFHANDS

Shield, OFF_SHIELD
Sarcophagus, OFF_TOWERSHIELD_UNDEAD
Caitiff Shield, OFF_SHIELD_HELL
Facebreaker, OFF_SPIKEDSHIELD_MORGANA
Astral Aegis, OFF_SHIELD_AVALON
Tome of Spells, OFF_BOOK
Eye of Secrets, OFF_ORB_MORGANA
Muisak, OFF_DEMONSKULL_HELL
Taproot, OFF_TOTEM_KEEPER
Celestial Censer, OFF_CENSER_AVALON
Torch, OFF_TORCH
Mistcaller, OFF_HORN_KEEPER
Sacred Scepter, OFF_TALISMAN_AVALON
Cryptcandle, OFF_LAMP_UNDEAD
Leering Cane, OFF_JESTERCANE_HELL
Timelocked Grimoire, OFF_TOME_CRYSTAL
Blueflame Torch, OFF_TORCH_CRYSTAL
Unbreakable Ward, OFF_SHIELD_CRYSTAL

# ARMORS
Soldier Helmet, HEAD_PLATE_SET1
Soldier Armor, ARMOR_PLATE_SET1
Soldier Boots, SHOES_PLATE_SET1
Knight Helmet, HEAD_PLATE_SET2
Knight Armor, ARMOR_PLATE_SET2
Knight Boots, SHOES_PLATE_SET2
Guardian Helmet, HEAD_PLATE_SET3
Guardian Armor, ARMOR_PLATE_SET3
Guardian Boots, SHOES_PLATE_SET3
Graveguard Helmet, HEAD_PLATE_UNDEAD
Graveguard Armor, ARMOR_PLATE_UNDEAD
Graveguard Boots, SHOES_PLATE_UNDEAD
Demon Helmet, HEAD_PLATE_HELL
Demon Armor, ARMOR_PLATE_HELL
Demon Boots, SHOES_PLATE_HELL
Judicator Helmet, HEAD_PLATE_KEEPER
Judicator Armor, ARMOR_PLATE_KEEPER
Judicator Boots, SHOES_PLATE_KEEPER
Duskweaver Helmet, HEAD_PLATE_FEY
Duskweaver Armor, ARMOR_PLATE_FEY
Duskweaver Boots, SHOES_PLATE_FEY
Helmet of Valor, HEAD_PLATE_AVALON
Armor of Valor, ARMOR_PLATE_AVALON
Boots of Valor, SHOES_PLATE_AVALON
Mercenary Hood, HEAD_LEATHER_SET1
Mercenary Jacket, ARMOR_LEATHER_SET1
Mercenary Shoes, SHOES_LEATHER_SET1
Hunter Hood, HEAD_LEATHER_SET2
Hunter Jacket, ARMOR_LEATHER_SET2
Hunter Shoes, SHOES_LEATHER_SET2
Assassin Hood, HEAD_LEATHER_SET3
Assassin Jacket, ARMOR_LEATHER_SET3
Assassin Shoes, SHOES_LEATHER_SET3
Stalker Hood, HEAD_LEATHER_MORGANA
Stalker Jacket, ARMOR_LEATHER_MORGANA
Stalker Shoes, SHOES_LEATHER_MORGANA
Hellion Hood, HEAD_LEATHER_HELL
Hellion Jacket, ARMOR_LEATHER_HELL
Hellion Shoes, SHOES_LEATHER_HELL
Specter Hood, HEAD_LEATHER_UNDEAD
Specter Jacket, ARMOR_LEATHER_UNDEAD
Specter Shoes, SHOES_LEATHER_UNDEAD
Mistwalker Hood, HEAD_LEATHER_FEY
Mistwalker Jacket, ARMOR_LEATHER_FEY
Mistwalker Shoes, SHOES_LEATHER_FEY
Hood of Tenacity, HEAD_LEATHER_AVALON
Jacket of Tenacity, ARMOR_LEATHER_AVALON
Shoes of Tenacity, SHOES_LEATHER_AVALON
Dragonslayer Hood, HEAD_LEATHER_DRAGON
Dragonslayer Jacket, ARMOR_LEATHER_DRAGON
Dragonslayer Shoes, SHOES_LEATHER_DRAGON
Scholar Cowl, HEAD_CLOTH_SET1
Scholar Robe, ARMOR_CLOTH_SET1
Scholar Sandals, SHOES_CLOTH_SET1
Cleric Cowl, HEAD_CLOTH_SET2
Cleric Robe, ARMOR_CLOTH_SET2
Cleric Sandals, SHOES_CLOTH_SET2
Mage Cowl, HEAD_CLOTH_SET3
Mage Robe, ARMOR_CLOTH_SET3
Mage Sandals, SHOES_CLOTH_SET3
Druid Cowl, HEAD_CLOTH_KEEPER
Druid Robe, ARMOR_CLOTH_KEEPER
Druid Sandals, SHOES_CLOTH_KEEPER
Fiend Cowl, HEAD_CLOTH_HELL
Fiend Robe, ARMOR_CLOTH_HELL
Fiend Sandals, SHOES_CLOTH_HELL
Cultist Cowl, HEAD_CLOTH_MORGANA
Cultist Robe, ARMOR_CLOTH_MORGANA
Cultist Sandals, SHOES_CLOTH_MORGANA
Feyscale Hat, HEAD_CLOTH_FEY
Feyscale Robe, ARMOR_CLOTH_FEY
Feyscale Sandals, SHOES_CLOTH_FEY
Cowl of Purity, HEAD_CLOTH_AVALON
Robe of Purity, ARMOR_CLOTH_AVALON
Sandals of Purity, SHOES_CLOTH_AVALON
Royal Cowl, HEAD_CLOTH_ROYAL
Royal Robe, ARMOR_CLOTH_ROYAL
Royal Sandals, SHOES_CLOTH_ROYAL
Royal Hood, HEAD_LEATHER_ROYAL
Royal Jacket, ARMOR_LEATHER_ROYAL
Royal Shoes, SHOES_LEATHER_ROYAL
Royal Helmet, HEAD_PLATE_ROYAL
Royal Armor, ARMOR_PLATE_ROYAL
Royal Boots, SHOES_PLATE_ROYAL

# CAPES
Cape, CAPE
Bridgewatch Cape, CAPEITEM_FW_BRIDGEWATCH
Fort Sterling Cape, CAPEITEM_FW_FORTSTERLING
Lymhurst Cape, CAPEITEM_FW_LYMHURST
Martlock Cape, CAPEITEM_FW_MARTLOCK
Thetford Cape, CAPEITEM_FW_THETFORD
Caerleon Cape, CAPEITEM_FW_CAERLEON
Brecilien Cape, CAPEITEM_FW_BRECILIEN
Avalonian Cape, CAPEITEM_AVALON
Smuggler Cape, CAPEITEM_SMUGGLER
Heretic Cape, CAPEITEM_HERETIC
Undead Cape, CAPEITEM_UNDEAD
Keeper Cape, CAPEITEM_KEEPER
Morgana Cape, CAPEITEM_MORGANA
Demon Cape, CAPEITEM_DEMON

# BAGS
Bag, BAG
Satchel of Insight, BAG_INSIGHT

# MOUNTS
Mule, T2_MOUNT_MULE
Riding Horse, MOUNT_HORSE
Armored Horse, MOUNT_ARMORED_HORSE
Transport Ox, MOUNT_OX
Giant Stag, T4_MOUNT_GIANTSTAG
Moose, T6_MOUNT_GIANTSTAG_MOOSE
Direwolf, T6_MOUNT_DIREWOLF
Saddled Direboar, T7_MOUNT_DIREBOAR
Saddled Swamp Dragon, T7_MOUNT_SWAMPDRAGON
Saddled Direbear, T8_MOUNT_DIREBEAR
Transport Mammoth, T8_MOUNT_MAMMOTH_TRANSPORT
Saddled Moabird, T5_MOUNT_MOABIRD_FW_BRIDGEWATCH
Saddled Winter Bear, T5_MOUNT_DIREBEAR_FW_FORTSTERLING
Saddled Wild Boar, T5_MOUNT_DIREBOAR_FW_LYMHURST
Saddled Bighorn Ram, T5_MOUNT_RAM_FW_MARTLOCK
Saddled Swamp Salamander, T5_MOUNT_SWAMPDRAGON_FW_THETFORD
Saddled Greywolf, T5_MOUNT_GREYWOLF_FW_CAERLEON
Saddled Mystic Owl, T5_MOUNT_OWL_FW_BRECILIEN
Elite Terrorbird, T8_MOUNT_MOABIRD_FW_BRIDGEWATCH_ELITE
Elite Winter Bear, T8_MOUNT_DIREBEAR_FW_FORTSTERLING_ELITE
Elite Wild Boar, T8_MOUNT_DIREBOAR_FW_LYMHURST_ELITE
Elite Bighorn Ram, T8_MOUNT_RAM_FW_MARTLOCK_ELITE
Elite Swamp Salamander, T8_MOUNT_SWAMPDRAGON_FW_THETFORD_ELITE
Elite Greywolf, T8_MOUNT_GREYWOLF_FW_CAERLEON_ELITE
Elite Mystic Owl, T8_MOUNT_OWL_FW_BRECILIEN_ELITE
Hellspinner, T5_MOUNT_SPIDER_HELL
Soulspinner, T8_MOUNT_SPIDER_HELL
Command Mammoth, T8_MOUNT_MAMMOTH_BATTLE
Flame Basilisk, T7_MOUNT_SWAMPDRAGON_BATTLE
Venom Basilisk, T7_MOUNT_ARMORED_SWAMPDRAGON_BATTLE
Siege Ballista, T6_MOUNT_SIEGE_BALLISTA
Spectral Bonehorse, T8_MOUNT_HORSE_UNDEAD
Swiftclaw, T5_MOUNT_COUGAR_KEEPER
Rageclaw, T8_MOUNT_COUGAR_KEEPER
Morgana Nightmare, T8_MOUNT_ARMORED_HORSE_MORGANA
Spring Cottontail, T8_MOUNT_RABBIT_EASTER
Caerleon Cottontail, T8_MOUNT_RABBIT_EASTER_DARK
Yule Ram, UNIQUE_MOUNT_RAM_XMAS
Avalonian Basilisk, T7_MOUNT_SWAMPDRAGON_AVALON_BASILISK
Recruiter's Ram, UNIQUE_MOUNT_RAM_TELLAFRIEND
Recruiter's Moabird, UNIQUE_MOUNT_MOABIRD_TELLAFRIEND
Recruiter's Saddled Bat, UNIQUE_MOUNT_BAT_TELLAFRIEND
Recruiter's Toad, UNIQUE_MOUNT_GIANTTOAD_TELLAFRIEND
Recruiter's Giant Frog, UNIQUE_MOUNT_GIANTTOAD_02_TELLAFRIEND
Warhorse, T5_MOUNT_ARMORED_HORSE_SKIN_01
Spectral Bat, UNIQUE_MOUNT_BAT_PERSONAL
Pest Lizard, T7_MOUNT_MONITORLIZARD_ADC
Snow Husky, T7_MOUNT_HUSKY_ADC
Frost Ram, T6_MOUNT_FROSTRAM_ADC
Saddled Terrorbird, T7_MOUNT_TERRORBIRD_ADC
Grizzly Bear, UNIQUE_MOUNT_BEAR_KEEPER_ADC
Black Panther, UNIQUE_MOUNT_BLACK_PANTHER_ADC
Morgana Raven, UNIQUE_MOUNT_MORGANA_RAVEN_ADC
Gallant Horse, UNIQUE_MOUNT_GIANT_HORSE_ADC
Spectral Direboar, UNIQUE_MOUNT_UNDEAD_DIREBOAR_ADC
Divine Owl, UNIQUE_MOUNT_DIVINE_OWL_ADC
Heretic Combat Mule, UNIQUE_MOUNT_HERETIC_MULE_ADC
Crystal Battle Rhino, UNIQUE_MOUNT_RHINO_SEASON_CRYSTAL
Gold Battle Rhino, UNIQUE_MOUNT_RHINO_SEASON_GOLD
Silver Battle Rhino, UNIQUE_MOUNT_RHINO_SEASON_SILVER
Bronze Battle Rhino, UNIQUE_MOUNT_RHINO_SEASON_BRONZE
Crystal Tower Chariot, UNIQUE_MOUNT_TOWER_CHARIOT_CRYSTAL
Gold Tower Chariot, UNIQUE_MOUNT_TOWER_CHARIOT_GOLD
Silver Tower Chariot, UNIQUE_MOUNT_TOWER_CHARIOT_SILVER
Crystal Battle Eagle, UNIQUE_MOUNT_ARMORED_EAGLE_CRYSTAL
Gold Battle Eagle, UNIQUE_MOUNT_ARMORED_EAGLE_GOLD
Silver Battle Eagle, UNIQUE_MOUNT_ARMORED_EAGLE_SILVER
Crystal Colossus Beetle, UNIQUE_MOUNT_BEETLE_CRYSTAL
Gold Colossus Beetle, UNIQUE_MOUNT_BEETLE_GOLD
Silver Colossus Beetle, UNIQUE_MOUNT_BEETLE_SILVER
Crystal Behemoth, UNIQUE_MOUNT_BEHEMOTH_CRYSTAL
Gold Behemoth, UNIQUE_MOUNT_BEHEMOTH_GOLD
Silver Behemoth, UNIQUE_MOUNT_BEHEMOTH_SILVER
Crystal Ancient Ent, UNIQUE_MOUNT_ENT_CRYSTAL
Gold Ancient Ent, UNIQUE_MOUNT_ENT_GOLD
Silver Ancient Ent, UNIQUE_MOUNT_ENT_SILVER
Crystal Goliath Horseeater, UNIQUE_MOUNT_BATTLESPIDER_CRYSTAL
Gold Goliath Horseeater, UNIQUE_MOUNT_BATTLESPIDER_GOLD
Silver Goliath Horseeater, UNIQUE_MOUNT_BATTLESPIDER_SILVER
Crystal Roving Bastion, UNIQUE_MOUNT_BASTION_CRYSTAL
Gold Roving Bastion, UNIQUE_MOUNT_BASTION_GOLD
Silver Roving Bastion, UNIQUE_MOUNT_BASTION_SILVER
Crystal Juggernaut, UNIQUE_MOUNT_JUGGERNAUT_CRYSTAL
Gold Juggernaut, UNIQUE_MOUNT_JUGGERNAUT_GOLD
Silver Juggernaut, UNIQUE_MOUNT_JUGGERNAUT_SILVER
Crystal Phalanx Beetle, UNIQUE_MOUNT_TANKBEETLE_CRYSTAL
Gold Phalanx Beetle, UNIQUE_MOUNT_TANKBEETLE_GOLD
Silver Phalanx Beetle, UNIQUE_MOUNT_TANKBEETLE_SILVER

# POTIONS
Healing Potion, POTION_HEAL
Energy Potion, POTION_ENERGY
Gigantify Potion, POTION_REVIVE
Resistance Potion, POTION_STONESKIN
Sticky Potion, POTION_SLOWFIELD
Poison Potion, POTION_COOLDOWN
Invisibility Potion, POTION_CLEANSE
Calming Potion, POTION_MOB_RESET
Cleansing Potion, POTION_CLEANSE2
Acid Potion, POTION_ACID
Berserk Potion, POTION_BERSERK
Hellfire Potion, POTION_LAVA
Gathering Potion, POTION_GATHER
Tornado in a Bottle, POTION_TORNADO
Lifeward Potion, POTION_LIFEWARD

# FISH
Common Rudd, T1_FISH_FRESHWATER_ALL_COMMON
Striped Carp, T2_FISH_FRESHWATER_ALL_COMMON
Albion Perch, T3_FISH_FRESHWATER_ALL_COMMON
Bluescale Pike, T4_FISH_FRESHWATER_ALL_COMMON
Spotted Trout, T5_FISH_FRESHWATER_ALL_COMMON
Brightscale Zander, T6_FISH_FRESHWATER_ALL_COMMON
Danglemouth Catfish, T7_FISH_FRESHWATER_ALL_COMMON
River Sturgeon, T8_FISH_FRESHWATER_ALL_COMMON
Common Herring, T1_FISH_SALTWATER_ALL_COMMON
Striped Mackerel, T2_FISH_SALTWATER_ALL_COMMON
Flatshore Plaice, T3_FISH_SALTWATER_ALL_COMMON
Bluescale Cod, T4_FISH_SALTWATER_ALL_COMMON
Spotted Wolffish, T5_FISH_SALTWATER_ALL_COMMON
Strongfin Salmon, T6_FISH_SALTWATER_ALL_COMMON
Bluefin Tuna, T7_FISH_SALTWATER_ALL_COMMON
Steelscale Swordfish, T8_FISH_SALTWATER_ALL_COMMON
Greenriver Eel, T3_FISH_FRESHWATER_FOREST_RARE
Redspring Eel, T5_FISH_FRESHWATER_FOREST_RARE
Deadwater Eel, T7_FISH_FRESHWATER_FOREST_RARE
Upland Coldeye, T3_FISH_FRESHWATER_MOUNTAIN_RARE
Mountain Blindeye, T5_FISH_FRESHWATER_MOUNTAIN_RARE
Frostpeak Deadeye, T7_FISH_FRESHWATER_MOUNTAIN_RARE
Stonestream Lurcher, T3_FISH_FRESHWATER_HIGHLANDS_RARE
Rushwater Lurcher, T5_FISH_FRESHWATER_HIGHLANDS_RARE
Thunderfall Lurcher, T7_FISH_FRESHWATER_HIGHLANDS_RARE
Lowriver Crab, T3_FISH_FRESHWATER_STEPPE_RARE
Drybrook Crab, T5_FISH_FRESHWATER_STEPPE_RARE
Dusthole Crab, T7_FISH_FRESHWATER_STEPPE_RARE
Greenmoor Clam, T3_FISH_FRESHWATER_SWAMP_RARE
Murkwater Clam, T5_FISH_FRESHWATER_SWAMP_RARE
Blackbog Clam, T7_FISH_FRESHWATER_SWAMP_RARE
Shallowshore Squid, T3_FISH_SALTWATER_ALL_RARE
Midwater Octopus, T5_FISH_SALTWATER_ALL_RARE
Deepwater Kraken, T7_FISH_SALTWATER_ALL_RARE
Whitefog Snapper, T3_FISH_FRESHWATER_AVALON_RARE
Clearhaze Snapper, T5_FISH_FRESHWATER_AVALON_RARE
Puremist Snapper, T7_FISH_FRESHWATER_AVALON_RARE
Carefree Leyfin, T3_FISH_FRESHWATER_DRAGON_AREA_RARE
Brightsoul Leyfin, T5_FISH_FRESHWATER_DRAGON_AREA_RARE
Dragonbound Leyfin, T7_FISH_FRESHWATER_DRAGON_AREA_RARE

# FOODS
Grilled Fish, T1_MEAL_GRILLEDFISH
Seaweed Salad, T1_MEAL_SEAWEEDSALAD
Carrot Soup, T1_MEAL_SOUP
Wheat Soup, T3_MEAL_SOUP
Cabbage Soup, T5_MEAL_SOUP
Greenmoor Clam Soup, T1_MEAL_SOUP_FISH
Murkwater Clam Soup, T3_MEAL_SOUP_FISH
Blackbog Clam Soup, T5_MEAL_SOUP_FISH
Bean Salad, T2_MEAL_SALAD
Turnip Salad, T4_MEAL_SALAD
Potato Salad, T6_MEAL_SALAD
Shallowshore Squid Salad, T2_MEAL_SALAD_FISH
Midwater Octopus Salad, T4_MEAL_SALAD_FISH
Deepwater Kraken Salad, T6_MEAL_SALAD_FISH
Chicken Pie, T3_MEAL_PIE
Goose Pie, T5_MEAL_PIE
Pork Pie, T7_MEAL_PIE
Upland Coldeye Pie, T3_MEAL_PIE_FISH
Mountain Blindeye Pie, T5_MEAL_PIE_FISH
Frostpeak Deadeye Pie, T7_MEAL_PIE_FISH
Chicken Omelette, T3_MEAL_OMELETTE
Goose Omelette, T5_MEAL_OMELETTE
Pork Omelette, T7_MEAL_OMELETTE
Lowriver Crab Omelette, T3_MEAL_OMELETTE_FISH
Drybrook Crab Omelette, T5_MEAL_OMELETTE_FISH
Dusthole Crab Omelette, T7_MEAL_OMELETTE_FISH
Avalonian Chicken Omelette, T3_MEAL_OMELETTE_AVALON
Avalonian Goose Omelette, T5_MEAL_OMELETTE_AVALON
Avalonian Pork Omelette, T7_MEAL_OMELETTE_AVALON
Carefree Leyfin Omelette, T3_MEAL_OMELETTE_DRAGONAREA
Brightsoul Leyfin Omelette, T5_MEAL_OMELETTE_DRAGONAREA
Dragonbound Leyfin Omelette, T7_MEAL_OMELETTE_DRAGONAREA
Goat Stew, T4_MEAL_STEW
Mutton Stew, T6_MEAL_STEW
Beef Stew, T8_MEAL_STEW
Greenriver Eel Stew, T4_MEAL_STEW_FISH
Redspring Eel Stew, T6_MEAL_STEW_FISH
Deadwater Eel Stew, T8_MEAL_STEW_FISH
Avalonian Goat Stew, T4_MEAL_STEW_AVALON
Avalonian Mutton Stew, T6_MEAL_STEW_AVALON
Avalonian Beef Stew, T8_MEAL_STEW_AVALON
Goat Sandwich, T4_MEAL_SANDWICH
Mutton Sandwich, T6_MEAL_SANDWICH
Beef Sandwich, T8_MEAL_SANDWICH
Stonestream Lurcher Sandwich, T4_MEAL_SANDWICH_FISH
Rushwater Lurcher Sandwich, T6_MEAL_SANDWICH_FISH
Thunderfall Lurcher Sandwich, T8_MEAL_SANDWICH_FISH
Avalonian Goat Sandwich, T4_MEAL_SANDWICH_AVALON
Avalonian Mutton Sandwich, T6_MEAL_SANDWICH_AVALON
Avalonian Beef Sandwich, T8_MEAL_SANDWICH_AVALON
Roast Chicken, T3_MEAL_ROAST
Roast Goose, T5_MEAL_ROAST
Roast Pork, T7_MEAL_ROAST
Roasted Whitefog Snapper, T3_MEAL_ROAST_FISH
Roasted Clearhaze Snapper, T5_MEAL_ROAST_FISH
Roasted Puremist Snapper, T7_MEAL_ROAST_FISH
Drake Egg Biscuits, T8_MEAL_SPECIAL_FOOD_DRAKE_EGG

# SIPHONED
Siphoned Energy, UNIQUE_GVGTOKEN_GENERIC

# GATHERING TOOLS
Pickaxe, 2H_TOOL_PICK
Avalonian Pickaxe, 2H_TOOL_PICK_AVALON
Stone Hammer, 2H_TOOL_HAMMER
Avalonian Stone Hammer, 2H_TOOL_HAMMER_AVALON
Axe, 2H_TOOL_AXE
Avalonian Axe, 2H_TOOL_AXE_AVALON
Sickle, 2H_TOOL_SICKLE
Avalonian Sickle, 2H_TOOL_SICKLE_AVALON
Skinning Knife, 2H_TOOL_KNIFE
Avalonian Skinning Knife, 2H_TOOL_KNIFE_AVALON
Siege Hammer, 2H_TOOL_SIEGEHAMMER
Avalonian Siege Hammer, 2H_TOOL_SIEGEHAMMER_AVALON
Fishing Rod, 2H_TOOL_FISHINGROD
Avalonian Fishing Rod, 2H_TOOL_FISHINGROD_AVALON

# GATHERING GEAR
Harvester Cap, HEAD_GATHERER_FIBER
Harvester Garb, ARMOR_GATHERER_FIBER
Harvester Workboots, SHOES_GATHERER_FIBER
Harvester Backpack, BACKPACK_GATHERER_FIBER
Skinner Cap, HEAD_GATHERER_HIDE
Skinner Garb, ARMOR_GATHERER_HIDE
Skinner Workboots, SHOES_GATHERER_HIDE
Skinner Backpack, BACKPACK_GATHERER_HIDE
Miner Cap, HEAD_GATHERER_ORE
Miner Garb, ARMOR_GATHERER_ORE
Miner Workboots, SHOES_GATHERER_ORE
Miner Backpack, BACKPACK_GATHERER_ORE
Quarrier Cap, HEAD_GATHERER_ROCK
Quarrier Garb, ARMOR_GATHERER_ROCK
Quarrier Workboots, SHOES_GATHERER_ROCK
Quarrier Backpack, BACKPACK_GATHERER_ROCK
Lumberjack Cap, HEAD_GATHERER_WOOD
Lumberjack Garb, ARMOR_GATHERER_WOOD
Lumberjack Workboots, SHOES_GATHERER_WOOD
Lumberjack Backpack, BACKPACK_GATHERER_WOOD
Fisherman Cap, HEAD_GATHERER_FISH
Fisherman Garb, ARMOR_GATHERER_FISH
Fisherman Workboots, SHOES_GATHERER_FISH
Fisherman Backpack, BACKPACK_GATHERER_FISH

# GATHERING JOURNALS
Lumberjack's Journal, JOURNAL_WOOD_EMPTY
Stonecutter's Journal, JOURNAL_STONE_EMPTY
Prospector's Journal, JOURNAL_ORE_EMPTY
Cropper's Journal, JOURNAL_FIBER_EMPTY
Gamekeeper's Journal, JOURNAL_HIDE_EMPTY

# MAPS
Dungeon Map (Solo), RANDOM_DUNGEON_SOLO_TOKEN_1
Dungeon Map (Group), RANDOM_DUNGEON_TOKEN_1
Dungeon Map (Large Group), RANDOM_DUNGEON_ELITE_TOKEN_1
"""

# BEGIN EMBEDDED SPELLS
SPELLS_CSV = """
# English name, spell ID; optional third field 'id' means ID-only variant.
# Player weapon and armour abilities/passives, including gathering armour and shapeshifter forms.
# Includes recasts; excludes capes, mounts, consumables, tools, vanity, mobs, and internal effects.
# Name lookup prefers the initial cast; different icons remain ambiguous.
# Source: https://github.com/ao-data/ao-bin-dumps/tree/0be6a5e74f30fc1312118be3d017f3832f027cef
# Generated by scripts/update_spells.py from localization, spells, items, transformations.
Adapting Matter,SHAPE_Q_DAMAGE_AND_SHIELD
Adrenaline Boost,AXEBOOST
Adrenaline Driven Charity,PASSIVE_HEALPOWERCHANCE
Aegis of Energy,ENERGYSHIELD2
After Image,AFTER_IMAGE
After Image,AFTER_IMAGE_RETURN,id
Aftershock,LETHAL_CLEAVER
Aggression,PASSIVE_ARMOR_INCREASED_DAMAGE,id
Aggression,PASSIVE_INCREASED_DAMAGE
Aggressive Caster,PASSIVE_CASTINGSPEED_CHANCE_FIRESTAFF
Aggressive Caster,PASSIVE_CASTINGSPEED_CHANCE_FROSTSTAFF,id
Aggressive Rush,PASSIVE_SPELLPOWER_CHANCE_AXE
Aggressive Rush,PASSIVE_SPELLPOWER_CHANCE_DAGGER,id
Aggressive Rush,PASSIVE_SPELLPOWER_CHANCE_SPEAR,id
Air Compressor,PBAOE_PULL
Altered Beast,PASSIVE_SHAPESHIFT_ATTACK_BUFF
Ambush,AMBUSH
Anguished Soul,CURSED_WALL
Arcane Orb,ARCANEORB2
Arcane Protection,SHIELDFRIENDLY
Arctic Volley,FROST_TARGETED_CHANNEL
Area of Decay,AREAOFDECAY
Armor Piercer,ARMORPIERCER
Ascended,PASSIVE_HOLY_ASCENDED
Assassin Spirit,ASSASSINSPIRIT
Authority,PASSIVE_ARMOR_CCDURATION,id
Authority,PASSIVE_CCDURATION
Auto Fire,AUTOFIRE2
Auto-Attack Speed,PASSIVE_AASPEEDCHANCE_BOW
Auto-Attack Speed,PASSIVE_AASPEEDCHANCE_DAGGER,id
Auto-Attack Speed,PASSIVE_AASPEEDCHANCE_SPEAR,id
Avalanche,ICEROCK_EXPLODE
Avalonian Beam,AVALON_BEAM
Backhand Strike,BACKHAND_KNOCKBACK
Balanced Mind,PASSIVE_ARMOR_BALANCE,id
Balanced Mind,PASSIVE_BALANCE
Ballista Support Fire,ARTILLERY_COMMAND
Bane,PASSIVE_CURSE
Barbed Roots,ENT_CHANNEL_TREE
Battle Frenzy,BATTLEFRENZY
Battle Howl,SHRIEKMACE
Battle Rush,AXE_CHARGE
Bear Trap,BEARTRAP
Black Hole,BLACKHOLE
Blade Cyclone,SWORD_SPIN
Blazing Geyser,BLAZING_GEYSER
Blessed Aurora,BLESSED_MACES
Blind Spot,BLINDSPOT
Blink,BLINK
Block,BLOCK
Blood Bandit,AXETHROW
Blood Bandit,AXETHROW_SECOND,id
Blood Ritual,BLOOD_BLADE
Blood Ritual,BLOOD_BLADE_MULTI,id
Blood Ritual,BLOOD_BLADE_MULTI2,id
Bloodlust,BLOODLUST
Bloodthirsty Blade,BLOODTHIRSTYBLADE
Bloody Reap,SCYTHESWING
Boulder Crash,ROCK_ELEMENTAL_STONE_THROW
Brambleseed,BRAMBLESEED
Break Free,CLEANSE_DASH
Breakthrough,LANCE_CHARGE
Burn,PASSIVE_BURN
Burning Field,FIRESTAFFBOLT_AOE
Burning Momentum,WEAPON_SPRINT
Calmness,PASSIVE_ARMOR_CASTER_ARCANESTAFF
Calmness,PASSIVE_ARMOR_CASTER_NATURESTAFF,id
Caltrops,CALTROPS
Cartwheel,CARTWHEEL
Cataclysm,CURSEULTIMATE
Celestial Sphere,CELESTIAL_SPHERE
Chain Missile,ARCANE_CHAIN_MISSILE
Chain Slash,CHAINDASH
Charge,CLAYMORECHARGE
Circle of Inspiration,ENERGYFIELD
Circle of Life,CIRCLEOFLIFE
Cleanse,SELF_CLEANSE
Cleanse Heal,CLEANSEHEAL
Clinging Frost,PROTOTYPE_ICESHIELD
Combustion,HUMAN_TORCH
Concentration,PASSIVE_ARMOR_INCREASED_CASTSPEED,id
Concentration,PASSIVE_CASTSPEED
Concussive Combo,CONCUSSIVEBLOW_MULTI_1
Concussive Combo,CONCUSSIVEBLOW_MULTI_2,id
Concussive Combo,CONCUSSIVEBLOW_MULTI_3,id
Conjure Magic,WILD_MAGIC_ROTATION_LOCK
Corrupting Steel,TAINTED_STEEL
Counter,KNUCKLE_COUNTER
Courier,PASSIVE_MAXLOAD_SHOES
Create Opening,CREATE_OPENING
Create Opening,CREATE_OPENING_STRIKE,id
Crescent Slash,MIGHTYSWING
Cripple,LEGBREAKER
Crush Charge,CHARGE_IN
Crystal Cobra Transformation,SHAPESHIFT_CRYSTAL_COBRA
Crystalburst,CRYSTAL_COBRA_SPIT
Cursed Beam,CURSEDBEAM
Cursed Sickle,CURSEBLADE
Cursed Tar,CURSED_SPLAT
Dark Matter,DARKMATTER
Dark Sphere,ARCANE_METEOR
Dash,GROUNDDASH
Dawnbird Transformation,SHAPESHIFT_AVALONIAN_EAGLE
Deadly Chop,AXESMASH
Deadly Shot,DEADLYSHOT
Deadly Swipe,QDASH
Death Curse,DEATHCURSE2
Deathward Climax,DUAL_RAPIDFIRE
Deep Cuts,PASSIVE_BLEEDCHANCE
Deep Leap,MACELEAP
Defenseless Rush,GLASS_MOVESPEED
Defensive Slam,DEFENSIVESLAM
Deflecting Spin,DEFLECTINGSTANCE
Delayed Teleport,DELAYED_TELEPORT
Demon Arrow,HELL_ARROW
Desecrate,CURSENOVA
Desperate Prayer,HOLYDESPERATEPRAYER2
Devastating Combo,KNUCKLECOMBO
Disembowel,DISEMBOWEL
Disorienting Shriek,PROTOTYPE_DISORIENT
Displacement Immunity,DISRUPTIONIMMUNITY
Distortion,SHAPE_W_DAMAGE_AOE
Divine Engine,CROSSBOW_DIVINE_SHOT
Divine Intervention,DIVINE_JUMP
Divine Protection,HOLYSHIELD
Divine Protection,HOLYSHIELD_MULTI,id
Dodge,DODGE
Dragon Leap,DASHKICK
Dreadladen Fighting,PASSIVE_CCDURATION_CHANCE_HAMMER,id
Dreadladen Fighting,PASSIVE_CCDURATION_CHANCE_MACE
Dreadladen Fighting,PASSIVE_CCDURATION_CHANCE_QUARTERSTAFF,id
Dual Nature,DUAL_NATURE
Dynamic Defense,DYNAMIC_DEFENSE
Earth Crusher,GROWING_PUNCH
Earth Shatter,HAMMERWHIRLWIND2
Efficiency,PASSIVE_ARMOR_REDUCED_ENERGYCOST,id
Efficiency,PASSIVE_REDUCED_ENERGYCOST
Elbow Smash,SHOULDERTACKLE
Electric Discharge,ELECTRICSHOCK
Electric Field,STORMSHIELD
Elevated Nature,ROTTENVINES
Emergency Shield,EMERGENCY_SHIELD
Empowering Beam,EMPOWERBEAM
Enchanted Quiver,SPEEDARCHER_KITE
Enchanted Quiver,SPEEDARCHER_KITE_MULTI_DASH,id
End Transformation,SHAPESHIFT_HUMAN
End Transformation,SHAPESHIFT_WEREWOLF_TO_HUMAN,id
Energetic,PASSIVE_ENERGYCHANCE_ARCANESTAFF,id
Energetic,PASSIVE_ENERGYCHANCE_BOW
Energetic,PASSIVE_ENERGYCHANCE_CROSSBOW,id
Energetic,PASSIVE_ENERGYCHANCE_CURSEDSTAFF,id
Energetic,PASSIVE_ENERGYCHANCE_FIRESTAFF,id
Energetic,PASSIVE_ENERGYCHANCE_FROSTSTAFF,id
Energetic,PASSIVE_ENERGYCHANCE_HAMMER,id
Energetic,PASSIVE_ENERGYCHANCE_HOLYSTAFF,id
Energetic,PASSIVE_ENERGYCHANCE_MACE,id
Energetic,PASSIVE_ENERGYCHANCE_NATURESTAFF,id
Energetic,PASSIVE_ENERGYCHANCE_QUARTERSTAFF,id
Energetic Sprint,SPRINTEOT
Energizing Shield,ENERGY_BARRIER
Energy Emission,CASTBUBBLE
Energy Source,MANADRAIN
Enfeeble Aura,ENFEEBLEAURA
Enfeeble Blades,ENFEEBLEBLADES
Enigma Blade,ENIGMA_BLADE
Ensnare,DUAL_NATURE_FLIP
Ethereal Form,TRANSLUCENT
Ethereal Path,ETHERIAL_PATH
Evasive Jump,JUMP
Everlasting Spirit,LIFESAVIOR
Exploding Shot,EXPLODING_SHOT
Explosive Arrows,BURNINGARROWS
Explosive Bolt,BOLTSHOT
Explosive Mine,GROUNDMINE
Explosive Salvo,ACID_BOMB
Eye of the Storm,MACE_CRYSTAL_FRAGMENT_STORM
Eye of the Storm,MACE_CRYSTAL_FRAGMENT_STORM_MULTI1,id
Eye of the Storm,MACE_CRYSTAL_FRAGMENT_STORM_MULTI2,id
Falcon Smash,DIVEPUNCH_FALL,id
Falcon Smash,DIVEPUNCH_RISE
Fatal Blade,COMBATSTAFF_SLASH
Fatal Fury,PASSIVE_KNUCKLE_BRAWLER
Fatal Fury,PASSIVE_KNUCKLE_BRAWLER_SPEED,id
Fatigue-Proof,SLOWSHIELD
Fear Aura,FEAR_AURA
Fearless Strike,CLAYMORESLASH
Feral Bash,BEAR_GROUND_SMASH
Fire Artillery,FIREARTILLERY
Fire Bolt,FIRESTAFFBOLT2
Fire Wave,FIRECONE
Firebreath,HELMET_FIREBREATH
Fireflash Orb,FLAME_ORB
Fireflash Orb,FLAME_ORB_TELEPORT_EFFECT,id
Fisherman Skills,PASSIVE_HEAD_YIELD_FISH_T4,id
Fisherman Skills,PASSIVE_HEAD_YIELD_FISH_T5,id
Fisherman Skills,PASSIVE_HEAD_YIELD_FISH_T6,id
Fisherman Skills,PASSIVE_HEAD_YIELD_FISH_T7,id
Fisherman Skills,PASSIVE_HEAD_YIELD_FISH_T8,id
Fisherman Skills,PASSIVE_SHOES_YIELD_FISH_T4,id
Fisherman Skills,PASSIVE_SHOES_YIELD_FISH_T5,id
Fisherman Skills,PASSIVE_SHOES_YIELD_FISH_T6,id
Fisherman Skills,PASSIVE_SHOES_YIELD_FISH_T7,id
Fisherman Skills,PASSIVE_SHOES_YIELD_FISH_T8,id
Fisherman Skills,PASSIVE_YIELD_FISH_T4
Fisherman Skills,PASSIVE_YIELD_FISH_T5,id
Fisherman Skills,PASSIVE_YIELD_FISH_T6,id
Fisherman Skills,PASSIVE_YIELD_FISH_T7,id
Fisherman Skills,PASSIVE_YIELD_FISH_T8,id
Flame Blast,FIRESTAFFIGNITE2_SPREAD
Flame Pillar,FLAMEPILLAR
Flame Tornado,FLAMETORNADO
Flaming Phoenix,FIREPHOENIX
Flare,FLARE
Flash of Insight,ARMOR_CD_RESET
Flee,FLEE
Fleet Footwork,CROSSSTEP_ROUNDHOUSE
Flickershots,CRYSTALXBOW
Flickershots,CRYSTALXBOW_MULTI_1,id
Flickershots,CRYSTALXBOW_MULTI_2,id
Fling,SHOVEL
Flow,FLOWSHIELD
Focused Run,CHANNELED_RUN
Forbidden Stab,DEEPCUTS
Force Field,PBAOE_KNOCKBACK
Force of Nature,PRIMALSLAM
Force Shield,FORCESHIELD
Forceful Bolts,PASSIVE_KNOCKBACKCHANCE
Forceful Swing,QS_WHIRLWIND2
Forest of Spears,FORESTOFSPEARS
Fortify,DUAL_NATURE_FLOP
Frazzle,FRAZZLE2
Freezing Wind,FREEZINGWIND
Frenzied Slashes,PASSIVE_SHAPE_WEREWOLF
Frost,PASSIVE_FROST
Frost Beam,FROSTBEAM
Frost Bomb,FROSTBOMB_CASTSLOW
Frost Lance,FROST_LANCE
Frost Nova,FROSTNOVA
Frost Shield,FROSTSHIELD
Frost Shot,JUMPSHOT2
Frost Walk,FROSTWALK
Frostbite,FROST_BITE
Frozen Hell,GLACIALFIELD
Frozen Surge,SHATTER_Q
Furious,PASSIVE_SPELLPOWER_CASTER_CROSSBOW
Furious,PASSIVE_SPELLPOWER_CASTER_CURSEDSTAFF,id
Furious,PASSIVE_SPELLPOWER_CASTER_FIRESTAFF,id
Furious,PASSIVE_SPELLPOWER_CASTER_FROSTSTAFF,id
Fury,ENRAGE
Gale Dance,QSTAFF_COMBO
Generous Heal,GENEROUSHEAL
Ghost Strike,GHOSTSTRIKE
Giant,MAXHEALTHBUFF
Giant Smash,GIANTSTEPS_SMASH
Giant Steps,GIANTSTEPS
Glacial Obelisk,ICE_SCULPTURE
Glacial Obelisk,ICE_SCULPTURE_EXPLODE,id
Glacial Prison,FROZEN_CRYSTAL
Glide,PROTOTYPE_GLIDE
Grasp of the Undead,UNDEADHAND
Gravitas,ROOTFIELD
Gravitas,ROOTFIELD_EFFECT,id
Gravitational Collapse,IMPULSE_PUNCH
Ground Pound,RAM_CHARGE
Ground Shaker,GROUNDSHAKER
Groundbreaker,GROUNDBREAKER2
Growing Rage,GROWING_RAGE
Grudge,CURSEDHANDS_STACKUP
Guard Rune,GUARDRUNE
Hail,HAIL_MULTI_1
Hail,HAIL_MULTI_2,id
Hamstring,HAMSTRINGSWORD
Hard to Catch,PASSIVE_KNUCKLE_COMBOBREAKER
Harpoon,SKILLSHOT_PULL
Harvesting Skills,PASSIVE_HEAD_YIELD_FIBER_T4,id
Harvesting Skills,PASSIVE_HEAD_YIELD_FIBER_T5,id
Harvesting Skills,PASSIVE_HEAD_YIELD_FIBER_T6,id
Harvesting Skills,PASSIVE_HEAD_YIELD_FIBER_T7,id
Harvesting Skills,PASSIVE_HEAD_YIELD_FIBER_T8,id
Harvesting Skills,PASSIVE_SHOES_YIELD_FIBER_T4,id
Harvesting Skills,PASSIVE_SHOES_YIELD_FIBER_T5,id
Harvesting Skills,PASSIVE_SHOES_YIELD_FIBER_T6,id
Harvesting Skills,PASSIVE_SHOES_YIELD_FIBER_T7,id
Harvesting Skills,PASSIVE_SHOES_YIELD_FIBER_T8,id
Harvesting Skills,PASSIVE_YIELD_FIBER_T4
Harvesting Skills,PASSIVE_YIELD_FIBER_T5,id
Harvesting Skills,PASSIVE_YIELD_FIBER_T6,id
Harvesting Skills,PASSIVE_YIELD_FIBER_T7,id
Harvesting Skills,PASSIVE_YIELD_FIBER_T8,id
Haste,HASTE
Haunting Screams,SKULLCURSE
Hellfire Barrage,IMP_BEAM
Hellfire Imp Transformation,SHAPESHIFT_IMP
Heroic Cleave,CLEAVE
Heroic Fighting,PASSIVE_HEROICSTACK
Heroic Strike,HEROICSTRIKE2
Hide Animal Poison,HOTSHIELD
Hit and Run,PASSIVE_MOVESPEED_CHANCE_CURSEDSTAFF
Hit and Run,PASSIVE_MOVESPEED_CHANCE_NATURESTAFF,id
Holy Beam,HEALINGBEAM
Holy Blessing,HOLYHOT
Holy Explosion,HOLYEXPLOSION
Holy Flash,HOLYFLASH
Holy Orb,HOLYORB
Holy Touch,HOLYTOUCH
Hover,HOVER_SPRINT
Howl,HOWL
Hundred Striking Fists,PUMMELING_STRIKES
Hurricane,QSWHIRLWIND
Hush,PASSIVE_SILENCECHANCE
Hush,WEAPON_SILENCE
Hyper Focus,HYPER_FOCUS
Hyperstatic,CRYSTALWAVE
Ice Block,ICEBLOCK2
Ice Crystal,FROST_ULTIMATE
Ice Shard,ICESHARD
Ice Storm,ICESTORM2
Immortal,IMMORTAL
Impaler,GROUNDSPEAR
Increased Defense,PASSIVE_ARMORCHANCE_AXE
Increased Defense,PASSIVE_ARMORCHANCE_SWORD,id
Inertia Ring,TAR_RING
Infected Scrapes,PASSIVE_SHAPE_PANTHER
Infernal Boulder,BOULDER_TOSS
Inferno Shield,FLAMESHIELD
Innate Power,PASSIVE_SHAPESHIFT_GATHER_CHARGES
Inner Corruption,INNER_CORRUPTION
Inner Focus,CHARGINGBLADE
Inner Shadow,DYNAMIC_CURSE
Internal Bleeding,INNERBLEEDING
Interrupt,INTERRUPT2
Intimidating Presence,PASSIVE_SHAPESHIFT_Q_CAST_DAMAGE_REDUCE
Iron Breaker,IRONBREAKER
Iron Will,DEFENSERUN
Judgment,AVALON_EAGLE_AIR_STRIKE
Knockback Shot,KNOCKBACKSHOT2
Knockout,KNOCKOUT
Levitate,LEVITATE
Life Leech,PASSIVE_HEALTHCHANCE_AXE
Life Leech,PASSIVE_HEALTHCHANCE_DAGGER,id
Life Leech,PASSIVE_HEALTHCHANCE_HAMMER,id
Life Leech,PASSIVE_HEALTHCHANCE_MACE,id
Life Leech,PASSIVE_HEALTHCHANCE_QUARTERSTAFF,id
Life Leech,PASSIVE_HEALTHCHANCE_SPEAR,id
Life Steal Aura,LIFESTEALAURA
Light Spark,PASSIVE_SHAPE_EAGLE
Limitbreaker,SWORD_BERSERK_RUN_RE
Lingering Power,PASSIVE_ATTACKBUFF_ARCANESTAFF
Living Armor,BRIEROFLIFE
Lucent Hawk,HAWK_SHOT_MULTI1
Lucent Hawk,HAWK_SHOT_MULTI2,id
Lucent Hawk,HAWK_SHOT_MULTI3,id
Lucent Hawk,HAWK_SHOT_MULTI4,id
Lumberjack Skills,PASSIVE_HEAD_YIELD_WOOD_T4,id
Lumberjack Skills,PASSIVE_HEAD_YIELD_WOOD_T5,id
Lumberjack Skills,PASSIVE_HEAD_YIELD_WOOD_T6,id
Lumberjack Skills,PASSIVE_HEAD_YIELD_WOOD_T7,id
Lumberjack Skills,PASSIVE_HEAD_YIELD_WOOD_T8,id
Lumberjack Skills,PASSIVE_SHOES_YIELD_WOOD_T4,id
Lumberjack Skills,PASSIVE_SHOES_YIELD_WOOD_T5,id
Lumberjack Skills,PASSIVE_SHOES_YIELD_WOOD_T6,id
Lumberjack Skills,PASSIVE_SHOES_YIELD_WOOD_T7,id
Lumberjack Skills,PASSIVE_SHOES_YIELD_WOOD_T8,id
Lumberjack Skills,PASSIVE_YIELD_WOOD_T4
Lumberjack Skills,PASSIVE_YIELD_WOOD_T5,id
Lumberjack Skills,PASSIVE_YIELD_WOOD_T6,id
Lumberjack Skills,PASSIVE_YIELD_WOOD_T7,id
Lumberjack Skills,PASSIVE_YIELD_WOOD_T8,id
Lunging Stabs,RAPIERSTAB
Lunging Strike,SPEAR_LUNGE
Magic Arrow,SKILLSHOT_STUN
Magic Force,PASSIVE_KNOCKBACK_CASTER_HOLYSTAFF
Magic Pollen,MAGICMUSHROOM
Magic Rune,MAGICCIRCLE
Magic Shock,MAGICSHOCK
Magma Sphere,MAGMASPHERE
Majestic Smash,MAJESTIC_SMASH
Mark of Sacrifice,DEATHMARK
Meditation,SUMMONER_CD_REDUCTION
Melting Point,PASSIVE_SHAPE_IMP
Mend Wounds,OUTOFCOMBATHEAL
Merciless Finish,BACK_SLASH
Meteor,METEOR
Mighty Blow,MIGHTYBLOW
Mimic,MIMIC
Mining Skills,PASSIVE_HEAD_YIELD_ORE_T4,id
Mining Skills,PASSIVE_HEAD_YIELD_ORE_T5,id
Mining Skills,PASSIVE_HEAD_YIELD_ORE_T6,id
Mining Skills,PASSIVE_HEAD_YIELD_ORE_T7,id
Mining Skills,PASSIVE_HEAD_YIELD_ORE_T8,id
Mining Skills,PASSIVE_SHOES_YIELD_ORE_T4,id
Mining Skills,PASSIVE_SHOES_YIELD_ORE_T5,id
Mining Skills,PASSIVE_SHOES_YIELD_ORE_T6,id
Mining Skills,PASSIVE_SHOES_YIELD_ORE_T7,id
Mining Skills,PASSIVE_SHOES_YIELD_ORE_T8,id
Mining Skills,PASSIVE_YIELD_ORE_T4
Mining Skills,PASSIVE_YIELD_ORE_T5,id
Mining Skills,PASSIVE_YIELD_ORE_T6,id
Mining Skills,PASSIVE_YIELD_ORE_T7,id
Mining Skills,PASSIVE_YIELD_ORE_T8,id
Mist Cloud,MIST_WALKER
Morgana Raven,SHOCKWAVE
Mortal Agony,SMELLOFBLOOD
Motivating Cleanse,CLEANSESPEED2
Motivating Pain,PAINSPRINT
Motivating Worker's Song,CC_IMMUNITY
Multishot,MULTISHOT2
Mystic Rocks,QS_SLOWROPE
Mythical Web,ARMOR_WEB
Nasty Wounds,NASTY_WOUNDS
Neurotoxin,PASSIVE_SHAPE_CRYSTAL_COBRA
Noise Eraser,SILENCINGBOLT
Obsessive Burst,SPELLRUSH
Onslaught,SPINNING_SMASH
Parry Strike,PARRY
Perpetual Energy,PERPETUALENERGY
Phantom Slash,QS_CRYSTAL_COMBO_MULTI3
Phantom Twin,QS_CRYSTAL_COMBO
Phantom Twin,QS_CRYSTAL_COMBO_MULTI2,id
Piercing Arrows,PASSIVE_ARMOR_PIERCE_STACK
Piercing Light,AVALON_EAGLE_LASER
Plaguebringer,CURSE_SKELETON_BARF_FDHR
Poisoned Arrow,POISONARROW
Polymorph,SHAPE_W_POLYMORPH
Position Swap,SWAP
Positional Drift,SHAPE_W_AREA_PULL
Pounce,PANTHER_POUNCE
Power Geyser,GEYSER
Powerful Impact,PASSIVE_SHAPE_ROCK_ELEMENTAL
Powerful Swing,HAMMER_SHOVE
Premonition,CC_BLOCK
Protection of Nature,NATURERESILIENCE
Protection of the Fiends,REFLECTAREA
Protective Beam,INVULNERABILITY
Protective Instinct,PASSIVE_PLATEARMOR_THREATGENERATION
Pulse Shock,SHAPE_Q_CONE_MELEE
Purge,PURGE_HELMET
Purging Shield,PURGINGSHIELD
Purging Shield,PURGINGSHIELD2,id
Purifying Combination,TRIPLECOMBO_DIVEKICK
Purifying Combination,TRIPLECOMBO_POWERPUNCH,id
Purifying Smoke,PURIFYING_SMOKE
Pyroblast,PYROBLAST_SKILLSHOT
Quarrying Skills,PASSIVE_HEAD_YIELD_ROCK_T4,id
Quarrying Skills,PASSIVE_HEAD_YIELD_ROCK_T5,id
Quarrying Skills,PASSIVE_HEAD_YIELD_ROCK_T6,id
Quarrying Skills,PASSIVE_HEAD_YIELD_ROCK_T7,id
Quarrying Skills,PASSIVE_HEAD_YIELD_ROCK_T8,id
Quarrying Skills,PASSIVE_SHOES_YIELD_ROCK_T4,id
Quarrying Skills,PASSIVE_SHOES_YIELD_ROCK_T5,id
Quarrying Skills,PASSIVE_SHOES_YIELD_ROCK_T6,id
Quarrying Skills,PASSIVE_SHOES_YIELD_ROCK_T7,id
Quarrying Skills,PASSIVE_SHOES_YIELD_ROCK_T8,id
Quarrying Skills,PASSIVE_YIELD_ROCK_T4
Quarrying Skills,PASSIVE_YIELD_ROCK_T5,id
Quarrying Skills,PASSIVE_YIELD_ROCK_T6,id
Quarrying Skills,PASSIVE_YIELD_ROCK_T7,id
Quarrying Skills,PASSIVE_YIELD_ROCK_T8,id
Quick Thinker,PASSIVE_ARMOR_CD_REDUCTION,id
Quick Thinker,PASSIVE_CD_REDUCTION
Rage,PASSIVE_KNUCKLE_RAGE
Raging Blades,BLADE_AURA
Raging Blink,DMG_BLINK
Raging Blink,DMG_BLINK_MULTI2,id
Raging Flare,SKILLSHOT_FIREBALL
Raging Storm,LIGHTNING_ARROW
Rain of Arrows,ARROWRAIN
Ray of Light,GROUNDARROW
Razor Cut,DUALAXE_CRAWLER
Razor's Edge,SPEAR_AOE_FINISHER
Reality Fissure,SHAPE_Q_SKILLSHOT
Reawaken,RESURRECTION
Reckless Charge,DASHDMG
Refreshing Sprint,SPRINT_CD_REDUCTION
Rejuvenating Breeze,REJUVENATING_BREEZE
Rejuvenating Flower,REJUVMUSHROOM_GRENADE
Rejuvenating Sprint,SPRINTHOT
Rejuvenation,REJUVENATION
Relentless Assault,CRYSTAL_SCYTHE_DASH_ZONE
Relentless Reap,CRYSTAL_SCYTHE_DASH_MULTI
Rending Rage,RENDINGCOMBO
Rending Rage,RENDINGCOMBO_MULTI2,id
Rending Rage,RENDINGCOMBO_MULTI3,id
Rending Spin,RENDINGSPIN
Rending Strike,RENDINGSTRIKE
Requite,REFLECT_CHANNEL
Retaliate,RETALIATE2
Revitalize,REANIMATE
Ring of Death,CRYSTAL_DAGGER_BLADE_RING
Ring of Death,CRYSTAL_DAGGER_BLADE_RING_RECALL,id
Rip Through,WEREWOLF_DASH
Rising Blow,LAUNCHER
Rooting Smash,HALBERDSMASH
Rotten Fish,THROWINGFISH
Rotten Ground,DEMONWALK
Royal Banner,ROYAL_BANNER
Royal March,ROYAL_MARCH
Rule Bender,PASSIVE_SHAPESHIFT_W_CAST_SPEED_BUFF
Runestone Golem Transformation,SHAPESHIFT_ROCK_ELEMENTAL
Rush,OVERSPRINT
Rushdown,PASSIVE_KNUCKLE_RUSHDOWN
Sacred Ground,SACRED_GROUND
Sacred Pulse,PULSINGHEAL
Sacrifice,SACRIFICE_HEAL
Salvation,HOLY_ULTIMATE
Sanctify,HOLY_DISPEL
Scent of the Wilderness,FLEE_MOB
Searing Flame,SEARING_FLAME
Seedling's Bloom,ENT_HEAL_AREA
Seismic Tremor,HAMMER_TREMOR
Self Ignition,BURNAURA
Separator,SEPARATING_SLAM
Serpent's Gaze,CRYSTAL_COBRA_PETRIFY
Shadow Edge,SKILLSHOT_TELEPORT
Shadow Panther Transformation,SHAPESHIFT_PANTHER
Shield Charge,CHARGE_SHIELD
Shockwave,SHOCKWAVE_PUNCH
Shrinking Curse,SHRINKINGSMASH
Sinister Swipes,PANTHER_CLAWS
Skinning Skills,PASSIVE_HEAD_YIELD_HIDE_T4,id
Skinning Skills,PASSIVE_HEAD_YIELD_HIDE_T5,id
Skinning Skills,PASSIVE_HEAD_YIELD_HIDE_T6,id
Skinning Skills,PASSIVE_HEAD_YIELD_HIDE_T7,id
Skinning Skills,PASSIVE_HEAD_YIELD_HIDE_T8,id
Skinning Skills,PASSIVE_SHOES_YIELD_HIDE_T4,id
Skinning Skills,PASSIVE_SHOES_YIELD_HIDE_T5,id
Skinning Skills,PASSIVE_SHOES_YIELD_HIDE_T6,id
Skinning Skills,PASSIVE_SHOES_YIELD_HIDE_T7,id
Skinning Skills,PASSIVE_SHOES_YIELD_HIDE_T8,id
Skinning Skills,PASSIVE_YIELD_HIDE_T4
Skinning Skills,PASSIVE_YIELD_HIDE_T5,id
Skinning Skills,PASSIVE_YIELD_HIDE_T6,id
Skinning Skills,PASSIVE_YIELD_HIDE_T7,id
Skinning Skills,PASSIVE_YIELD_HIDE_T8,id
Sky Fall,PROTOTYPE_SKYFALL
Sky's Fury,AIR_RAID
Slit Throat,EXECUTEDAGGER
Slow Poison,PASSIVE_SLOWPOISON
Slowing Charge,CHARGESLOWAE
Smite,SMITE_AOE
Smokebomb,SMOKEBOMB
Snare Charge,CHARGE_ROOT
Snipe Shot,SNIPESHOT_CROSSBOW
Soaring Swipe,DASH_KNOCKBACK
Soul Chain,ARMORCHAIN
Soul Link,SOUL_LINK
Soul Link,SOUL_LINK_MULTISPELL2,id
Soul Shaker,SOULSHAKER
Soul Shaker,SOULSHAKER_MULTI2,id
Soulless Stream,BLADE_AREA
Spear Throw,SPEARTHROW
Spectral Run,INVISIBLE_WALK
Spectral Trident,TRIDENTTHROW
Speed Caster,SPEEDCASTER
Speed Shot,SPEEDSHOT2
Speed Shot,SPEEDSHOT2_MULTI,id
Spider's Thread,SPIDER_THREAD
Spinning Blades,SPINATTACK
Spirit Animal,SPIRITANIMAL
Spirit Bear Transformation,SHAPESHIFT_BEAR
Spirit Crush,PASSIVE_PLATEARMOR_HEALTH_REDUCTION
Spirit of Vengeance,ROOTSHIELD
Spirit Spear,SPIRITSPEAR
Spiritual Seed,NATURE_ULTIMATE_SINGLE
Spiritual Seed,NATURE_ULTIMATE_SINGLE_MULTI2,id
Spiritual Seed,NATURE_ULTIMATE_SINGLE_MULTI3,id
Splash Wave,SPLASHWAVE
Splitting Slash,SPLITTINGSLASH
Spontaneous Combustion,FLAMEDASH
Spore Burst,PASSIVE_SHAPE_ENT
Sprint Shield,MOVEMENTSHIELD
Starfall,STARFALL_DOT
Stone Skin,STONESKIN
Stun Run,STUNRUN
Stunning Strike,PASSIVE_STUNCHANCE
Stunning Strike,PASSIVE_STUNCHANCE_QUARTERSTAFF,id
Sunder Armor,SUNDERARMOR2
Sunder Shot,SUNDERSHOT
Sweeping Bolt,CROSSBOW_ARMORPIERCER
Swift Cut,ASSASSIN_DASH
Swiftness,PASSIVE_ARMOR_INCREASED_AASPEED,id
Swiftness,PASSIVE_INCREASED_AASPEED
Swipes,PASSIVE_SHAPE_BEAR
Sylvian Transformation,SHAPESHIFT_ENT
Tackle,HAMMERTACKLE
Taunt,TAUNT
Tear Apart,RENDINGSWING
Tear Open,WEREWOLF_TEAR_APART
Tectonic Shift,ROCK_ELEMENTAL_SPLITTING_EARTH
Tectonic Slam,ROCK_ELEMENTAL_SPLITTING_EARTH_MULTI1
Tenacity,PASSIVE_ARMOR_INCREASED_CCR,id
Tenacity,PASSIVE_INCREASED_CCR
Tether Shift,SHAPE_W_TETHERBEAM
The Void,VOID
Thorn Growth,THORNSAREA
Thorn Growth,THORNSAREA_MULTI,id
Thorn Growth,THORNSAREA_MULTI2,id
Threatening Smash,THREATENINGSMASH
Threatening Strike,THREATENINGSTRIKE_HAMMER
Throwing Blades,THROWINGBLADES
Time Corridor,ARCANECORRIDOR
Time Freeze,TIME_FREEZE
Tornado,TORNADO
Toughness,PASSIVE_ARMOR_MR_AR,id
Toughness,PASSIVE_MR_AR
Tree Trunks,TREETRUNKS
Triple Kick,TRIPLE_KICK
Undead Arrows,UNDEADARROWS
Unstable Projectile,SHAPE_Q_CAST
Unstoppable Rush,CONEPUNCH2
Unstoppable Rush,CONEPUNCH2_DASH2,id
Vault Leap,VAULT_ATTACK
Vendetta,VACUUMSLASH
Vengeful Sprint,BERSERK_SPRINT
Vicious Barrage,CROSSBOW_CONE_ULTIMATE
Vile Curse,CURSEDOT
Wall of Flames,FIREWALL
Wanderlust,WANDERLUST
Water Shield,WATERSHIELD
Weakening,PASSIVE_REDUCE_DMG_SWORD
Well Of Life,WELLOFLIFE2
Well-Prepared,PASSIVE_CD_RESET_Q
Werewolf Transformation,SHAPESHIFT_WEREWOLF
Whirling Strikes,WHIRLING_STAFF
Whirlwind,AXEWHIRLWIND2
Wild Magic,WILD_MAGIC
Wild Onslaught,BEAR_DASH_THROUGH
Wild Onslaught,BEAR_ROAR,id
Wind Shield,WINDSHIELD
Wind Wall,WINDWALL
Wings of Fire,IMMUNEAREA
"""
# END EMBEDDED SPELLS


if __name__ == "__main__":
    raise SystemExit(main())
