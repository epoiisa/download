"""Verify release packaging and the standalone runtime it distributes."""

import shutil
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from download import VERSION
from scripts.build_release import ASSETS, ROOT, build_release


class ReleaseTests(unittest.TestCase):
    def test_archives_contain_only_matching_runtime_and_run_elsewhere(self):
        with tempfile.TemporaryDirectory(prefix="download release [test] ") as directory:
            base = Path(directory)
            output = base / "assets"
            self.assertEqual(build_release(ROOT, output), VERSION)
            self.assertEqual({path.name for path in output.iterdir()}, set(ASSETS))
            for name in ("install.sh", "install.ps1"):
                self.assertEqual((output / name).read_bytes(), (ROOT / name).read_bytes())
            prefix = f"download-{VERSION}/"
            unix = base / "unix runtime"
            unix.mkdir()
            with tarfile.open(output / "download-unix.tar.gz") as archive:
                self.assertEqual(set(archive.getnames()),
                                 {prefix + name for name in ("download", "download.py", "catalogue.json")})
                self.assertEqual(archive.getmember(prefix + "download").mode, 0o755)
                # Extract only known regular files; no version-dependent extractall behaviour.
                for member in archive.getmembers():
                    self.assertTrue(member.isfile())
                    (unix / Path(member.name).name).write_bytes(archive.extractfile(member).read())
            windows = base / "windows runtime"
            windows.mkdir()
            with zipfile.ZipFile(output / "download-windows.zip") as archive:
                self.assertEqual(set(archive.namelist()),
                                 {prefix + name for name in ("download.cmd", "download.py", "catalogue.json")})
                for name in archive.namelist():
                    (windows / Path(name).name).write_bytes(archive.read(name))
            work = base / "other working directory"
            work.mkdir()
            for runtime in (unix, windows):
                for name in ("download.py", "catalogue.json"):
                    self.assertEqual((runtime / name).read_bytes(), (ROOT / name).read_bytes())
                for args, stdin in ((["--version"], ""), (["--help"], ""), ([], "exit\n")):
                    result = subprocess.run([sys.executable, "-B", str(runtime / "download.py")] + args,
                                            cwd=work, input=stdin, text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    if args == ["--version"]:
                        self.assertEqual(result.stdout, f"download {VERSION}\n")
                (runtime / "catalogue.json").unlink()
                result = subprocess.run([sys.executable, "-B", str(runtime / "download.py"), "--version"],
                                        cwd=work, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(list(work.iterdir()))

    def test_mismatched_installer_version_is_rejected_before_packaging(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "source"
            root.mkdir()
            for name in ("download.py", "install.sh", "install.ps1"):
                shutil.copyfile(ROOT / name, root / name)
            for name in ("install.sh", "install.ps1"):
                with self.subTest(installer=name):
                    original = (root / name).read_text(encoding="utf-8")
                    (root / name).write_text(original.replace(VERSION, "99.0.0"), encoding="utf-8")
                    output = Path(directory) / "assets"
                    with self.assertRaisesRegex(ValueError, "does not match"):
                        build_release(root, output)
                    self.assertFalse(output.exists())
                    (root / name).write_text(original, encoding="utf-8")

    def test_build_refuses_to_replace_existing_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            existing = output / "install.sh"
            existing.write_text("preserve me", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "already exists"):
                build_release(ROOT, output)
            self.assertEqual(existing.read_text(encoding="utf-8"), "preserve me")
            self.assertEqual(list(output.iterdir()), [existing])
