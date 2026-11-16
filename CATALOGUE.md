# Albion Online icon catalogue

`catalogue.json` maps English names to Albion Online Render Service identifiers: **509 item families, 475 spell/passive icons and 4 item-name aliases**. The downloader loads this file directly.

The text files in `lists/` define which items and spells belong in the catalogue.

The catalogue includes the listed weapons, off-hands, armour, bags, capes, foods, potions, mounts, the Firewing Drake egg and tame animal, gathering equipment, tracking tools, siege hammers, maps, gathering journals and Siphoned Energy. Spells and passives are limited to weapons and head, chest and feet armour, including gathering armour.

## Items

```json
{"Guardian Armor":{"id":"ARMOR_PLATE_SET3","tiers":[4,5,6,7,8],"enchant":4,"quality":5}}
```

- `items` keys are English names without equipment tier prefixes.
- Comma-separated list names share one item family, with alternate names in `aliases`. Potion tiers select Minor, standard and Major variants.
- `id`: identifier stem; `tiers`: allowed tiers.
- `enchant`: maximum enchantment, starting at 0; `quality`: maximum quality, starting at 1.
- Optional `min_enchant` overrides the minimum enchantment.
- Optional `enchant_by_tier` replaces the enchantment range with allowed levels for specified tiers.
- Optional `id_by_enchant` replaces the identifier stem for specified enchantments. Override keys are strings.

Validate the options, select the overridden or default stem, add `T{tier}_`, and append `@{enchant}` when nonzero. Stems beginning `UNIQUE_` or `QUESTITEM_` omit the tier prefix.

Omitted tier selects the only available tier, if there is one. Omitted enchantment selects the lowest allowed level for that tier. Quality defaults to 1.

The five gathering journals use empty journal identifiers at tiers 2–8, with enchantment 0 and quality 1. Siphoned Energy uses `UNIQUE_GVGTOKEN_GENERIC` at tier 1, with enchantment 0 and quality 1.

URL: `https://render.albiononline.com/v1/item/{identifier}.png?quality={quality}`

## Spells and aliases

`spells` maps English names directly to spell identifiers. Parenthetical labels use proper stage names from English tooltips and official item qualifiers where available, preserving their capitalisation. The descriptive labels `second cast`, `dash`, `dive`, `teleport` and `passive` remain lowercase because the game supplies no distinct names for those variants. Grouped casts in the lists become separate entries for the main icon and each listed cast variant. Spell families are ordered alphabetically, with the base icon first and variants in curated list order. Spells have no tier, enchantment or quality options.

URL: `https://render.albiononline.com/v1/spell/{identifier}.png`

`aliases` maps alternative item names to canonical item keys. For lookup, ignore case and repeated whitespace; URL-encode identifiers.

Source: [ao-data/ao-bin-dumps, 8 September 2026](https://github.com/ao-data/ao-bin-dumps/tree/0be6a5e74f30fc1312118be3d017f3832f027cef). All 7,776 item tier/enchantment identifiers and 475 spell mappings checked against this source on 20 September 2026. Parenthetical names were also reviewed against its English tooltips and item names on that date.

### 16 November 2026 — Firewing Drake icons

The Firewing Drake family has three distinct item icons:

| Name | Render Service identifier | Catalogue status |
| --- | --- | --- |
| Firewing Drake Egg | `T8_FARM_DRAKE_BABY` | Added to `lists/miscellaneous.txt` and the catalogue |
| Tame Firewing Drake | `T8_FARM_DRAKE_GROWN` | Added to `lists/miscellaneous.txt` and the catalogue |
| Firewing Drake | `T8_MOUNT_DRAKE_FIRE` | Existing mount entry verified |

All three support only tier 8, enchantment 0 and quality 1. Tier may be omitted. The egg and tame animal are farming items; the mount's recipe consumes the tame animal. Drake Egg Biscuits remain a separate, existing food entry.

Names and item definitions were checked against `formatted/items.txt` and `items.xml` in [ao-data/ao-bin-dumps revision `0483dd97f1581dfaf02eed42f25f5ea2a3cb75e5`, 2 November 2026](https://github.com/ao-data/ao-bin-dumps/tree/0483dd97f1581dfaf02eed42f25f5ea2a3cb75e5). On 16 November 2026, all three identifiers returned HTTP 200 PNGs at 217 × 217 pixels from the Render Service with `quality=1`; visual inspection confirmed distinct egg, tame-animal and mount icons. This verification applies to these entries only, not the whole catalogue.

### 16 November 2026 — Waystone zero-enchantment requests

`Waystone (Large Group)` accepts enchantment 0 at tiers 6–8, including when enchantment is omitted. Zero requests `T{tier}_RANDOM_DUNGEON_ELITE_DRAGON_TOKEN_D1.png` without an `@` suffix and saves `Waystone (Large Group) {tier}.png`. Levels 1–4 retain their existing identifiers and filenames. The curated name lists are unchanged because this updates an existing family's accepted values.

This is an explicitly requested rendering option, not a verified unenchanted game item. Inspection of installed build `albiononline-macosx-full-1.32.040.346594` on 16 November found only levels 1–4 among tier-8 waystone definitions. Direct render checks that day returned the `D1@1` icon successfully, while `D1` without a suffix timed out and `D1@0` and the hypothetical legacy `_1` identifier returned HTTP 502. No successful zero-enchantment waystone render was verified; service errors remain normal download failures. The September verification above predates this exception.
