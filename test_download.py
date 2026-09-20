import codecs
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import download
from scripts.update_spells import (
    curated_spell_names, equipment_spell_ids, generate_catalog, include_recast_spells, select_spells,
)


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"png"


class TerminalBuffer(io.StringIO):
    def isatty(self):
        return True


class DownloadTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="download test ")
        self.addCleanup(directory.cleanup)
        self.output_dir = directory.name

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
                    status = download.run_single_download(catalog, line.split(), self.output_dir)

                self.assertEqual(status, 0)
                fetch.assert_called_once_with(
                    "https://render.albiononline.com/v1/" + endpoint + ".png",
                    os.path.join(self.output_dir, filename + ".png"),
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
                    status = download.run_single_download(catalog, line.split(), self.output_dir)

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
            [sys.executable, download.__file__, "Guardian Armor", "99"],
            text=True,
            capture_output=True,
            timeout=15,
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("[FAIL] Invalid tier", result.stdout)

    def test_redirected_output_handles_characters_outside_its_encoding(self):
        for encoding in ("ascii", "cp1252"):
            with self.subTest(encoding=encoding):
                result = subprocess.run(
                    [sys.executable, download.__file__, "Unknown \u6f22 ability"],
                    env=dict(os.environ, PYTHONIOENCODING=encoding),
                    capture_output=True, timeout=15,
                )
                self.assertEqual(result.returncode, 1)
                self.assertIn(b"[FAIL]", result.stdout)
                self.assertIn(b"\\u6f22", result.stdout)
                self.assertEqual(result.stderr, b"")

    def test_utf8_and_utf16_batches_replace_outputs_and_keep_failures(self):
        successful = {
            "Lumberjack's Journal 8": "Lumberjack's Journal 8.png",
            "Hunter Shoes 8 1 4": "Hunter Shoes 8.1 Excellent.png",
            "Rending Rage (second cast)": "Rending Rage (second cast).png",
            "Siphoned Energy": "Siphoned Energy.png",
        }
        invalid = "  Hunter Shoes 99  "
        unknown = "  Unknown \u6f22 \U0001f600 Ability  "
        lines = [next(iter(successful)), invalid, "# comment", ""]
        lines += list(successful)[1:] + [unknown]
        for encoding, bom in (("utf-8", b""), ("utf-8", codecs.BOM_UTF8),
                              ("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE)):
            for newline in ("\n", "\r\n"):
                with self.subTest(encoding=encoding, bom=bom, newline=newline), tempfile.TemporaryDirectory() as directory:
                    path = Path(directory) / "batch file.txt"
                    path.write_bytes(bom + (newline.join(lines) + newline).encode(encoding))
                    for filename in successful.values():
                        (Path(directory) / filename).write_bytes(b"old image")
                    with patch.object(download.os, "getcwd", return_value=directory), patch.object(
                        download.urllib.request, "urlopen", return_value=FakeResponse()
                    ) as fetch, contextlib.redirect_stdout(io.StringIO()):
                        status = download.main([str(path)])

                    self.assertEqual(status, 1)
                    self.assertEqual(fetch.call_count, len(successful))
                    self.assertEqual(path.read_bytes(), (invalid + "\n" + unknown + "\n").encode("utf-8"))
                    for filename in successful.values():
                        self.assertEqual((Path(directory) / filename).read_bytes(), b"png")
                    self.assertEqual(sorted(p.name for p in Path(directory).iterdir()),
                                     sorted([path.name] + list(successful.values())))

    def test_redirected_success_preserves_unicode_output_path(self):
        directory = Path(self.output_dir) / "icons \u6f22"
        directory.mkdir()
        buffer = io.BytesIO()
        with io.TextIOWrapper(buffer, encoding="ascii") as output:
            with patch.object(download.sys, "stdout", output), patch.object(
                download.os, "getcwd", return_value=str(directory)
            ), patch.object(download.urllib.request, "urlopen", return_value=FakeResponse()):
                self.assertEqual(download.main(["Siphoned", "Energy"]), 0)
            output.flush()
            self.assertIn(b"[OK]", buffer.getvalue())
            self.assertIn(b"\\u6f22", buffer.getvalue())
        self.assertEqual((directory / "Siphoned Energy.png").read_bytes(), b"png")

    def test_utf16_successful_and_bom_only_batches_become_empty(self):
        path = Path(self.output_dir) / "utf16.txt"
        for encoding, bom in (("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE)):
            for text in ("Heroic Cleave", ""):
                with self.subTest(encoding=encoding, text=text):
                    path.write_bytes(bom + text.encode(encoding))
                    with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(io.StringIO()):
                        self.assertEqual(download.main([str(path)]), 0)
                    self.assertEqual(path.read_bytes(), b"")
                    self.assertEqual(fetch.call_count, 1 if text else 0)

    def test_invalid_batch_encodings_are_reported_and_left_unchanged(self):
        path = Path(self.output_dir) / "invalid.txt"
        valid_line = "Heroic Cleave\r\n"
        samples = [valid_line.encode("utf-8") + b"\xff"]
        for encoding, bom in (("utf-16-le", codecs.BOM_UTF16_LE), ("utf-16-be", codecs.BOM_UTF16_BE)):
            prefix = bom + valid_line.encode(encoding)
            samples.append(prefix + b"\x00")
            samples.append(prefix + "\ud800".encode(encoding, errors="surrogatepass"))
        for encoding, bom in (("utf-32-le", codecs.BOM_UTF32_LE), ("utf-32-be", codecs.BOM_UTF32_BE)):
            samples.append(bom + valid_line.encode(encoding))
        for original in samples:
            with self.subTest(original=original):
                path.write_bytes(original)
                output = io.StringIO()
                with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
                    self.assertEqual(download.main([str(path)]), 1)
                self.assertIn("Could not read batch file", output.getvalue())
                self.assertNotIn("Traceback", output.getvalue())
                self.assertEqual(path.read_bytes(), original)
                fetch.assert_not_called()

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
            [("Refreshing Sprint", -1, None, 1), ("Guardian Armor", 6, 1, 4)],
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
            status = download.run_single_download(catalog, ["Siphoned Energy"], self.output_dir)

        self.assertEqual(status, 0)
        self.assertEqual(
            calls[0][0],
            "https://render.albiononline.com/v1/item/UNIQUE_GVGTOKEN_GENERIC.png",
        )

    def test_heroic_cleave_uses_ability_id_and_preserves_filename(self):
        for name in ("Heroic Cleave", "heroic cleave", "  HEROIC   CLEAVE  "):
            with self.subTest(name=name), patch.object(download, "download_one") as fetch:
                with contextlib.redirect_stdout(io.StringIO()):
                    status = download.run_single_download({}, [name], self.output_dir)

                self.assertEqual(status, 0)
                fetch.assert_called_once_with(
                    "https://render.albiononline.com/v1/spell/CLEAVE.png",
                    os.path.join(self.output_dir, name.strip() + ".png"),
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

    def test_every_curated_spell_name_and_id_is_accessible(self):
        names, identifiers = download.load_spell_catalog()
        spells = json.loads(download.CATALOGUE_PATH.read_text(encoding="utf-8"))["spells"]
        self.assertEqual(len(identifiers), len(set(spells.values())))
        self.assertEqual(set(names), {download.norm_name(name) for name in spells})
        for name, identifier in spells.items():
            with self.subTest(name=name):
                self.assertEqual(download.resolve_spell(name), identifier)
                self.assertEqual(download.resolve_spell(identifier), identifier)

    def test_curated_labels_select_passive_and_recast_icons(self):
        for name, identifier in (
            ("Hush", "WEAPON_SILENCE"),
            ("Hush (passive)", "PASSIVE_SILENCECHANCE"),
            ("Hush (Passive)", "PASSIVE_SILENCECHANCE"),
            ("Rending Rage", "RENDINGCOMBO"),
            ("Rending Rage (second cast)", "RENDINGCOMBO_MULTI2"),
            ("Rending Rage (Second cast)", "RENDINGCOMBO_MULTI2"),
            ("Rending Rage (Raging Leap)", "RENDINGCOMBO_MULTI3"),
            ("After Image (Return to Image)", "AFTER_IMAGE_RETURN"),
            ("Eye of the Storm (Dreadstorm Surge)", "MACE_CRYSTAL_FRAGMENT_STORM_MULTI2"),
            ("Purifying Combination (Purifying Fist)", "TRIPLECOMBO_POWERPUNCH"),
            ("Wild Onslaught (Roar)", "BEAR_ROAR"),
        ):
            with self.subTest(name=name), patch.object(download, "download_one") as fetch:
                with contextlib.redirect_stdout(io.StringIO()):
                    status = download.run_single_download({}, name.split(), self.output_dir)
                self.assertEqual(status, 0)
                fetch.assert_called_once_with(
                    f"https://render.albiononline.com/v1/spell/{identifier}.png",
                    os.path.join(self.output_dir, name + ".png"),
                )

    def test_unknown_spell_name_fails_without_downloading(self):
        output = io.StringIO()
        with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
            status = download.run_single_download({}, ["Refreshing Spring"], self.output_dir)

        self.assertEqual(status, 1)
        self.assertIn("not in the spell catalog", output.getvalue())
        fetch.assert_not_called()

    def test_terminal_spell_selection_in_command_and_interactive_modes(self):
        cases = (
            ("hUsH", "1", "WEAPON_SILENCE", "hUsH"),
            ("hush", "2", "PASSIVE_SILENCECHANCE", "hush (passive)"),
            ("Rending Rage", "1", "RENDINGCOMBO", "Rending Rage"),
            ("Rending Rage", "2", "RENDINGCOMBO_MULTI2", "Rending Rage (second cast)"),
            ("RENDING   RAGE", "3", "RENDINGCOMBO_MULTI3", "RENDING RAGE (Raging Leap)"),
            ("After Image", "2", "AFTER_IMAGE_RETURN", "After Image (Return to Image)"),
            ("Eye of the Storm", "2", "MACE_CRYSTAL_FRAGMENT_STORM_MULTI2", "Eye of the Storm (Dreadstorm Surge)"),
            ("Purifying Combination", "2", "TRIPLECOMBO_POWERPUNCH", "Purifying Combination (Purifying Fist)"),
            ("Wild Onslaught", "2", "BEAR_ROAR", "Wild Onslaught (Roar)"),
            ("Enchanted Quiver", "2", "SPEEDARCHER_KITE_MULTI_DASH", "Enchanted Quiver (dash)"),
        )
        for mode in ("command", "interactive"):
            for name, choice, identifier, filename in cases:
                with self.subTest(mode=mode, name=name, choice=choice):
                    lines = choice + "\n"
                    if mode == "interactive":
                        lines = name + "\n" + lines + "exit\n"
                    output = TerminalBuffer()
                    with patch.object(download.sys, "stdin", TerminalBuffer(lines)), patch.object(
                        download.os, "getcwd", return_value=self.output_dir
                    ), patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
                        self.assertEqual(download.main(name.split() if mode == "command" else []), 0)
                    fetch.assert_called_once_with(
                        f"https://render.albiononline.com/v1/spell/{identifier}.png",
                        os.path.join(self.output_dir, filename + ".png"),
                    )
                    self.assertEqual(output.getvalue().count("Choose an icon"), 1)
                    self.assertIn("Enter to cancel", output.getvalue())
                    if name.lower() == "hush":
                        self.assertIn("  1. Hush\n  2. Hush (passive)\n", output.getvalue())
                    elif name == "Rending Rage":
                        self.assertIn("  1. Rending Rage\n  2. Rending Rage (second cast)\n"
                                      "  3. Rending Rage (Raging Leap)\n", output.getvalue())

    def test_terminal_explicit_labels_ids_and_unambiguous_names_skip_selection(self):
        for name in ("Hush (passive)", "Rending Rage (second cast)", "Rending Rage (raging leap)",
                     "After Image (return to image)", "Heroic Cleave",
                     "WEAPON_SILENCE", "PASSIVE_SILENCECHANCE", "rendingcombo_multi3", "RUSH"):
            with self.subTest(name=name), patch.object(download.sys, "stdin", TerminalBuffer()):
                output = TerminalBuffer()
                with patch("builtins.input") as read, patch.object(download, "download_one") as fetch:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(download.run_single_download({}, [name], self.output_dir), 0)
                read.assert_not_called()
                fetch.assert_called_once_with(
                    download.build_spell_url(name), os.path.join(self.output_dir, name + ".png")
                )
                self.assertNotIn("Choose an icon", output.getvalue())

    def test_spell_selection_retries_invalid_choices_without_downloading_them(self):
        output = TerminalBuffer()
        with patch.object(download.sys, "stdin", TerminalBuffer("0\n4\nsecond\n2\n")), patch.object(
            download, "download_one"
        ) as fetch, contextlib.redirect_stdout(output):
            self.assertEqual(download.run_single_download({}, ["Rending Rage"], self.output_dir), 0)
        self.assertEqual(output.getvalue().count("Enter a number from 1 to 3"), 3)
        fetch.assert_called_once_with(
            "https://render.albiononline.com/v1/spell/RENDINGCOMBO_MULTI2.png",
            os.path.join(self.output_dir, "Rending Rage (second cast).png"),
        )

    def test_spell_selection_cancellation_and_eof_do_not_download(self):
        for lines in ("\n", ""):
            with self.subTest(lines=lines), patch.object(download.sys, "stdin", TerminalBuffer(lines)):
                output = TerminalBuffer()
                with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
                    self.assertEqual(download.main(["Hush"]), 1)
                fetch.assert_not_called()
                self.assertIn("[FAIL] Cancelled 'Hush'.", output.getvalue())

    def test_interactive_spell_selection_continues_after_cancellation_and_download_failure(self):
        output = TerminalBuffer()
        lines = "Hush\n\nRending Rage\n2\nHeroic Cleave\nexit\n"
        with patch.object(download.sys, "stdin", TerminalBuffer(lines)), patch.object(
            download, "download_one", side_effect=[urllib.error.URLError("offline"), None]
        ) as fetch, contextlib.redirect_stdout(output):
            self.assertEqual(download.main([]), 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertIn("Cancelled 'Hush'", output.getvalue())
        self.assertIn("Download failed for spell 'Rending Rage (second cast)'", output.getvalue())
        self.assertIn("[OK] Downloaded spell 'Heroic Cleave'", output.getvalue())

    def test_eof_and_exit_commands_at_spell_menu_end_the_interactive_session(self):
        for ending in (EOFError, "exit", "quit"):
            output = TerminalBuffer()
            with self.subTest(ending=ending), patch.object(download.sys, "stdin", TerminalBuffer()):
                with patch("builtins.input", side_effect=["Hush", ending]) as read:
                    with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
                        self.assertEqual(download.main([]), 1)
                self.assertEqual(read.call_count, 2)
                fetch.assert_not_called()
                self.assertIn("Cancelled 'Hush'", output.getvalue())

    def test_spell_selection_interrupt_exits_without_a_traceback(self):
        for args in (["Hush"], []):
            output = TerminalBuffer()
            reads = [KeyboardInterrupt] if args else ["Hush", KeyboardInterrupt]
            with self.subTest(args=args), patch.object(download.sys, "stdin", TerminalBuffer()):
                with patch("builtins.input", side_effect=reads), patch.object(download, "download_one") as fetch:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(download.main(args), 130)
                fetch.assert_not_called()
                self.assertNotIn("Traceback", output.getvalue())

    def test_redirected_spell_requests_keep_exact_names_without_reading_a_choice(self):
        for stdin_tty, stdout_tty in ((False, True), (True, False), (False, False)):
            with self.subTest(stdin_tty=stdin_tty, stdout_tty=stdout_tty):
                output = TerminalBuffer() if stdout_tty else io.StringIO()
                stream = TerminalBuffer("2\n") if stdin_tty else io.StringIO("2\n")
                with patch.object(download.sys, "stdin", stream), patch("builtins.input") as read:
                    with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
                        self.assertEqual(download.run_single_download({}, ["Rending Rage"], self.output_dir), 0)
                read.assert_not_called()
                self.assertEqual(stream.read(), "2\n")
                fetch.assert_called_once_with(
                    "https://render.albiononline.com/v1/spell/RENDINGCOMBO.png",
                    os.path.join(self.output_dir, "Rending Rage.png"),
                )

    def test_piped_interactive_requests_do_not_consume_the_next_request_as_a_choice(self):
        output = TerminalBuffer()
        with patch.object(download.sys, "stdin", io.StringIO("Hush\nRending Rage\nexit\n")), patch.object(
            download, "download_one"
        ) as fetch, contextlib.redirect_stdout(output):
            self.assertEqual(download.main([]), 0)
        self.assertEqual([call.args[0] for call in fetch.call_args_list], [
            "https://render.albiononline.com/v1/spell/WEAPON_SILENCE.png",
            "https://render.albiononline.com/v1/spell/RENDINGCOMBO.png",
        ])
        self.assertNotIn("Choose an icon", output.getvalue())

    def test_batch_spell_requests_never_prompt_even_in_a_terminal(self):
        path = Path(self.output_dir) / "spells.txt"
        path.write_text("Hush\nRending Rage\nRending Rage (second cast)\n", encoding="utf-8")
        output = TerminalBuffer()
        with patch.object(download.sys, "stdin", TerminalBuffer()), patch("builtins.input") as read:
            with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(output):
                self.assertEqual(download.run_batch_download({}, str(path), self.output_dir), 0)
        read.assert_not_called()
        self.assertEqual(path.read_bytes(), b"")
        self.assertEqual({call.args[0] for call in fetch.call_args_list}, {
            "https://render.albiononline.com/v1/spell/WEAPON_SILENCE.png",
            "https://render.albiononline.com/v1/spell/RENDINGCOMBO.png",
            "https://render.albiononline.com/v1/spell/RENDINGCOMBO_MULTI2.png",
        })

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
            "PASSIVE_HEAD_YIELD_ORE_T8", "AXETHROW_SECOND", "Sky Fall",
        ):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                download.resolve_spell(identifier)
        for identifier in (
            "CLEAVE", "SPRINT_CD_REDUCTION", "PANTHER_CLAWS", "BEAR_ROAR",
            "AFTER_IMAGE_RETURN",
        ):
            with self.subTest(identifier=identifier):
                self.assertEqual(download.resolve_spell(identifier), identifier)

    def test_unknown_and_excluded_batch_names_remain_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            unknown = '  "Foo, Bar"  \n'
            excluded = '"Sky Fall"\n'
            path.write_text(unknown + '"Heroic Cleave"\n' + excluded, encoding="utf-8")
            with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(io.StringIO()):
                status = download.run_batch_download({}, str(path), directory)

            self.assertEqual(status, 1)
            self.assertEqual(path.read_text(encoding="utf-8"), unknown + excluded)
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
            if os.name == "nt":
                self.assertTrue(path.stat().st_mode & stat.S_IWRITE)
            else:
                self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_atomic_image_write_preserves_original_if_replace_fails(self):
        path = Path(self.output_dir) / "existing.png"
        path.write_bytes(b"original image")
        with patch.object(download.urllib.request, "urlopen", return_value=FakeResponse()), patch.object(
            download.os, "replace", side_effect=PermissionError("replace denied")
        ), self.assertRaises(PermissionError):
            download.download_one("https://example.invalid/icon", str(path))
        self.assertEqual(path.read_bytes(), b"original image")
        self.assertEqual(list(path.parent.glob(f".{path.name}.*.part")), [])

    def test_atomic_image_replacement_preserves_file_permissions(self):
        path = Path(self.output_dir) / "existing.png"
        path.write_bytes(b"original image")
        path.chmod(0o640)
        with patch.object(download.urllib.request, "urlopen", return_value=FakeResponse()):
            download.download_one("https://example.invalid/icon", str(path))
        self.assertEqual(path.read_bytes(), b"png")
        if os.name == "nt":
            self.assertTrue(path.stat().st_mode & stat.S_IWRITE)
        else:
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    @unittest.skipUnless(os.name == "nt", "requires native Windows read-only file semantics")
    def test_windows_read_only_replacement_preserves_original_and_cleans_temp_files(self):
        for operation in ("image", "batch"):
            with self.subTest(operation=operation):
                path = Path(self.output_dir) / (operation + ".txt")
                path.write_bytes(b"original")
                path.chmod(stat.S_IREAD)
                try:
                    with self.assertRaises(PermissionError):
                        if operation == "image":
                            with patch.object(download.urllib.request, "urlopen", return_value=FakeResponse()):
                                download.download_one("https://example.invalid/icon", str(path))
                        else:
                            download.rewrite_failed_lines(str(path), ["failed request"])
                    self.assertEqual(path.read_bytes(), b"original")
                    self.assertFalse(path.stat().st_mode & stat.S_IWRITE)
                    self.assertEqual(list(path.parent.glob(f".{path.name}.*")), [])
                finally:
                    path.chmod(stat.S_IREAD | stat.S_IWRITE)

    def test_catalog_load_error_is_reported_without_a_traceback(self):
        output = io.StringIO()
        with patch.object(
            download,
            "load_item_catalog",
            side_effect=FileNotFoundError("missing catalog"),
        ), contextlib.redirect_stdout(output):
            status = download.main(["Refreshing Sprint"])

        self.assertEqual(status, 1)
        self.assertIn("[FAIL] Could not load catalogue: missing catalog", output.getvalue())
        self.assertNotIn("Traceback", output.getvalue())


class CatalogueTests(unittest.TestCase):
    def test_item_rules_construct_special_identifiers_and_filenames(self):
        catalog = download.load_item_catalog()
        cases = (
            ("Bow 2", "T2_2H_BOW", "Bow 2.png"),
            ("Dungeon Map (Solo) 3 1", "T3_RANDOM_DUNGEON_SOLO_TOKEN_D1@1", "Dungeon Map (Solo) 3.1.png"),
            ("Dungeon Map (Group) 8 4", "T8_RANDOM_DUNGEON_TOKEN_D4@4", "Dungeon Map (Group) 8.4.png"),
            ("Waystone (Large Group) 6", "T6_RANDOM_DUNGEON_ELITE_DRAGON_TOKEN_D1@1", "Waystone (Large Group) 6.1.png"),
            ("Waystone (Large Group) 8 4", "T8_RANDOM_DUNGEON_ELITE_DRAGON_TOKEN_D4@4", "Waystone (Large Group) 8.4.png"),
            ("Swiftclaw", "T5_MOUNT_COUGAR_KEEPER@1", "Swiftclaw 5.1.png"),
            ("Beef Stew", "T8_MEAL_STEW", "Beef Stew.png"),
            ("Black Panther 8", "UNIQUE_MOUNT_BLACK_PANTHER_ADC", "Black Panther.png"),
            ("Siphoned Energy", "UNIQUE_GVGTOKEN_GENERIC", "Siphoned Energy.png"),
            ("Healing Potion 2", "T2_POTION_HEAL", "Healing Potion 2.png"),
            ("Healing Potion 6 3", "T6_POTION_HEAL@3", "Healing Potion 6.3.png"),
        )
        for line, identifier, filename in cases:
            with self.subTest(line=line):
                request = download.parse_request(line.split())
                self.assertEqual(download.resolve_item(catalog, *request), (identifier, filename))
        quest = download.parse_item_entry("Quest", {"id": "QUESTITEM_TEST", "tiers": [1], "enchant": 0, "quality": 1})
        self.assertEqual(download.resolve_item({"quest": quest}, "Quest", 1, 0, 1), ("QUESTITEM_TEST", "Quest.png"))

    def test_gathering_journals_have_only_verified_tiers_and_empty_identifiers(self):
        catalog = download.load_item_catalog()
        for name, material in (
            ("Lumberjack's Journal", "WOOD"), ("Stonecutter's Journal", "STONE"),
            ("Prospector's Journal", "ORE"), ("Cropper's Journal", "FIBER"),
            ("Gamekeeper's Journal", "HIDE"),
        ):
            for tier in range(2, 9):
                with self.subTest(name=name, tier=tier):
                    self.assertEqual(download.resolve_item(catalog, name, tier, None, 1),
                                     (f"T{tier}_JOURNAL_{material}_EMPTY", f"{name} {tier}.png"))

    def test_aliases_share_rules_and_preserve_requested_filenames(self):
        catalog = download.load_item_catalog()
        for alias, canonical in json.loads(download.CATALOGUE_PATH.read_text(encoding="utf-8"))["aliases"].items():
            with self.subTest(alias=alias):
                self.assertIs(catalog[download.norm_name(alias)], catalog[download.norm_name(canonical)])
                entered = "  ".join(alias.lower().split())
                identifier, filename = download.resolve_item(catalog, entered, 8, 4, 5)
                self.assertEqual(identifier, download.resolve_item(catalog, canonical, 8, 4, 5)[0])
                self.assertEqual(filename, entered + " 8.4 Masterpiece.png")

    def test_invalid_catalogue_options_never_download_in_any_input_mode(self):
        lines = [
            "Hunter Shoes 3", "Broadsword 3 1", "Beef Stew 8 0 2",
            "Lumberjack's Journal 1", "Lumberjack's Journal 8 1",
            "Swiftclaw 5 0", "Dungeon Map (Solo) 3 2", "Waystone (Large Group) 6 0",
            "Siphoned Energy 8", "Siphoned Energy 1 0 2", "Hunter Shoes",
        ]
        for mode in ("command", "interactive", "file"):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "requests.txt"
                path.write_text("\n".join(lines) + "\n", encoding="utf-8")
                with patch.object(download, "download_one") as fetch, contextlib.redirect_stdout(io.StringIO()):
                    if mode == "command":
                        statuses = [download.main(line.split()) for line in lines]
                        self.assertEqual(statuses, [1] * len(lines))
                    elif mode == "interactive":
                        with patch.object(download.sys, "stdin", io.StringIO(path.read_text(encoding="utf-8") + "exit\n")):
                            self.assertEqual(download.main([]), 1)
                    else:
                        self.assertEqual(download.main([str(path)]), 1)
                        self.assertEqual(path.read_text(encoding="utf-8"), "\n".join(lines) + "\n")
                fetch.assert_not_called()

    def test_installed_runtime_uses_its_own_catalogue_from_another_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            installed = Path(directory) / "bin with spaces"
            installed.mkdir()
            work = Path(directory) / "output with spaces \u6f22"
            work.mkdir()
            (work / "catalogue.json").write_text("{}", encoding="utf-8")
            root = Path(download.__file__).parent
            for name in ("download", "download.cmd", "download.py", "catalogue.json"):
                shutil.copy2(root / name, installed / name)
            commands = [[sys.executable, str(installed / name)] for name in ("download", "download.py")]
            if os.name == "nt":
                commands.append([str(installed / "download.cmd")])
            else:
                commands.extend([[str(installed / name)] for name in ("download", "download.py")])
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(command, input="exit\n", cwd=work,
                                            text=True, capture_output=True, timeout=15)
                    self.assertEqual((result.returncode, result.stdout, result.stderr), (0, "", ""))
            (installed / "catalogue.json").unlink()
            for command in commands:
                with self.subTest(command=command):
                    result = subprocess.run(command + ["--help"], cwd=work,
                                            text=True, capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 0)
                    result = subprocess.run(command + ["Heroic Cleave"], cwd=work,
                                            text=True, capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 1)
                    self.assertIn("Could not load catalogue", result.stdout)
                    self.assertNotIn("Traceback", result.stderr)

    def test_lists_match_catalogue_scope(self):
        data = json.loads(download.CATALOGUE_PATH.read_text(encoding="utf-8"))
        root = download.CATALOGUE_PATH.parent / "lists"
        items = {name.strip() for path in root.glob("*.txt") if path.name != "spells.txt"
                 for line in path.read_text(encoding="utf-8").splitlines() for name in line.split(",") if name.strip()}
        self.assertEqual(items, set(data["items"]) | set(data["aliases"]))
        self.assertEqual(set(curated_spell_names((root / "spells.txt").read_text(encoding="utf-8"))), set(data["spells"]))

    def test_malformed_catalogues_fail_without_falling_back(self):
        base = {"items": {"Example": {"id": "MAIN_TEST", "tiers": [4], "enchant": 0, "quality": 1}},
                "spells": {"Example Spell": "TEST"}, "aliases": {}}
        cases = ["{", "[]", json.dumps({"items": {}}),
                 json.dumps(base).replace('"MAIN_TEST"', '"MAIN_TEST", "id": "OTHER"')]
        for field, value in (("tiers", []), ("tiers", [True]), ("quality", 6), ("min_enchant", 1),
                             ("enchant_by_tier", {"8": [0]}), ("enchant_by_tier", {"4": [1]}),
                             ("id_by_enchant", {"1": "OTHER"}), ("id", "")):
            broken = json.loads(json.dumps(base))
            broken["items"]["Example"][field] = value
            cases.append(json.dumps(broken))
        for aliases in ({"Alias": "Missing"}, {"EXAMPLE": "Example"}):
            broken = dict(base, aliases=aliases)
            cases.append(json.dumps(broken))
        cases.append(json.dumps(dict(base, spells={"Spell": "ONE", " spell ": "TWO"})))
        for content in cases:
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "catalogue.json"
                path.write_text(content, encoding="utf-8")
                output = io.StringIO()
                with patch.object(download, "CATALOGUE_PATH", path), patch.object(download, "download_one") as fetch:
                    with contextlib.redirect_stdout(output):
                        self.assertEqual(download.main(["Example", "4"]), 1)
                self.assertIn("Could not load catalogue", output.getvalue())
                fetch.assert_not_called()

    def test_legacy_environment_override_cannot_expand_the_catalogue(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(
            os.environ, {"ALBION_ITEM_CATALOG_PATH": str(Path(directory) / "missing.json")}
        ):
            self.assertIn("siphoned energy", download.load_item_catalog())
            with self.assertRaises(ValueError):
                download.resolve_spell("Sky Fall")


@unittest.skipUnless(os.name == "nt", "requires native Windows and py -3")
class WindowsLauncherTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="download launcher ")
        self.addCleanup(directory.cleanup)
        installed = Path(directory.name) / "tools with spaces & (round) !"
        installed.mkdir()
        self.work = Path(directory.name) / "output with spaces"
        self.work.mkdir()
        self.launcher = installed / "download.cmd"
        shutil.copy2(Path(download.__file__).with_name("download.cmd"), self.launcher)
        # Isolate the launcher's process contract from network and catalogue logic.
        (installed / "download.py").write_text(
            "import json, os, sys\n"
            "print(json.dumps({'args': sys.argv[1:], 'cwd': os.getcwd(), 'input': sys.stdin.read()}))\n"
            "print('launcher stderr', file=sys.stderr)\n"
            "raise SystemExit(int(os.environ['DOWNLOAD_TEST_STATUS']))\n",
            encoding="utf-8",
        )
        self.args = ["Hunter Shoes", "8", "1", "4", "Lumberjack's Journal",
                     "Hush (passive)", "Rending Rage (second cast)", "batch file.txt"]

    def assert_launcher_result(self, result, status, powershell=False):
        self.assertEqual(result.returncode, status)
        if powershell:
            # Windows PowerShell may format native stderr as an ErrorRecord.
            self.assertIn("launcher stderr", result.stderr)
        else:
            self.assertEqual(result.stderr, "launcher stderr\n")
        payload = json.loads(result.stdout)
        self.assertEqual(payload["args"], self.args)
        self.assertEqual(Path(payload["cwd"]).resolve(), self.work.resolve())
        self.assertEqual(payload["input"], "input line\n")

    def test_launcher_forwards_arguments_streams_working_directory_and_exit_status(self):
        for status in (0, 1, 130):
            with self.subTest(status=status):
                result = subprocess.run(
                    [str(self.launcher)] + self.args, input="input line\n", cwd=self.work,
                    env=dict(os.environ, DOWNLOAD_TEST_STATUS=str(status)),
                    text=True, capture_output=True, timeout=30,
                )
                self.assert_launcher_result(result, status)

    def test_powershell_preserves_arguments_and_last_exit_code(self):
        shells = [path for name in ("powershell.exe", "pwsh.exe") if (path := shutil.which(name))]
        if not shells:
            self.skipTest("requires Windows PowerShell or PowerShell 7")
        arguments = ", ".join("'" + arg.replace("'", "''") + "'" for arg in self.args)
        script = (
            f"$requestArgs = @({arguments})\n"
            "'input line' | & $env:DOWNLOAD_TEST_LAUNCHER @requestArgs\n"
            "exit $LASTEXITCODE\n"
        )
        for shell in shells:
            for status in (0, 1, 130):
                with self.subTest(shell=shell, status=status):
                    result = subprocess.run(
                        [shell, "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", script],
                        cwd=self.work, env=dict(os.environ, DOWNLOAD_TEST_STATUS=str(status),
                                               DOWNLOAD_TEST_LAUNCHER=str(self.launcher)),
                        text=True, capture_output=True, timeout=30,
                    )
                    self.assert_launcher_result(result, status, powershell=True)


class SpellCatalogGenerationTests(unittest.TestCase):
    def test_unlisted_spells_with_missing_localization_do_not_block_updates(self):
        sources = {
            "localization": {"tmx": {"body": {"tu": [{"@tuid": "@SPELLS_CLEAVE", "tuv": {
                "@xml:lang": "EN-US", "seg": "Heroic Cleave"}}]}}},
            "spells": {"spells": {"activespell": [{"@uniquename": "CLEAVE"}, {"@uniquename": "UNLISTED"}],
                                  "passivespell": [], "togglespell": []}},
            "items": {"items": {"weapon": [{"@uniquename": "T4_SWORD", "@shopcategory": "weapons",
                "@slottype": "mainhand", "craftingspelllist": {"craftspell": [
                    {"@uniquename": "CLEAVE"}, {"@uniquename": "UNLISTED"}]}}]}},
            "transformations": {"transformations": {}},
        }
        with tempfile.TemporaryDirectory() as directory:
            for name, value in sources.items():
                (Path(directory) / (name + ".json")).write_text(json.dumps(value), encoding="utf-8")
            self.assertEqual(generate_catalog(Path(directory), ["Heroic Cleave"], {"Heroic Cleave": "CLEAVE"}),
                             {"Heroic Cleave": "CLEAVE"})

    def test_list_expansion_preserves_separate_passives_and_grouped_casts(self):
        self.assertEqual(curated_spell_names("Rending Rage (second cast, Raging Leap)\n\nHush (passive)\nRending Rage\n"),
                         ["Rending Rage", "Rending Rage (second cast)", "Rending Rage (Raging Leap)", "Hush (passive)"])
        for label in ("passive", "Passive"):
            self.assertEqual(curated_spell_names(f"Hush ({label})\n"), [f"Hush ({label})"])

    def test_generation_keeps_reviewed_ids_and_only_includes_listed_names(self):
        spells = {"FIRST": ("Example", {"@uisprite": "icon"}),
                  "SECOND": ("Example", {"@uisprite": "recast"}),
                  "THIRD": ("Example", {"@uisprite": "finish"}),
                  "REMOVED": ("Removed", {"@uisprite": "other"})}
        previous = {"Example": "FIRST", "Example (second cast)": "SECOND",
                    "Example (Named Finish)": "THIRD", "Removed": "REMOVED"}
        names = curated_spell_names("Example (second cast, Named Finish)\n")
        result = select_spells(names, previous, spells, {"FIRST", "REMOVED"})
        self.assertEqual(result, {"Example": "FIRST", "Example (second cast)": "SECOND",
                                  "Example (Named Finish)": "THIRD"})
        self.assertEqual(list(result), names)

    def test_generation_requires_review_for_changed_or_ambiguous_icons(self):
        spells = {"ACTIVE": ("Hush", {"@uisprite": "active"}),
                  "PASSIVE": ("Hush", {"@uisprite": "passive"})}
        for names, previous in ((["Hush"], {}), (["Hush (passive)"], {}), (["Hush"], {"Hush": "REMOVED"})):
            with self.subTest(names=names, previous=previous), self.assertRaises(ValueError):
                select_spells(names, previous, spells, {"ACTIVE", "PASSIVE"})

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
