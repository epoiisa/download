# Albion Online icon catalogue

`catalogue.json` maps English names to Albion Online Render Service identifiers: **507 item families, 475 spell/passive icons and 4 item-name aliases**. The downloader loads this file directly.

The text files in `lists/` define which items and spells belong in the catalogue.

The catalogue includes the listed weapons, off-hands, armour, bags, capes, foods, potions, mounts, gathering equipment, tracking tools, siege hammers, maps, gathering journals and Siphoned Energy. Spells and passives are limited to weapons and head, chest and feet armour, including gathering armour.

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

`spells` maps English names directly to spell identifiers. Parenthetical labels distinguish different icons with the same name. Grouped casts in the lists become separate entries for the main icon and each listed cast variant. Spells have no tier, enchantment or quality options.

URL: `https://render.albiononline.com/v1/spell/{identifier}.png`

`aliases` maps alternative item names to canonical item keys. For lookup, ignore case and repeated whitespace; URL-encode identifiers.

Source: [ao-data/ao-bin-dumps, 8 September 2026](https://github.com/ao-data/ao-bin-dumps/tree/0be6a5e74f30fc1312118be3d017f3832f027cef). All 7,776 item tier/enchantment identifiers and 475 spell mappings checked against this source on 20 September 2026.
