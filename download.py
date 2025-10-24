#!/usr/bin/env python3
"""
download.py — Albion Online item icon downloader with embedded name → identifier data.

Usage:
    python3 download.py [downloads.txt] [Downloads]

Inputs:
- downloads.txt lines (CSV): Name, tier[, enchant[, quality]]
  * If the embedded identifier already begins with T1_…T8_, the tier may be omitted.
  * enchantment defaults to 0 (unenchanted)
  * quality defaults to 1 (common)

Examples:
Guardian Helmet, 6
Cleric Robe, 6, 1, 4
Transport Mammoth        # (tier omitted; embedded id is already T8_…)
"""

import csv
import os
import re
import sys
from io import StringIO
from typing import Dict, List, Tuple

import requests

BASE_URL = "https://render.albiononline.com/v1/item/"
DEFAULT_INPUT = "downloads.txt"
DEFAULT_OUTPUT_DIR = "downloads"
TIMEOUT = 15.0

QUALITY_WORD = {
    1: "Common",  # not appended in filename
    2: "Good",
    3: "Outstanding",
    4: "Excellent",
    5: "Masterpiece",
}

_NAME_SPACE_RX = re.compile(r"\s+")
_TIER_PREFIX_RX = re.compile(r"^T([1-8])_")

def norm_name(name: str) -> str:
    """Normalize for mapping lookup (case/space-insensitive)."""
    return _NAME_SPACE_RX.sub(" ", name.strip()).casefold()

def safe_file_stem(text: str) -> str:
    """Filename-safe (leave extension handling to caller)."""
    return text.replace("/", "-").replace("\\", "-")

def parse_embedded_items(csv_text: str) -> Dict[str, str]:
    """
    Parse embedded CSV (Name, IDENT) into a dict of normalized name → identifier.
    Lines starting with '#' or blank lines are ignored.
    """
    mapping: Dict[str, str] = {}
    cleaned: List[str] = []
    for raw in csv_text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        cleaned.append(line)
    if not cleaned:
        return mapping
    rdr = csv.reader(StringIO("\n".join(cleaned)))
    for row in rdr:
        if not row:
            continue
        if len(row) < 2:
            continue
        name = row[0].strip()
        ident = row[1].strip()
        if name and ident:
            mapping[norm_name(name)] = ident
    return mapping

def parse_requests_file(path: str) -> List[Tuple[str, int, int, int, str]]:
    """
    Read the downloads file and return a list of tuples:
      (name, tier, enchant, quality, original_line)

    Notes:
    - If the embedded identifier for a name already has a T1_…T8_ prefix,
      the tier in the file may be omitted. In that case we pass tier=-1
      as a sentinel and resolve it later once we know the identifier.
    - Ignores blank lines and lines starting with '#'.
    """
    reqs: List[Tuple[str, int, int, int, str]] = []
    if not os.path.exists(path):
        print(f"[ERROR] Input file not found: {path}")
        return reqs

    with open(path, "r", encoding="utf-8", errors="ignore", newline="") as f:
        rdr = csv.reader(f)
        for raw_line in rdr:
            if not raw_line:
                continue
            if len(raw_line) == 1 and raw_line[0].lstrip().startswith("#"):
                continue

            cols = [c.strip() for c in raw_line]
            if not cols or not cols[0]:
                continue

            name = cols[0]
            original_line = ", ".join(cols)

            # tier: allow missing (set to -1 sentinel)
            tier = -1
            if len(cols) >= 2 and cols[1] != "":
                try:
                    tier = int(cols[1])
                except ValueError:
                    print(f"[WARN] Bad tier for '{name}': {cols[1]!r}; treating as omitted.")
                    tier = -1

            enchant = 0
            if len(cols) >= 3 and cols[2] != "":
                try:
                    enchant = int(cols[2])
                except ValueError:
                    print(f"[WARN] Bad enchantment for '{name}': {cols[2]!r}; defaulting to 0.")

            quality = 1
            if len(cols) >= 4 and cols[3] != "":
                try:
                    quality = int(cols[3])
                except ValueError:
                    print(f"[WARN] Bad quality for '{name}': {cols[3]!r}; defaulting to 1.")

            if tier != -1 and not (1 <= tier <= 8):
                print(f"[WARN] Invalid tier {tier} for '{name}' (must be 1–8); skipping.")
                continue
            if not (0 <= enchant <= 4):
                print(f"[WARN] Invalid enchantment {enchant} for '{name}' (must be 0–4); skipping.")
                continue
            if not (1 <= quality <= 5):
                print(f"[WARN] Invalid quality {quality} for '{name}' (must be 1–5); skipping.")
                continue

            reqs.append((name, tier, enchant, quality, original_line))

    return reqs

def has_tier_prefix(ident: str) -> bool:
    return _TIER_PREFIX_RX.match(ident) is not None

def extract_tier_from_ident(ident: str) -> int:
    m = _TIER_PREFIX_RX.match(ident)
    return int(m.group(1)) if m else -1

def build_identifier(base_ident: str, tier: int, enchant: int) -> Tuple[str, int]:
    """
    Returns (identifier_for_url, tier_for_filename).
    - If base_ident already has T1_…T8_, do not add another prefix.
    - If not, require a valid tier (1–8).
    """
    if has_tier_prefix(base_ident):
        tier_for_name = extract_tier_from_ident(base_ident)
        core = base_ident
    else:
        if tier == -1:
            raise ValueError("Tier is required for items without a T1_…T8_ identifier.")
        tier_for_name = tier
        core = f"T{tier}_{base_ident}"
    ident = core if enchant == 0 else f"{core}@{enchant}"
    return ident, tier_for_name

def build_url(identifier: str, quality: int) -> str:
    return (
        f"{BASE_URL}{identifier}.png"
        if quality == 1
        else f"{BASE_URL}{identifier}.png?quality={quality}"
    )

def build_filename(name: str, tier: int, enchant: int, quality: int) -> str:
    """
    Example:
      Guardian Helmet 6.png
      Cleric Robe 6.1 Excellent.png
    """
    stem = f"{safe_file_stem(name)} {tier}"
    if enchant > 0:
        stem += f".{enchant}"
    if quality > 1:
        stem += f" {QUALITY_WORD.get(quality, str(quality))}"
    return stem + ".png"

def download_one(url: str, filepath: str) -> None:
    resp = requests.get(url, timeout=TIMEOUT)
    resp.raise_for_status()
    with open(filepath, "wb") as f:
        f.write(resp.content)

def main() -> None:
    # Args: script [input_file] [output_dir]
    input_path = sys.argv[1] if len(sys.argv) >= 2 else DEFAULT_INPUT
    output_dir = sys.argv[2] if len(sys.argv) >= 3 else DEFAULT_OUTPUT_DIR

    os.makedirs(output_dir, exist_ok=True)

    # Parse embedded name→identifier map
    name_to_ident = parse_embedded_items(ITEMS_CSV)
    if not name_to_ident:
        print("[ERROR] Embedded item mapping is empty.")
        sys.exit(1)

    # Read requests from input file
    entries = parse_requests_file(input_path)
    if not entries:
        print("[INFO] No valid download entries found.")
        return

    failed_lines: List[str] = []
    for (name, tier, enchant, quality, original_line) in entries:
        key = norm_name(name)
        if key not in name_to_ident:
            print(f"[FAIL] Not in data: '{name}' — leaving in file.")
            failed_lines.append(original_line)
            continue

        base_ident = name_to_ident[key]

        # If caller supplied a tier that conflicts with an embedded Tn_ prefix, warn but prefer embedded.
        if has_tier_prefix(base_ident):
            embedded_tier = extract_tier_from_ident(base_ident)
            if tier != -1 and tier != embedded_tier:
                print(f"[WARN] '{name}': supplied tier {tier} ignored; using embedded tier {embedded_tier}.")
            eff_tier = embedded_tier
        else:
            if tier == -1:
                print(f"[FAIL] '{name}': tier required (identifier has no Tn_ prefix).")
                failed_lines.append(original_line)
                continue
            eff_tier = tier

        try:
            ident, tier_for_filename = build_identifier(base_ident, tier, enchant)
            url = build_url(ident, quality)
            out_file = os.path.join(output_dir, build_filename(name, tier_for_filename, enchant, quality))

            download_one(url, out_file)
            print(f"[OK] {name} → {out_file}")
        except Exception as e:
            print(f"[FAIL] {name} ({base_ident}) : {e} — leaving in file.")
            failed_lines.append(original_line)

    # Rewrite input file with only the failed lines (unchanged text)
    try:
        with open(input_path, "w", encoding="utf-8", newline="") as f:
            for line in failed_lines:
                f.write(line.rstrip() + "\n")
        if failed_lines:
            print(f"\n[INFO] Kept {len(failed_lines)} failed line(s) in {input_path}.")
        else:
            print(f"\n[INFO] All items downloaded. {input_path} is now empty.")
    except Exception as e:
        print(f"[WARN] Could not rewrite '{input_path}': {e}")

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
Riding Horse, T3_MOUNT_HORSE
Armored Horse, T5_MOUNT_ARMORED_HORSE
Transport Ox, T3_MOUNT_OX
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

# SIPHONED
Siphoned Energy, UNIQUE_GVGTOKEN_GENERIC
"""

if __name__ == "__main__":
    main()
