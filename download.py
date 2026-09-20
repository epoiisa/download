#!/usr/bin/env python3
"""
Albion Online item and spell icon downloader.

Examples:
    download
    download Hunter Shoes 8 1 4
    download downloads.txt
"""

from __future__ import annotations

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
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Sequence, Tuple

BASE_URL = "https://render.albiononline.com/v1/item/"
SPELL_BASE_URL = "https://render.albiononline.com/v1/spell/"
TIMEOUT = 15.0
DEFAULT_WORKERS = 6
CATALOGUE_PATH = Path(__file__).resolve().with_name("catalogue.json")

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
  download "Hush (Passive)"
  download "Lumberjack's Journal" 8
  download downloads.txt

Notes:
  download with no arguments reads one request per line, with no visible prompt
  exit, quit, or Ctrl+C leaves interactive mode
  end of input: Ctrl+D on macOS/Linux; Ctrl+Z then Enter on Windows
  names containing spaces do not need quotes
  at the shell, double-quote names containing apostrophes or parentheses
  interactive and file lines use: Name [tier [enchant [quality]]]
  PNGs are saved in the current directory; change directory to choose the output
  names and allowed item values come from catalogue.json beside download.py
  spell names and IDs in the catalogue are accepted
  use labels such as Hush (Passive) and Rending Rage (Second cast) for distinct icons
  tier may be omitted for items with only one available tier
  enchant defaults to the lowest available level (usually 0)
  quality defaults to 1
  successful file entries are removed from the file
  failed file entries stay in the file
  text files must be UTF-8 (with or without a BOM); LF and CRLF are accepted
  exit status: 0 for success, 1 for failures, 130 for interactive Ctrl+C
"""

_NAME_SPACE_RX = re.compile(r"\s+")
_INTEGER_RX = re.compile(r"[+-]?[0-9]+")
_IDENTIFIER_RX = re.compile(r"[A-Z0-9_]+")


@dataclass(frozen=True)
class ItemEntry:
    name: str
    identifier: str
    tiers: Tuple[int, ...]
    max_enchant: int
    max_quality: int
    min_enchant: int = 0
    enchant_by_tier: Mapping[int, Tuple[int, ...]] = field(default_factory=dict)
    id_by_enchant: Mapping[int, str] = field(default_factory=dict)

    def enchantments(self, tier: int) -> Tuple[int, ...]:
        return self.enchant_by_tier.get(tier, tuple(range(self.min_enchant, self.max_enchant + 1)))


@dataclass(frozen=True)
class Catalogue:
    items: Dict[str, ItemEntry]
    spell_names: Dict[str, str]
    spell_ids: Dict[str, str]


@dataclass(frozen=True)
class BatchRequest:
    name: str
    tier: int
    enchant: int | None
    quality: int
    original_line: str
    line_number: int


def print_usage() -> None:
    print(USAGE.rstrip())


def norm_name(name: str) -> str:
    return _NAME_SPACE_RX.sub(" ", name.strip()).casefold()


def safe_file_stem(text: str) -> str:
    return text.replace("/", "-").replace("\\", "-")


def unique_json_object(pairs: Sequence[Tuple[str, object]]) -> dict:
    result: dict = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate catalogue key: {key!r}.")
        result[key] = value
    return result


def catalog_integer(value: object, minimum: int, maximum: int, label: str) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer between {minimum} and {maximum}.")
    return value


def catalog_identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not _IDENTIFIER_RX.fullmatch(value):
        raise ValueError(f"{label} must be a nonempty Render Service identifier.")
    return value


def parse_item_entry(name: str, data: object) -> ItemEntry:
    required = {"id", "tiers", "enchant", "quality"}
    optional = {"min_enchant", "enchant_by_tier", "id_by_enchant"}
    if not isinstance(data, dict) or not required <= data.keys() <= required | optional:
        raise ValueError(f"Invalid catalogue fields for item '{name}'.")
    identifier = catalog_identifier(data["id"], f"Identifier for '{name}'")
    tiers = data["tiers"]
    if not isinstance(tiers, list) or not tiers:
        raise ValueError(f"'{name}' must have at least one tier.")
    tiers = tuple(catalog_integer(t, 1, 8, f"Tier for '{name}'") for t in tiers)
    if len(set(tiers)) != len(tiers):
        raise ValueError(f"Duplicate tiers for '{name}'.")
    maximum = catalog_integer(data["enchant"], 0, 4, f"Enchantment for '{name}'")
    minimum = catalog_integer(data.get("min_enchant", 0), 0, maximum, f"Minimum enchantment for '{name}'")
    quality = catalog_integer(data["quality"], 1, 5, f"Quality for '{name}'")
    enchant_by_tier: Dict[int, Tuple[int, ...]] = {}
    overrides = data.get("enchant_by_tier", {})
    if not isinstance(overrides, dict):
        raise ValueError(f"Invalid enchant_by_tier for '{name}'.")
    for tier, levels in overrides.items():
        if tier not in {str(t) for t in tiers} or not isinstance(levels, list) or not levels:
            raise ValueError(f"Invalid enchantment override for '{name}' tier {tier}.")
        values = tuple(catalog_integer(e, minimum, maximum, f"Enchantment for '{name}'") for e in levels)
        if len(set(values)) != len(values):
            raise ValueError(f"Duplicate enchantments for '{name}' tier {tier}.")
        enchant_by_tier[int(tier)] = tuple(sorted(values))
    id_by_enchant: Dict[int, str] = {}
    overrides = data.get("id_by_enchant", {})
    if not isinstance(overrides, dict):
        raise ValueError(f"Invalid id_by_enchant for '{name}'.")
    allowed = {str(e) for t in tiers for e in enchant_by_tier.get(t, range(minimum, maximum + 1))}
    for enchant, stem in overrides.items():
        if enchant not in allowed:
            raise ValueError(f"Invalid identifier override for '{name}' enchantment {enchant}.")
        id_by_enchant[int(enchant)] = catalog_identifier(stem, f"Identifier override for '{name}'")
    return ItemEntry(name, identifier, tuple(sorted(tiers)), maximum, quality, minimum,
                     enchant_by_tier, id_by_enchant)


@lru_cache(maxsize=1)
def load_catalogue(path: Path) -> Catalogue:
    with path.open(encoding="utf-8") as handle:
        data = json.load(handle, object_pairs_hook=unique_json_object)
    if not isinstance(data, dict) or set(data) != {"items", "spells", "aliases"}:
        raise ValueError("Catalogue must contain items, spells and aliases objects.")
    for section, entries in data.items():
        if not isinstance(entries, dict) or (section != "aliases" and not entries):
            raise ValueError(f"Invalid or empty catalogue section: {section}.")
        seen = set()
        for name in entries:
            if not name.strip() or norm_name(name) in seen:
                raise ValueError(f"Empty or duplicate name in {section}: {name!r}.")
            seen.add(norm_name(name))
    items = {norm_name(name): parse_item_entry(name, entry) for name, entry in data["items"].items()}
    for alias, canonical in data["aliases"].items():
        if not isinstance(canonical, str) or canonical not in data["items"]:
            raise ValueError(f"Unknown canonical item for alias '{alias}': {canonical!r}.")
        if norm_name(alias) in items:
            raise ValueError(f"Alias conflicts with an item name: '{alias}'.")
        items[norm_name(alias)] = items[norm_name(canonical)]
    names = {norm_name(name): catalog_identifier(ident, f"Spell '{name}'")
             for name, ident in data["spells"].items()}
    identifiers = {norm_name(ident): ident for ident in names.values()}
    return Catalogue(items, names, identifiers)


def load_item_catalog() -> Dict[str, ItemEntry]:
    return load_catalogue(CATALOGUE_PATH).items


def load_spell_catalog() -> Tuple[Dict[str, str], Dict[str, str]]:
    catalogue = load_catalogue(CATALOGUE_PATH)
    return catalogue.spell_names, catalogue.spell_ids


def resolve_spell(name: str) -> str:
    names, identifiers = load_spell_catalog()
    key = norm_name(name)
    identifier = identifiers.get(key)
    # An exact uppercase ID takes precedence over a coincident English name.
    if identifier == name.strip():
        return identifier
    if key in names:
        return names[key]
    if identifier:
        return identifier
    raise ValueError(f"'{name}' is not in the spell catalog.")


def is_unique_identifier(ident: str) -> bool:
    return ident.startswith(("UNIQUE_", "QUESTITEM_"))


def parse_int(value: str, field_name: str) -> int:
    if not _INTEGER_RX.fullmatch(value):
        raise ValueError(f"Invalid {field_name}: {value!r}.")
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Invalid {field_name}: {value!r}.") from exc


def validate_item_values(name: str, tier: int, enchant: int | None, quality: int) -> None:
    if not name.strip():
        raise ValueError("Item name cannot be empty.")
    if not (1 <= tier <= 8):
        raise ValueError(f"Invalid tier for '{name}': {tier}. Tier must be between 1 and 8.")
    if enchant is not None and not (0 <= enchant <= 4):
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


def parse_request(args: Sequence[str]) -> Tuple[str, int, int | None, int]:
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
        return name, -1, None, 1

    tier = parse_int(values[0], "tier")
    enchant = parse_int(values[1], "enchantment") if len(values) >= 2 else None
    quality = parse_int(values[2], "quality") if len(values) >= 3 else 1
    validate_item_values(name, tier, enchant, quality)
    return name, tier, enchant, quality


def build_url(identifier: str, quality: int) -> str:
    identifier = urllib.parse.quote(identifier, safe="@")
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
        # Windows chmod only changes the read-only flag. Keep the temporary
        # file writable so a rejected replacement can still be cleaned up.
        if os.name != "nt":
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

    with open(path, "r", encoding="utf-8-sig", newline="") as handle:
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
    catalog: Mapping[str, ItemEntry], name: str, tier: int, enchant: int | None, quality: int
) -> Tuple[str, str]:
    key = norm_name(name)
    if key not in catalog:
        raise ValueError(f"'{name}' is not in the item catalog.")

    entry = catalog[key]
    include_tier = tier != -1
    tiers = ", ".join(str(value) for value in entry.tiers)
    if tier == -1:
        if len(entry.tiers) != 1:
            raise ValueError(f"Tier is required for '{name}'. Available tiers: {tiers}.")
        tier = entry.tiers[0]
    if tier not in entry.tiers:
        raise ValueError(f"'{name}' is not available at tier {tier}. Available tiers: {tiers}.")
    levels = entry.enchantments(tier)
    if enchant is None:
        enchant = levels[0]
    if enchant not in levels:
        allowed = ", ".join(str(value) for value in levels)
        raise ValueError(f"'{name}' tier {tier} does not support enchantment {enchant}. Available enchantments: {allowed}.")
    if not 1 <= quality <= entry.max_quality:
        raise ValueError(f"'{name}' does not support quality {quality}. Available qualities: 1–{entry.max_quality}.")
    stem = entry.id_by_enchant.get(enchant, entry.identifier)
    fixed = is_unique_identifier(stem)
    identifier = stem if fixed else f"T{tier}_{stem}"
    if enchant:
        identifier += f"@{enchant}"
        include_tier = True
    filename = build_filename(name, tier, enchant, quality, include_tier=include_tier and not fixed)
    return identifier, filename


def download_item_to_directory(
    catalog: Mapping[str, ItemEntry], name: str, tier: int, enchant: int | None, quality: int, output_dir: str
) -> str:
    validate_item_values(name, tier, enchant, quality)
    return download_catalog_item_to_directory(catalog, name, tier, enchant, quality, output_dir)


def download_catalog_item_to_directory(
    catalog: Mapping[str, ItemEntry],
    name: str,
    tier: int,
    enchant: int | None,
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
        if os.name != "nt":
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


def run_batch_entry(
    catalog: Mapping[str, ItemEntry], output_dir: str, entry: BatchRequest
) -> Tuple[str, str]:
    name = entry.name
    tier = entry.tier
    enchant = entry.enchant
    quality = entry.quality
    if tier == -1 and norm_name(name) not in catalog:
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
        if norm_name(name) in catalog:
            try:
                output_path = download_catalog_item_to_directory(
                    catalog, name, -1, enchant, quality, output_dir
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
    # Windows pipes may use a legacy encoding. Report unencodable characters
    # as escapes instead of failing while printing a result or an error.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = list(sys.argv[1:] if argv is None else argv)
    if args in (["-h"], ["--help"]):
        print_usage()
        return 0

    try:
        catalog = load_item_catalog()
    except (OSError, UnicodeError, ValueError) as exc:
        print(f"[FAIL] Could not load catalogue: {exc}")
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


if __name__ == "__main__":
    raise SystemExit(main())
