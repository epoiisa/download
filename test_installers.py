"""Installer regression checks use private directories and local archive fixtures."""

import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent
RUNTIME_FILES = ("download", "download.py", "catalogue.json")
POWERSHELL = os.environ.get("DOWNLOAD_TEST_POWERSHELL") or shutil.which("pwsh") or shutil.which("powershell")


@unittest.skipIf(os.name == "nt", "requires Unix shell tools")
class UnixInstallerTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="download installer ")
        self.addCleanup(directory.cleanup)
        self.sandbox = Path(directory.name)
        self.user_dir = self.sandbox / "user with spaces ' & [brackets]"
        self.user_dir.mkdir()
        self.installed = self.user_dir / ".local" / "bin"
        self.work = self.sandbox / "working directory"
        self.work.mkdir()
        self.fixture = self.sandbox / "source"
        self.fixture.mkdir()
        for name in RUNTIME_FILES:
            shutil.copy2(ROOT / name, self.fixture / name)
        self.archive = self.sandbox / "source.tar.gz"
        self.make_archive()
        self.bin_dir = self.sandbox / "test commands"
        self.bin_dir.mkdir()
        (self.bin_dir / "python3").symlink_to(sys.executable)
        self.write_command("curl", """
import os, shutil, sys
if os.environ.get('DOWNLOAD_TEST_NETWORK_FAIL'):
    print('Simulated download failure', file=sys.stderr)
    sys.exit(22)
shutil.copyfile(os.environ['DOWNLOAD_TEST_ARCHIVE'], sys.argv[sys.argv.index('-o') + 1])
""")
        # Only child processes see the fixture user directory and command shims.
        self.env = dict(os.environ, HOME=str(self.user_dir), SHELL="/bin/zsh",
                        PATH=str(self.bin_dir) + os.pathsep + os.environ["PATH"],
                        DOWNLOAD_TEST_ARCHIVE=str(self.archive), PYTHONDONTWRITEBYTECODE="1")
        self.env.pop("ZDOTDIR", None)
        self.env.pop("XDG_CONFIG_HOME", None)

    def make_archive(self):
        with tarfile.open(self.archive, "w:gz") as archive:
            for name in RUNTIME_FILES:
                archive.add(self.fixture / name, arcname="download-main/" + name)

    def write_command(self, name, code):
        helper = self.bin_dir / (name + ".py")
        helper.write_text(code, encoding="utf-8")
        launcher = self.bin_dir / name
        launcher.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                            shlex.quote(str(helper)) + ' "$@"\n', encoding="utf-8")
        launcher.chmod(0o755)

    def install(self, **overrides):
        # Feed the script through stdin, as in the documented curl | sh command.
        return subprocess.run(["sh"], input=(ROOT / "install.sh").read_text(encoding="utf-8"),
                              cwd=self.work, env=dict(self.env, **overrides),
                              text=True, capture_output=True, timeout=30)

    def assert_success(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for name in RUNTIME_FILES:
            self.assertEqual((self.installed / name).read_bytes(), (self.fixture / name).read_bytes())
        self.assertFalse(list(self.installed.glob(".download-install.*")))

    def snapshot(self):
        return {path.relative_to(self.user_dir): (path.read_bytes(), stat.S_IMODE(path.stat().st_mode))
                for path in self.user_dir.rglob("*") if path.is_file()}

    def test_fresh_install_runs_from_another_directory_with_spaces(self):
        self.assert_success(self.install())
        self.assertEqual(set(path.name for path in self.installed.iterdir()), set(RUNTIME_FILES))
        self.assertTrue(os.access(self.installed / "download", os.X_OK))
        for arguments, stdin, status in ((["--help"], "", 0), ([], "exit\n", 0),
                                         (["Hunter Shoes", "99"], "", 1)):
            result = subprocess.run([str(self.installed / "download")] + arguments, input=stdin,
                                    cwd=self.work, env=self.env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, status, result.stdout + result.stderr)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_update_preserves_profile_content_modes_and_does_not_duplicate_path(self):
        profile = self.user_dir / ".zshrc"
        original = "# Existing setup, without a trailing newline"
        profile.write_text(original, encoding="utf-8")
        profile.chmod(0o640)
        self.assert_success(self.install())
        configured_profile = profile.read_bytes()
        self.assertTrue(configured_profile.startswith(original.encode("utf-8") + b"\n"))
        with (self.fixture / "download.py").open("a", encoding="utf-8") as source:
            source.write("\n# A newer repository snapshot.\n")
        self.make_archive()
        self.assert_success(self.install())
        self.assertEqual(profile.read_bytes(), configured_profile)
        self.assertEqual(stat.S_IMODE(profile.stat().st_mode), 0o640)
        if shutil.which("zsh"):
            result = subprocess.run(["zsh", "-ic", 'source "$HOME/.zshrc"; command -v download'],
                                    cwd=self.work, env=self.env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.strip(), str(self.installed / "download"))

    def test_bash_configures_login_and_interactive_profiles_without_hiding_existing_files(self):
        self.env["SHELL"] = "/bin/bash"
        login_profile = self.user_dir / ".bash_login"
        login_profile.write_text("# Existing login configuration\n", encoding="utf-8")
        self.assert_success(self.install())
        self.assertTrue((self.user_dir / ".bashrc").is_file())
        self.assertFalse((self.user_dir / ".bash_profile").exists())
        self.assertFalse((self.user_dir / ".profile").exists())
        inspector = self.work / "inspect_path.py"
        inspector.write_text("import json, os; print(json.dumps(os.environ['PATH'].split(os.pathsep)))",
                             encoding="utf-8")
        result = subprocess.run(["bash", "-c", '. "$HOME/.bash_login"; . "$HOME/.bashrc"; python3 inspect_path.py'],
                                cwd=self.work, env=self.env, text=True, capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), [str(self.installed)] + self.env["PATH"].split(os.pathsep))

    def test_zsh_honours_custom_startup_directory(self):
        config = self.sandbox / "zsh settings with spaces"
        self.assert_success(self.install(ZDOTDIR=str(config)))
        self.assertTrue((config / ".zshrc").is_file())
        self.assertFalse((self.user_dir / ".zshrc").exists())

    def test_empty_shell_uses_profile(self):
        self.env["SHELL"] = ""
        self.assert_success(self.install())
        self.assertTrue((self.user_dir / ".profile").is_file())

    def test_failed_download_leaves_an_existing_installation_and_profile_untouched(self):
        self.assert_success(self.install())
        before = self.snapshot()
        result = self.install(DOWNLOAD_TEST_NETWORK_FAIL="1")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.installed.glob(".download-install.*")))

    def test_invalid_catalogue_is_rejected_before_replacing_existing_files(self):
        self.assert_success(self.install())
        before = self.snapshot()
        (self.fixture / "catalogue.json").write_text("{}", encoding="utf-8")
        self.make_archive()
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("failed validation", result.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_failed_replacement_restores_all_previous_files_and_permissions(self):
        self.assert_success(self.install())
        (self.installed / "catalogue.json").chmod(0o640)
        before = self.snapshot()
        self.write_command("mv", """
import os, sys
from pathlib import Path
if Path(sys.argv[-2]).parent.name == 'download-main' and Path(sys.argv[-1]).name == 'download.py':
    print('Simulated replacement failure', file=sys.stderr)
    sys.exit(1)
os.execv(REAL_MV, [REAL_MV] + sys.argv[1:])
""".replace("REAL_MV", repr(shutil.which("mv"))))
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertFalse(list(self.installed.glob(".download-install.*")))

    def test_non_file_destination_is_preserved(self):
        self.installed.mkdir(parents=True)
        conflict = self.installed / "download.py"
        conflict.mkdir()
        marker = conflict / "user file.txt"
        marker.write_text("Preserve this directory", encoding="utf-8")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(encoding="utf-8"), "Preserve this directory")
        self.assertFalse((self.installed / "download").exists())
        self.assertFalse((self.user_dir / ".zshrc").exists())

    def test_missing_or_old_python_stops_before_installation(self):
        (self.bin_dir / "python3").unlink()
        self.write_command("python3", "import sys; sys.exit(1)\n")
        result = self.install()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Python 3.8+ is required", result.stderr)
        self.assertFalse(self.installed.exists())


@unittest.skipUnless(POWERSHELL, "requires PowerShell; set DOWNLOAD_TEST_POWERSHELL for a portable runtime")
class PowerShellInstallerTests(unittest.TestCase):
    """Exercise real PowerShell/filesystem logic with network, py and user PATH isolated.

    These checks do not replace native Windows launcher or registry verification.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="download PowerShell installer ")
        self.addCleanup(directory.cleanup)
        self.sandbox = Path(directory.name)
        self.local_data = self.sandbox / "user data with spaces & [brackets]"
        self.installed = self.local_data / "Programs" / "download"
        self.archive = self.sandbox / "source.zip"
        self.make_archive()
        # The local Environment class replaces only the persistent user PATH API.
        # No test reads or writes the actual user's registry or shell profiles.
        harness = r'''
class Environment {
    static [string] $UserPath
    static [int] $Writes = 0
    static [string] GetEnvironmentVariable([string] $name, [string] $target) {
        if ($name -ne 'Path' -or $target -ne 'User') { throw 'Unexpected environment read' }
        return [Environment]::UserPath
    }
    static [void] SetEnvironmentVariable([string] $name, [string] $value, [string] $target) {
        if ($name -ne 'Path' -or $target -ne 'User') { throw 'Unexpected environment write' }
        [Environment]::UserPath = $value
        [Environment]::Writes++
    }
    static [string] ExpandEnvironmentVariables([string] $value) {
        return [System.Environment]::ExpandEnvironmentVariables($value)
    }
}
[Environment]::UserPath = $env:DOWNLOAD_TEST_USER_PATH
function py {
    if ($args[0] -ne '-3') { throw 'Expected the Python 3 launcher' }
    $pythonArguments = @($args | Select-Object -Skip 1)
    if ($MyInvocation.ExpectingInput) {
        $input | & $env:DOWNLOAD_TEST_PYTHON @pythonArguments
    } else {
        & $env:DOWNLOAD_TEST_PYTHON @pythonArguments
    }
    $global:LASTEXITCODE = $LASTEXITCODE
    if ($LASTEXITCODE -eq 0 -and $pythonArguments -contains '-B' -and $env:DOWNLOAD_TEST_BLOCK_REPLACEMENT) {
        # Block the third destination after preflight to exercise rollback of the first two.
        $blocked = Join-Path $env:LOCALAPPDATA 'Programs/download/catalogue.json'
        [IO.File]::Move($blocked, $blocked + '.test-held')
        [IO.Directory]::CreateDirectory($blocked) | Out-Null
    }
}
function Invoke-WebRequest {
    param($Uri, $OutFile, $TimeoutSec, [switch] $UseBasicParsing)
    if ($env:DOWNLOAD_TEST_NETWORK_FAIL) { throw 'Simulated download failure' }
    Copy-Item -LiteralPath $env:DOWNLOAD_TEST_ARCHIVE -Destination $OutFile
}
$testStatus = 0
try {
'''
        harness += (ROOT / "install.ps1").read_text(encoding="utf-8")
        harness += r'''
} catch {
    [Console]::Error.WriteLine($_.ToString())
    $testStatus = 1
}
Write-Output ('TEST_RESULT:' + (@{path = [Environment]::UserPath; writes = [Environment]::Writes} | ConvertTo-Json -Compress))
exit $testStatus
'''
        self.harness = self.sandbox / "test.ps1"
        self.harness.write_text(harness, encoding="utf-8")
        self.env = dict(os.environ, OS="Windows_NT", LOCALAPPDATA=str(self.local_data),
                        DOWNLOAD_TEST_ARCHIVE=str(self.archive), DOWNLOAD_TEST_PYTHON=sys.executable,
                        DOWNLOAD_TEST_USER_PATH="C:\\Existing Tools;C:\\Other Tools", PYTHONDONTWRITEBYTECODE="1")

    def make_archive(self, invalid=False, suffix=b""):
        with zipfile.ZipFile(self.archive, "w") as archive:
            for name in ("download.cmd", "download.py", "catalogue.json"):
                content = (ROOT / name).read_bytes()
                if name == "catalogue.json" and invalid:
                    content = b"{}"
                if name == "download.py":
                    content += suffix
                archive.writestr("download-main/" + name, content)

    def install(self, **overrides):
        result = subprocess.run([POWERSHELL, "-NoLogo", "-NoProfile", "-NonInteractive",
                                 "-Command", self.harness.read_text(encoding="utf-8")],
                                cwd=self.sandbox, env=dict(self.env, **overrides),
                                text=True, capture_output=True, timeout=45)
        states = [line[len("TEST_RESULT:"):] for line in result.stdout.splitlines() if line.startswith("TEST_RESULT:")]
        self.assertEqual(len(states), 1, result.stdout + result.stderr)
        return result, json.loads(states[0])

    def snapshot(self):
        return {path.name: path.read_bytes() for path in self.installed.iterdir() if path.is_file()}

    def test_install_and_update_preserve_existing_path_entries(self):
        result, state = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.snapshot(), {name: (ROOT / name).read_bytes()
                                          for name in ("download.cmd", "download.py", "catalogue.json")})
        self.assertEqual(state, {"path": self.env["DOWNLOAD_TEST_USER_PATH"] + ";" + str(self.installed), "writes": 1})
        self.make_archive(suffix=b"\n# New snapshot\n")
        result, updated = self.install(DOWNLOAD_TEST_USER_PATH=state["path"])
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(updated, dict(state, writes=0))
        self.assertTrue((self.installed / "download.py").read_bytes().endswith(b"# New snapshot\n"))
        self.assertFalse(list(self.installed.glob(".download-install-*")))

    def test_equivalent_existing_path_is_not_added_again(self):
        existing = 'C:\\Existing Tools;"%LOCALAPPDATA%/Programs/download/"'
        result, state = self.install(DOWNLOAD_TEST_USER_PATH=existing)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(state, {"path": existing, "writes": 0})

    def test_failed_download_preserves_existing_files_and_path(self):
        result, state = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = self.snapshot()
        result, failure_state = self.install(DOWNLOAD_TEST_NETWORK_FAIL="1", DOWNLOAD_TEST_USER_PATH=state["path"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(failure_state, dict(state, writes=0))
        self.assertFalse(list(self.installed.glob(".download-install-*")))

    def test_invalid_catalogue_preserves_existing_files_and_path(self):
        result, state = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = self.snapshot()
        self.make_archive(invalid=True)
        result, failure_state = self.install(DOWNLOAD_TEST_USER_PATH=state["path"])
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(failure_state, dict(state, writes=0))

    def test_failed_replacement_restores_the_other_files(self):
        result, state = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        before = self.snapshot()
        self.make_archive(suffix=b"\n# New snapshot\n")
        result, failure_state = self.install(DOWNLOAD_TEST_BLOCK_REPLACEMENT="1", DOWNLOAD_TEST_USER_PATH=state["path"])
        self.assertNotEqual(result.returncode, 0)
        for name in ("download.cmd", "download.py"):
            self.assertEqual((self.installed / name).read_bytes(), before[name])
        self.assertTrue((self.installed / "catalogue.json").is_dir())
        self.assertEqual((self.installed / "catalogue.json.test-held").read_bytes(), before["catalogue.json"])
        self.assertEqual(failure_state, dict(state, writes=0))
        self.assertFalse(list(self.installed.glob(".download-install-*")))


if __name__ == "__main__":
    unittest.main()
