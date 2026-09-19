#!/usr/bin/env python3
"""Rebuild weapon and armour spells from one ao-bin-dumps revision.

Usage: python3 scripts/update_spells.py /path/to/ao-bin-dumps --revision COMMIT_SHA
Requires localization.json, spells.json, items.json, and transformations.json.
"""

import argparse
import csv
import json
import re
from collections import defaultdict
from io import StringIO
from pathlib import Path


def as_list(value):
    return value if isinstance(value, list) else [value]


def read_json(directory, name):
    return json.loads((directory / (name + ".json")).read_text(encoding="utf-8"))


def is_player_weapon_or_armour(item):
    if item.get("@hidefromplayeroncontext") == "all":
        return False
    if not re.match(r"^T[1-8]_", item["@uniquename"]):
        return False
    category = item.get("@shopcategory")
    slot = item.get("@slottype")
    if category == "weapons" and slot == "mainhand":
        return True
    material_slots = {"head": "helmet", "armor": "armor", "shoes": "shoes"}
    if slot not in material_slots:
        return False
    if category == "gathering":
        return True
    return category in ("head", "armors", "shoes") and item.get("@shopsubcategory1") in {
        material + "_" + material_slots[slot] for material in ("cloth", "leather", "plate")
    }


def equipment_spell_ids(items, transformations):
    equipment = {
        item["@uniquename"]: item
        for kind in ("weapon", "equipmentitem", "transformationweapon")
        for item in as_list(items.get(kind, []))
    }

    def craft_spells(item, visited):
        identifier = item["@uniquename"]
        if identifier in visited:
            raise ValueError("Circular spell-list reference: " + identifier)
        spell_list = item.get("craftingspelllist", {})
        identifiers = set()
        if "@reference" in spell_list:
            identifiers.update(craft_spells(equipment[spell_list["@reference"]], visited | {identifier}))
        identifiers.difference_update(
            spell["@uniquename"] for spell in as_list(spell_list.get("removespell", []))
        )
        identifiers.update(
            spell["@uniquename"] for spell in as_list(spell_list.get("craftspell", []))
        )
        return identifiers

    equipped, forms = set(), set()
    for item in equipment.values():
        if is_player_weapon_or_armour(item):
            equipped.update(craft_spells(item, set()))
            if "@transformation" in item:
                forms.add(item["@transformation"])
    for form in as_list(transformations.get("transformation", [])):
        if form["@uniquename"] in forms:
            for group, key in (("spells", "spell"), ("passivespells", "passivespell")):
                equipped.update(
                    spell["@uniquename"] for spell in as_list(form.get(group, {}).get(key, []))
                )
    return equipped


def include_recast_spells(equipped, definitions):
    identifiers = set(equipped)
    pending = list(equipped)
    while pending:
        spell = definitions[pending.pop()]
        for recast in as_list(spell.get("multispell", [])):
            identifier = recast["@spell"]
            if identifier not in identifiers:
                identifiers.add(identifier)
                pending.append(identifier)
    return identifiers


def generate_catalog(directory, revision):
    translations = read_json(directory, "localization")["tmx"]["body"]["tu"]
    english = {}
    for translation in translations:
        for text in as_list(translation.get("tuv", [])):
            if text.get("@xml:lang") == "EN-US" and isinstance(text.get("seg"), str):
                english[translation["@tuid"]] = text["seg"].strip()

    source = read_json(directory, "spells")["spells"]
    definitions = {}
    for kind in ("activespell", "passivespell", "togglespell"):
        for spell in as_list(source[kind]):
            definitions[spell["@uniquename"]] = spell

    equipped = equipment_spell_ids(
        read_json(directory, "items")["items"],
        read_json(directory, "transformations")["transformations"],
    )
    spells = {}
    for identifier in include_recast_spells(equipped, definitions):
        spell = definitions[identifier]
        name = english.get(spell.get("@namelocatag", "@SPELLS_" + identifier))
        if not name:
            if identifier in equipped:
                raise ValueError("Missing English spell name: " + identifier)
            continue
        spells[identifier] = (name, spell)

    by_name = defaultdict(list)
    for identifier, (name, _) in spells.items():
        by_name[" ".join(name.split()).casefold()].append(identifier)

    name_lookup = set()
    for identifiers in by_name.values():
        primary = [identifier for identifier in identifiers if identifier in equipped]
        icons = defaultdict(list)
        for identifier in primary or identifiers:
            # Missing icon metadata is not evidence that two icons are identical.
            icon = spells[identifier][1].get("@uisprite") or identifier
            icons[icon].append(identifier)
        for variants in icons.values():
            name_lookup.add(min(variants, key=lambda identifier: (len(identifier), identifier)))

    output = StringIO()
    output.write("# English name, spell ID; optional third field 'id' means ID-only variant.\n")
    output.write("# Player weapon and armour abilities/passives, including gathering armour and shapeshifter forms.\n")
    output.write("# Includes recasts; excludes capes, mounts, consumables, tools, vanity, mobs, and internal effects.\n")
    output.write("# Name lookup prefers the initial cast; different icons remain ambiguous.\n")
    output.write(f"# Source: https://github.com/ao-data/ao-bin-dumps/tree/{revision}\n")
    output.write("# Generated by scripts/update_spells.py from localization, spells, items, transformations.\n")
    writer = csv.writer(output, lineterminator="\n")
    for identifier, (name, _) in sorted(spells.items(), key=lambda item: (item[1][0].casefold(), item[0])):
        row = [name, identifier]
        if identifier not in name_lookup:
            row.append("id")
        writer.writerow(row)
    return output.getvalue(), len(spells), len(by_name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_directory", type=Path)
    parser.add_argument("--revision", required=True)
    args = parser.parse_args()
    if len(args.revision) != 40 or any(char not in "0123456789abcdef" for char in args.revision):
        parser.error("--revision must be a full lowercase commit SHA")
    catalog, count, names = generate_catalog(args.source_directory, args.revision)
    if '"""' in catalog or "\\" in catalog:
        raise ValueError("Spell data needs escaping before embedding in a Python string")
    target = Path(__file__).resolve().parents[1] / "download.py"
    source = target.read_text(encoding="utf-8")
    start = source.index("# BEGIN EMBEDDED SPELLS")
    end = source.index("# END EMBEDDED SPELLS", start) + len("# END EMBEDDED SPELLS")
    replacement = '# BEGIN EMBEDDED SPELLS\nSPELLS_CSV = """\n' + catalog + '"""\n# END EMBEDDED SPELLS'
    target.write_text(source[:start] + replacement + source[end:], encoding="utf-8")
    print(f"Embedded {count} spell IDs covering {names} English names.")


if __name__ == "__main__":
    main()
