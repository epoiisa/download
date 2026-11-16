# Releases

The runtime and catalogue share one version. `download.py` defines `VERSION`; update the version in both installers to match.

Use `vMAJOR.MINOR.PATCH` tags. Increment patch for fixes and catalogue corrections, minor for compatible features or catalogue additions, and major for breaking changes. Record changes in `CHANGELOG.md`.

## Prepare

Run from the repository root:

```bash
python3 -B -m unittest -v
python3 -B scripts/build_release.py /path/to/empty/release-assets
```

The builder produces four assets:

- `install.sh`
- `install.ps1`
- `download-unix.tar.gz`: `download`, `download.py` and `catalogue.json`
- `download-windows.zip`: `download.cmd`, `download.py` and `catalogue.json`

Both archives use the directory `download-VERSION`. Each installer fetches its own version's archive. The README fetches the installer from the latest stable release, so its commands remain the same between releases.

The GitHub verification workflow runs the test suite on Linux, macOS and Windows, including Python 3.8 on Linux, and packages assets only after every job passes. Workflow artifacts are build outputs; they are not published releases. Hosted checks do not cover every native console interaction or real persistent PATH configuration.

## GitHub safeguards

Issues and Discussions remain enabled for bug reports and user conversations. Wiki and Projects are disabled; installation and usage documentation stay in the repository.

GitHub Actions allows GitHub-owned actions only. Workflow tokens default to read-only access and cannot approve pull requests; fork workflows from all outside contributors require maintainer approval. Keep verification and packaging separate from release publication.

Private vulnerability reporting, Dependabot alerts and security updates are enabled. CodeQL uses default setup for Python and GitHub Actions. `.github/dependabot.yml` schedules weekly grouped updates for GitHub Actions; the Python runtime has no third-party dependencies.

The active `main` ruleset blocks deletion and force pushes while allowing normal direct pushes. The release-tag ruleset blocks updates and deletion of `v*` tags without a configured bypass. Pull requests use squash merging, with merged branches deleted automatically; auto-merge remains disabled.

## Publish

Committing, pushing, tagging and publishing require explicit user authorization. Before doing so, verify the effective Git author and signing identity, repository owner `epoiisa`, and the authenticated GitHub account.

1. Review the changes and verification results, then commit and push the approved source.
2. Require successful verification for that exact commit. Use its packaged assets, or build from a clean checkout of the same commit.
3. Create and push a signed `vVERSION` tag pointing to that commit.
4. Create a draft GitHub release for the existing tag. Upload all four assets and add the version's changelog entry and actual platform verification results to its release notes.
5. Verify the tag, notes and complete asset set, then publish as the latest stable release. Do not move a published tag or replace its assets; publish a new version for corrections.
6. Test the published installers in temporary installations from another working directory, including paths with spaces, and verify `download --version`, command, interactive and batch downloads.

The README's latest-release installation links resolve to the current stable release.

## v1.1.0 — published 16 November 2026

Published [v1.1.0](https://github.com/epoiisa/download/releases/tag/v1.1.0) as the latest stable release. The signed tag points to commit [`9acf21e261b6bdfae7a71976792ea3a7d4f807e7`](https://github.com/epoiisa/download/commit/9acf21e261b6bdfae7a71976792ea3a7d4f807e7). All four release assets were published together from the successful [Verify and package run](https://github.com/epoiisa/download/actions/runs/43850203063); both installers are pinned to v1.1.0.

This minor version includes the earlier Waystone correction prepared as v1.0.1. Successful rendering of zero-enchantment Waystones remains unverified; Render Service failures remain normal download failures.

### GitHub verification

All checks passed for the release commit, including [CodeQL for Python and GitHub Actions](https://github.com/epoiisa/download/actions/runs/43850203177). The [verification run triggered by the signed tag](https://github.com/epoiisa/download/actions/runs/43858333151) also passed before publication.

| Environment | Python | PowerShell | Result |
| --- | --- | --- | --- |
| Ubuntu 22.04.5 LTS | 3.8.18 | 7.6.10 | 82 tests: 79 passed, 3 Windows-only skips |
| Ubuntu 26.04.1 LTS | 3.14.10 | 7.6.10 | 82 tests: 79 passed, 3 Windows-only skips |
| macOS 26.7.2 | 3.14.10 | 7.6.9 | 82 tests: 79 passed, 3 Windows-only skips |
| Windows Server 2025 (10.0.26100) | 3.14.10 | 7.6.9 | 82 tests: 72 passed, 10 Unix-only skips |
| Windows Server 2025 (10.0.26100) | 3.14.10 | Windows PowerShell 5.1.26100.33648 | All 5 installer tests passed |

Hosted tests cover the Windows launcher and isolated installer/PATH behaviour. Manual Windows console Ctrl+C/Ctrl+Z and real persistent user PATH checks remain unverified.

### Local verification

Verified on 16 November 2026 using macOS 27.0.1 and Python 3.14.8: 82 tests ran, with 74 passing and 8 skipped. Three native Windows checks and five PowerShell checks were unavailable in this local run; PowerShell was not installed. Linux and Python 3.8 coverage came from GitHub verification above.

A temporary three-file installation from the built Unix release archive passed nine live PNG downloads across command, interactive and UTF-16 BOM/CRLF batch modes, including output replacement with permission preservation and paths with spaces and brackets. All three Firewing Drake icons downloaded successfully. Siphoned Energy, Swiftclaw and an enchanted Dungeon Map also verified identifier exceptions. The drake entries accepted omitted or explicit tier 8, enchantment 0 and quality 1, and rejected unsupported values. The suite validated release packaging and matching version numbers; `download --version` reported `download 1.1.0`.

With user approval on 16 November 2026, the three matching runtime files were installed locally in `~/.local/bin`. The installed `download` reported version 1.1.0 and downloaded Firewing Drake Egg, Tame Firewing Drake and Firewing Drake successfully across command, interactive and UTF-16 BOM/CRLF batch modes from another working directory.

### Verification after publication

After publication on 16 November 2026, all four public downloads matched the verified GitHub Actions asset hashes. The published macOS/Linux installer passed fresh-install and update checks on macOS in isolated directories with spaces and brackets, preserving the existing test profile on update. The installed runtime passed version/help checks and six live PNG downloads across command, interactive and UTF-16 BOM/CRLF batch modes, including all three drake icons, identifier exceptions and output replacement with permission preservation.

The published Windows archive also passed file verification and a direct Python version check on macOS. Native Windows installer coverage is provided by the GitHub verification above.

## v1.0.1 preparation (superseded)

Verified on 16 November 2026 using macOS 27.0.1 and Python 3.9.6: 82 tests ran, with 74 passing and 8 skipped. Three native Windows checks and five PowerShell checks remain unavailable; PowerShell is not installed.

A temporary three-file installation in a path with spaces accepted `Waystone (Large Group) 8 0` in command, interactive and batch modes, reaching the download step with the expected URL and filename. These checks mocked the image download and do not establish successful rendering by Albion. Default and explicit enchantment levels 0–4 resolved correctly at tiers 6–8. The suite also validated release packaging and matching version numbers. This preparation was superseded by v1.1.0; no separate v1.0.1 release was published.

## v1.0.0 verification

Verified locally on 5 October 2026 using macOS 27.0.1:

- Python 3.14.8 and 3.9.6 each ran 82 tests: 74 passed and 8 skipped.
- Three native Windows checks and five PowerShell installer checks were skipped; no PowerShell executable was available in this run.
- A temporary installation from the built Unix release archive passed six live PNG downloads across command, interactive and UTF-16 BOM/CRLF batch modes, including output replacement and paths with spaces and brackets. `download --version` reported `download 1.0.0`.
- Both packaged runtimes passed direct Python version, help and interactive-exit checks from another working directory. Version output also worked with the catalogue removed.
- Shell syntax, workflow YAML parsing and Git whitespace checks passed.

Hosted CI, native Windows, Linux and Python 3.8 remain unverified in this preparation run. The published release URLs cannot be tested until the release is published.
