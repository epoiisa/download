#!/usr/bin/env python3
"""Build GitHub release assets using only the Python standard library."""

import argparse
import ast
import re
import shutil
import tarfile
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ASSETS = ("download-unix.tar.gz", "download-windows.zip", "install.sh", "install.ps1")


def release_version(root):
    tree = ast.parse((root / "download.py").read_text(encoding="utf-8"))
    version = next(ast.literal_eval(node.value) for node in tree.body
                   if isinstance(node, ast.Assign)
                   and any(isinstance(target, ast.Name) and target.id == "VERSION"
                           for target in node.targets))
    if not isinstance(version, str) or not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("VERSION must be MAJOR.MINOR.PATCH")
    for name, pattern in (("install.sh", r"download_version=([^\s]+)"),
                          ("install.ps1", r"\$downloadVersion = '([^']+)'")):
        match = re.search(pattern, (root / name).read_text(encoding="utf-8"))
        if not match or match.group(1) != version:
            raise ValueError(f"{name} version does not match download.py VERSION {version}")
    return version


def build_release(root, output):
    version = release_version(root)
    files = ("download", "download.cmd", "download.py", "catalogue.json", "install.sh", "install.ps1")
    for name in files:
        if (root / name).is_symlink() or not (root / name).is_file():
            raise ValueError(f"Missing regular release source file: {name}")
    output.mkdir(parents=True, exist_ok=True)
    for name in ASSETS:
        if (output / name).exists():
            raise ValueError(f"Release asset already exists: {output / name}")
    prefix = f"download-{version}/"
    with tarfile.open(output / ASSETS[0], "w:gz") as archive:
        for name in ("download", "download.py", "catalogue.json"):
            info = archive.gettarinfo(str(root / name), arcname=prefix + name)
            info.mode = 0o755 if name != "catalogue.json" else 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            with (root / name).open("rb") as source:
                archive.addfile(info, source)
    with zipfile.ZipFile(output / ASSETS[1], "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name in ("download.cmd", "download.py", "catalogue.json"):
            archive.write(root / name, arcname=prefix + name)
    for name in ASSETS[2:]:
        shutil.copyfile(root / name, output / name)
    return version


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path, help="directory for the four release assets")
    args = parser.parse_args()
    try:
        version = build_release(ROOT, args.output)
    except (OSError, ValueError, StopIteration, SyntaxError) as exc:
        parser.exit(1, f"Release build failed: {exc}\n")
    print(f"Built v{version} in {args.output.resolve()}")


if __name__ == "__main__":
    main()
