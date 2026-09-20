#!/usr/bin/env python3
"""Update catalogue spells within the scope of lists/spells.txt.

Usage: python3 scripts/update_spells.py /path/to/ao-bin-dumps --revision COMMIT_SHA
Requires localization.json, spells.json, items.json, and transformations.json.
Preserves reviewed IDs and labels. Use --check to validate without writing.
"""

import argparse
import json
import re
from collections import defaultdict
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


def curated_spell_names(text):
    names = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        match = re.fullmatch(r"(.+) \(([^()]+)\)", line)
        if match and match[2].casefold() != "passive":
            expanded = [match[1]] + [f"{match[1]} ({cast.strip()})" for cast in match[2].split(",")]
        else:
            expanded = [line]
        for name in expanded:
            key = " ".join(name.split()).casefold()
            if key in names and names[key] != name:
                raise ValueError(f"Inconsistent spelling in spell list: {name!r} and {names[key]!r}.")
            names[key] = name
    if not names:
        raise ValueError("Curated spell list is empty.")
    return list(names.values())


def select_spells(names, previous, spells, equipped):
    selected = {}
    for name in names:
        base = re.sub(r" \([^()]+\)$", "", name)
        key = " ".join(base.split()).casefold()
        candidates = {identifier: spell for identifier, (english, spell) in spells.items()
                      if " ".join(english.split()).casefold() == key}
        if name in previous:
            identifier = previous[name]
            if identifier not in candidates:
                raise ValueError(f"Review '{name}': existing ID {identifier} no longer matches an eligible spell.")
            selected[name] = identifier
            continue
        if name != base:
            raise ValueError(f"Add a reviewed ID for labelled spell '{name}' to catalogue.json.")
        primary = {identifier: spell for identifier, spell in candidates.items() if identifier in equipped}
        icons = defaultdict(list)
        for identifier, spell in (primary or candidates).items():
            icons[spell.get("@uisprite") or identifier].append(identifier)
        if len(icons) != 1:
            choices = ", ".join(sorted(primary or candidates)) or "none"
            raise ValueError(f"Review '{name}': expected one icon; candidate IDs: {choices}.")
        selected[name] = min(next(iter(icons.values())), key=lambda identifier: (len(identifier), identifier))
    return dict(sorted(selected.items()))


def generate_catalog(directory, names, previous):
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
            continue
        spells[identifier] = (name, spell)

    return select_spells(names, previous, spells, equipped)


def format_catalogue(data):
    lines = ["{"]
    for index, (section, entries) in enumerate(data.items()):
        lines.append(f'  "{section}": {{')
        for row, (name, value) in enumerate(entries.items()):
            encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            comma = "," if row < len(entries) - 1 else ""
            lines.append(f"    {json.dumps(name, ensure_ascii=False)}: {encoded}{comma}")
        lines.append("  }" + ("," if index < len(data) - 1 else ""))
    return "\n".join(lines + ["}", ""])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_directory", type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--check", action="store_true", help="validate without changing the catalogue")
    args = parser.parse_args()
    if len(args.revision) != 40 or any(char not in "0123456789abcdef" for char in args.revision):
        parser.error("--revision must be a full lowercase commit SHA")
    root = Path(__file__).resolve().parents[1]
    target = root / "catalogue.json"
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        names = curated_spell_names((root / "lists" / "spells.txt").read_text(encoding="utf-8"))
        spells = generate_catalog(args.source_directory, names, data["spells"])
        if args.check:
            if data["spells"] != spells:
                parser.error("Catalogue spells differ from the curated list; run without --check to update.")
        else:
            data["spells"] = spells
            target.write_text(format_catalogue(data), encoding="utf-8")
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    action = "Checked" if args.check else "Updated"
    print(f"{action} {len(spells)} curated spell icons against ao-bin-dumps {args.revision}.")
    if not args.check:
        print("Record the source revision and verification date in CATALOGUE.md after reviewing the changes.")


if __name__ == "__main__":
    main()
