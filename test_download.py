import contextlib
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


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return b"png"


class DownloadTests(unittest.TestCase):
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
                '"Refreshing Sprint"\n"Guardian Armor" 6 1 4\n', encoding="utf-8"
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
                "https://render.albiononline.com/v1/spell/Refreshing%20Sprint.png",
                "https://render.albiononline.com/v1/item/T6_ARMOR_PLATE_SET3@1.png?quality=4",
            },
        )

    def test_failed_batch_line_is_preserved_verbatim(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.txt"
            original = '  "Foo, Bar"  \n'
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
            original = '"First Spell"\n"unterminated\n"Second Spell"\n'
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


if __name__ == "__main__":
    unittest.main()
