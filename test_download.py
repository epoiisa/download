import contextlib
import csv
import io
import os
import stat
import subprocess
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import download
from scripts.update_spells import equipment_spell_ids, include_recast_spells


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"png"


class DownloadTests(unittest.TestCase):
    def test_unquoted_item_writes_requested_filename_in_all_modes(self):
        line = "Hunter Shoes 8 1 4"
        for mode in ("command", "interactive", "file"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                requests = Path(directory) / "items.txt"
                if mode == "file":
                    requests.write_text(line + "\n", encoding="utf-8")
                args = {"command": line.split(), "interactive": [], "file": [str(requests)]}[mode]
                output = io.StringIO()
                with patch.object(download.os, "getcwd", return_value=directory), patch.object(
                    download.sys, "stdin", io.StringIO(line + "\nexit\n")
                ), patch.object(
                    download.urllib.request, "urlopen", return_value=FakeResponse()
                ) as fetch, contextlib.redirect_stdout(output):
                    status = download.main(args)

                target = Path(directory) / "Hunter Shoes 8.1 Excellent.png"
                self.assertEqual(status, 0)
                self.assertEqual(target.read_bytes(), b"png")
                fetch.assert_called_once_with(
                    "https://render.albiononline.com/v1/item/T8_SHOES_LEATHER_SET2@1.png?quality=4",
                    timeout=download.TIMEOUT,
                )
                if mode == "file":
                    self.assertEqual(requests.read_text(encoding="utf-8"), "")
                else:
                    self.assertEqual(output.getvalue(), f"[OK] Downloaded 'Hunter Shoes' to {target}\n")

    def test_unquoted_names_preserve_defaults_punctuation_and_spell_ids(self):
        catalog = download.load_item_catalog()
        for line, endpoint, filename in (
            ("Bow of Badon 8", "item/T8_2H_BOW_KEEPER", "Bow of Badon 8"),
            ("Guardian Armor 6 2", "item/T6_ARMOR_PLATE_SET3@2", "Guardian Armor 6.2"),
            ("Vendetta's Wrath 8", "item/T8_2H_FIRESTAFF", "Vendetta's Wrath 8"),
            ("Iron-clad Staff 6", "item/T6_2H_IRONCLADEDSTAFF", "Iron-clad Staff 6"),
            ("Siphoned Energy", "item/UNIQUE_GVGTOKEN_GENERIC", "Siphoned Energy"),
            ("Heroic Cleave", "spell/CLEAVE", "Heroic Cleave"),
            ("HEROICSTRIKE2", "spell/HEROICSTRIKE2", "HEROICSTRIKE2"),
        ):
            with self.subTest(line=line), patch.object(download, "download_one") as fetch:
                with contextlib.redirect_stdout(io.StringIO()):
                    status = download.run_single_download(catalog, line.split(), "/tmp")

                self.assertEqual(status, 0)
                fetch.assert_called_once_with(
                    "https://render.albiononline.com/v1/" + endpoint + ".png",
                    "/tmp/" + filename + ".png",
                )

    def test_invalid_unquoted_item_values_do_not_download(self):
        catalog = download.load_item_catalog()
        for line, error in (
            ("Hunter Shoes 99", "Invalid tier"),
            ("Hunter Shoes -1", "Invalid tier"),
            ("Hunter Shoes 8 5", "Invalid enchantment"),
            ("Hunter Shoes 8 one 4", "Invalid enchantment"),
            ("Hunter Shoes 8 1 0", "Invalid quality"),
            ("Hunter Shoes 8 1 4.0", "Invalid quality"),
            ("Hunter Shoes 8 1 4 extra", "Expected:"),
            ("Hunter Shoes 8 1 4 2", "Expected:"),
            ("8 1 4", "Name cannot be empty"),
        ):
            with self.subTest(line=line), patch.object(download, "download_one") as fetch:
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    status = download.run_single_download(catalog, line.split(), "/tmp")

                self.assertEqual(status, 1)
                self.assertIn(error, output.getvalue())
                fetch.assert_not_called()

    def test_unquoted_batch_failures_remain_verbatim_and_in_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "items.txt"
            invalid = "  Hunter Shoes 99  \n"
            unknown = "  Unknown Ability \n"
            path.write_text(
                invalid + "Vendetta's Wrath 8\nHeroic Cleave\n" + unknown,
                encoding="utf-8",
            )
            with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(io.StringIO()):
                status = download.main([str(path)])

            self.assertEqual(status, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), invalid + unknown)
            self.assertEqual(fetch.call_count, 2)

    def test_interactive_mode_keeps_reading_after_bad_input_and_network_failure(self):
        lines = '"unterminated\nHunter Shoes 99\nUnknown Ability\nHeroic Cleave\nHunter Shoes 8 1 4\nexit\n'
        output = io.StringIO()
        with patch.object(download.sys, "stdin", io.StringIO(lines)), patch.object(
            download, "download_one", side_effect=[urllib.error.URLError("offline"), None]
        ) as fetch, contextlib.redirect_stdout(output):
            status = download.main([])

        self.assertEqual(status, 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(output.getvalue().count("[FAIL]"), 4)
        self.assertEqual(output.getvalue().count("[OK]"), 1)
        self.assertNotIn("download>", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())

    def test_interactive_mode_keeps_reading_after_file_write_failure(self):
        output = io.StringIO()
        with patch.object(download.sys, "stdin", io.StringIO("Heroic Cleave\nRefreshing Sprint\n")), patch.object(
            download, "download_one", side_effect=[PermissionError("read-only directory"), None]
        ) as fetch, contextlib.redirect_stdout(output):
            status = download.main([])

        self.assertEqual(status, 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("[FAIL] read-only directory", output.getvalue())
        self.assertIn("[OK]", output.getvalue())

    def test_interactive_mode_is_silent_until_a_request_is_entered(self):
        for lines in ("", "exit\n", "quit\n", "\n # ignored\n\nexit\n"):
            with self.subTest(lines=lines), patch.object(download.sys, "stdin", io.StringIO(lines)):
                output = io.StringIO()
                with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
                    status = download.main([])

                self.assertEqual(status, 0)
                self.assertEqual(output.getvalue(), "")
                fetch.assert_not_called()

    def test_interactive_interrupt_exits_without_a_traceback(self):
        for target in ("builtins.input", "download.download_one"):
            with self.subTest(target=target), patch.object(download.sys, "stdin", io.StringIO("Heroic Cleave\n")):
                output = io.StringIO()
                with patch(target, side_effect=KeyboardInterrupt), contextlib.redirect_stdout(output):
                    status = download.main([])

                self.assertEqual(status, 130)
                self.assertEqual(output.getvalue(), "\n")

    def test_help_does_not_enter_interactive_mode_or_load_the_catalog(self):
        for flag in ("-h", "--help"):
            output = io.StringIO()
            with self.subTest(flag=flag), patch.object(download, "load_item_catalog") as load:
                with patch("builtins.input") as read, contextlib.redirect_stdout(output):
                    status = download.main([flag])

                self.assertEqual(status, 0)
                self.assertIn("Usage:", output.getvalue())
                load.assert_not_called()
                read.assert_not_called()

    def test_direct_script_propagates_failure_exit_status(self):
        result = subprocess.run(
            [download.__file__, "Guardian Armor", "99"],
            text=True,
            capture_output=True,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] Invalid tier", result.stdout)

    def test_batch_file_uses_command_line_argument_format(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            path.write_text(
                '# ignored\n"Refreshing Sprint"\n"Guardian Armor" 6 1 4\n',
                encoding="utf-8",
            )

            requests, rejected = download.parse_requests_file(str(path))

        self.assertEqual(rejected, [])
        self.assertEqual(
            [
                (request.name, request.tier, request.enchant, request.quality)
                for request in requests
            ],
            [("Refreshing Sprint", -1, 0, 1), ("Guardian Armor", 6, 1, 4)],
        )

    def test_successful_batch_download_uses_parsed_arguments_and_empties_file(self):
        catalog = download.load_item_catalog()
        calls = []

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            path.write_text(
                '"Heroic Cleave"\n"Refreshing Sprint"\n"Guardian Armor" 6 1 4\n',
                encoding="utf-8",
            )

            with patch.object(
                download,
                "download_one",
                side_effect=lambda url, output_path: calls.append((url, output_path)),
            ), contextlib.redirect_stdout(io.StringIO()):
                status = download.run_batch_download(catalog, str(path), directory)

            self.assertEqual(status, 0)
            self.assertEqual(path.read_text(encoding="utf-8"), "")

        self.assertEqual(
            {url for url, _ in calls},
            {
                "https://render.albiononline.com/v1/spell/CLEAVE.png",
                "https://render.albiononline.com/v1/spell/SPRINT_CD_REDUCTION.png",
                "https://render.albiononline.com/v1/item/T6_ARMOR_PLATE_SET3@1.png?quality=4",
            },
        )

    def test_failed_batch_line_is_preserved_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            original = '  "Heroic Cleave"  \n'
            path.write_text(original, encoding="utf-8")

            with patch.object(
                download, "download_one", side_effect=urllib.error.URLError("offline")
            ), contextlib.redirect_stdout(io.StringIO()):
                status = download.run_batch_download({}, str(path), directory)

            self.assertEqual(status, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_rejected_and_download_failed_lines_keep_input_order(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            original = '"Heroic Cleave"\n"unterminated\n"Refreshing Sprint"\n'
            path.write_text(original, encoding="utf-8")

            with patch.object(
                download, "download_one", side_effect=urllib.error.URLError("offline")
            ), contextlib.redirect_stdout(io.StringIO()):
                status = download.run_batch_download({}, str(path), directory)

            self.assertEqual(status, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_batch_line_rejects_more_arguments_than_the_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            original = '"Guardian Armor" 6 1 4 extra\n'
            path.write_text(original, encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                requests, rejected = download.parse_requests_file(str(path))

        self.assertEqual(requests, [])
        self.assertEqual(rejected, [(1, original.rstrip("\n"))])

    def test_single_tierless_catalog_item_uses_item_endpoint(self):
        catalog = download.load_item_catalog()
        calls = []

        with patch.object(
            download, "download_one", side_effect=lambda url, path: calls.append((url, path))
        ), contextlib.redirect_stdout(io.StringIO()):
            status = download.run_single_download(catalog, ["Siphoned Energy"], "/tmp")

        self.assertEqual(status, 0)
        self.assertEqual(
            calls[0][0],
            "https://render.albiononline.com/v1/item/UNIQUE_GVGTOKEN_GENERIC.png",
        )

    def test_heroic_cleave_uses_ability_id_and_preserves_filename(self):
        for name in ("Heroic Cleave", "heroic cleave", "  HEROIC   CLEAVE  "):
            with self.subTest(name=name), patch.object(download, "download_one") as fetch:
                with contextlib.redirect_stdout(io.StringIO()):
                    status = download.run_single_download({}, [name], "/tmp")

                self.assertEqual(status, 0)
                fetch.assert_called_once_with(
                    "https://render.albiononline.com/v1/spell/CLEAVE.png",
                    "/tmp/" + name.strip() + ".png",
                )

    def test_spell_lookup_prefers_player_abilities_over_same_named_effects(self):
        for name, identifier in (
            ("Heroic Cleave", "CLEAVE"),
            ("Heroic Strike", "HEROICSTRIKE2"),
            ("Refreshing Sprint", "SPRINT_CD_REDUCTION"),
            ("Frost", "PASSIVE_FROST"),
            ("Deep Cuts", "PASSIVE_BLEEDCHANCE"),
            ("Earth Shatter", "HAMMERWHIRLWIND2"),
            ("Rush", "OVERSPRINT"),
        ):
            with self.subTest(name=name):
                self.assertEqual(download.resolve_spell(name), identifier)

    def test_internal_spell_ids_and_recast_ids_are_accepted(self):
        for name, identifier in (
            ("CLEAVE", "CLEAVE"),
            ("after_image_return", "AFTER_IMAGE_RETURN"),
            ("SPRINT_CD_REDUCTION", "SPRINT_CD_REDUCTION"),
        ):
            with self.subTest(name=name):
                self.assertEqual(
                    download.build_spell_url(name),
                    "https://render.albiononline.com/v1/spell/" + identifier + ".png",
                )

    def test_every_embedded_spell_id_is_accessible(self):
        names, identifiers = download.load_spell_catalog()
        rows = [
            row for row in csv.reader(io.StringIO(download.SPELLS_CSV))
            if row and not row[0].startswith("#")
        ]
        self.assertEqual(len(identifiers), len(rows))
        for row in rows:
            with self.subTest(identifier=row[1]):
                self.assertIn(download.norm_name(row[0]), names)
                self.assertEqual(download.resolve_spell(row[1]), row[1])
                if len(row) == 2:
                    self.assertIn(row[1], names[download.norm_name(row[0])])

    def test_ambiguous_spell_name_lists_ids_without_downloading(self):
        output = io.StringIO()
        with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
            status = download.run_single_download({}, ["Hush"], "/tmp")

        self.assertEqual(status, 1)
        self.assertIn("ambiguous", output.getvalue())
        self.assertIn("PASSIVE_SILENCECHANCE", output.getvalue())
        self.assertIn("WEAPON_SILENCE", output.getvalue())
        fetch.assert_not_called()

    def test_unknown_spell_name_fails_without_downloading(self):
        output = io.StringIO()
        with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
            status = download.run_single_download({}, ["Refreshing Spring"], "/tmp")

        self.assertEqual(status, 1)
        self.assertIn("not in the spell catalog", output.getvalue())
        fetch.assert_not_called()

    def test_potions_require_item_values_in_single_and_batch_modes(self):
        catalog = download.load_item_catalog()
        self.assertIn(download.norm_name("Healing Potion"), catalog)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            path.write_text('"Healing Potion"\n"Healing Potion" 4\n', encoding="utf-8")
            with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(io.StringIO()):
                single = download.run_single_download(catalog, ["Healing Potion"], directory)
                batch = download.run_batch_download(catalog, str(path), directory)

            self.assertEqual((single, batch), (1, 1))
            self.assertEqual(path.read_text(encoding="utf-8"), '"Healing Potion"\n')
            fetch.assert_called_once_with(
                "https://render.albiononline.com/v1/item/T4_POTION_HEAL.png",
                os.path.join(directory, "Healing Potion 4.png"),
            )

    def test_spell_catalog_excludes_non_equipment_spells_and_internal_effects(self):
        for identifier in (
            "CLEAVE_MOVESPEED_BUFF", "MOB_UNDEAD_HERO_CLEAVE", "MOUNTSPELL_BIGCLEAVE",
            "PASSIVE_CAPE_THETFORD", "POTION_HEAL_P1", "VANITY_WARBANNER_PRESENT",
            "PASSIVE_AVALON_YIELD_ORE_T6", "PROTOTYPE_CD_PENALTY",
        ):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                download.resolve_spell(identifier)
        for identifier in (
            "CLEAVE", "SPRINT_CD_REDUCTION", "PANTHER_CLAWS", "BEAR_ROAR",
            "PASSIVE_HEAD_YIELD_ORE_T8", "AFTER_IMAGE_RETURN",
        ):
            with self.subTest(identifier=identifier):
                self.assertEqual(download.resolve_spell(identifier), identifier)

    def test_unknown_and_ambiguous_batch_names_remain_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            unknown = '  "Foo, Bar"  \n'
            ambiguous = '"Hush"\n'
            path.write_text(unknown + '"Heroic Cleave"\n' + ambiguous, encoding="utf-8")
            with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(io.StringIO()):
                status = download.run_batch_download({}, str(path), directory)

            self.assertEqual(status, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), unknown + ambiguous)
            fetch.assert_called_once_with(
                "https://render.albiononline.com/v1/spell/CLEAVE.png",
                os.path.join(directory, "Heroic Cleave.png"),
            )

    def test_concurrent_duplicate_downloads_use_unique_temporary_files(self):
        real_replace = os.replace
        replace_sources = []
        replace_sources_lock = threading.Lock()

        def record_replace(source, destination):
            with replace_sources_lock:
                replace_sources.append(source)
            real_replace(source, destination)

        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "duplicate.png")
            with patch.object(
                download.urllib.request,
                "urlopen",
                side_effect=lambda *args, **kwargs: FakeResponse(),
            ), patch.object(download.os, "replace", side_effect=record_replace):
                with ThreadPoolExecutor(max_workers=2) as executor:
                    futures = [
                        executor.submit(
                            download.download_one, "https://example.invalid/icon", target
                        )
                        for _ in range(2)
                    ]
                    for future in futures:
                        future.result()

            self.assertEqual(len(set(replace_sources)), 2)
            self.assertEqual(Path(target).read_bytes(), b"png")

    def test_atomic_batch_rewrite_preserves_original_if_replace_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            original = '"Original Request"\n'
            path.write_text(original, encoding="utf-8")

            with patch.object(download.os, "replace", side_effect=OSError("replace failed")):
                with self.assertRaises(OSError):
                    download.rewrite_failed_lines(str(path), ['"Failed Request"'])

            self.assertEqual(path.read_text(encoding="utf-8"), original)
            self.assertEqual(list(path.parent.glob(f".{path.name}.*.tmp")), [])

    def test_atomic_batch_rewrite_preserves_file_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            path.write_text('"Original Request"\n', encoding="utf-8")
            path.chmod(0o640)

            download.rewrite_failed_lines(str(path), ['"Failed Request"'])

            self.assertEqual(path.read_text(encoding="utf-8"), '"Failed Request"\n')
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_catalog_load_error_is_reported_without_a_traceback(self):
        output = io.StringIO()
        with patch.object(
            download,
            "load_item_catalog",
            side_effect=FileNotFoundError("missing catalog"),
        ), contextlib.redirect_stdout(output):
            status = download.main(["Refreshing Sprint"])

        self.assertEqual(status, 1)
        self.assertIn("[FAIL] Could not load item catalog: missing catalog", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


class SpellCatalogGenerationTests(unittest.TestCase):
    def test_scope_follows_equipped_spells_inheritance_and_used_forms(self):
        items = {
            "weapon": [
                {"@uniquename": "T4_SWORD", "@shopcategory": "weapons", "@slottype": "mainhand",
                 "craftingspelllist": {"craftspell": {"@uniquename": "CLEAVE"}}},
                {"@uniquename": "T4_PICK", "@shopcategory": "gathering", "@slottype": "mainhand",
                 "craftingspelllist": {"craftspell": {"@uniquename": "TOOL_PASSIVE"}}},
            ],
            "equipmentitem": [
                {"@uniquename": "T3_SHOES", "@shopcategory": "shoes", "@slottype": "shoes",
                 "@shopsubcategory1": "leather_shoes", "@hidefromplayeroncontext": "all",
                 "craftingspelllist": {"craftspell": [{"@uniquename": "RUN"}, {"@uniquename": "OLD_SPRINT"}]}},
                {"@uniquename": "T4_SHOES", "@shopcategory": "shoes", "@slottype": "shoes",
                 "@shopsubcategory1": "leather_shoes", "craftingspelllist": {
                     "@reference": "T3_SHOES", "removespell": {"@uniquename": "OLD_SPRINT"},
                     "craftspell": {"@uniquename": "SPRINT_CD_REDUCTION"}}},
                {"@uniquename": "T4_CAPE", "@shopcategory": "capes", "@slottype": "cape",
                 "craftingspelllist": {"craftspell": {"@uniquename": "CAPE_PASSIVE"}}},
                {"@uniquename": "T8_HEAD_PROTOTYPE", "@shopcategory": "head", "@slottype": "head",
                 "@shopsubcategory1": "other", "craftingspelllist": {"craftspell": {"@uniquename": "PROTOTYPE"}}},
            ],
            "transformationweapon": [
                {"@uniquename": "T4_SHAPESHIFTER", "@shopcategory": "weapons", "@slottype": "mainhand",
                 "@transformation": "PANTHER", "craftingspelllist": {"craftspell": {"@uniquename": "SHAPESHIFT_PANTHER"}}},
            ],
            "mount": [{"@uniquename": "T4_MOUNT", "mountspelllist": {"mountspell": {"@uniquename": "MOUNT_SPELL"}}}],
            "consumableitem": [{"@uniquename": "T4_POTION", "@consumespell": "POTION_SPELL"}],
        }
        forms = {"transformation": [
            {"@uniquename": "PANTHER", "spells": {"spell": {"@uniquename": "PANTHER_CLAWS"}},
             "passivespells": {"passivespell": {"@uniquename": "PASSIVE_SHAPE_PANTHER"}}},
            {"@uniquename": "UNUSED", "spells": {"spell": {"@uniquename": "MOB_SPELL"}}},
        ]}
        self.assertEqual(equipment_spell_ids(items, forms), {
            "CLEAVE", "RUN", "SPRINT_CD_REDUCTION", "SHAPESHIFT_PANTHER",
            "PANTHER_CLAWS", "PASSIVE_SHAPE_PANTHER",
        })

    def test_recasts_are_included_without_collecting_damage_and_buff_effects(self):
        definitions = {
            "FIRST": {"multispell": {"@spell": "SECOND"}, "applyspell": {"@spell": "DAMAGE_EFFECT"}},
            "SECOND": {"multispell": {"@spell": "THIRD"}},
            "THIRD": {"multispell": {"@spell": "FIRST"}},
            "DAMAGE_EFFECT": {},
        }
        self.assertEqual(include_recast_spells({"FIRST"}, definitions), {"FIRST", "SECOND", "THIRD"})


if __name__ == "__main__":
    unittest.main()
