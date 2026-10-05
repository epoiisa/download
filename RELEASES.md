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

The latest-release installation links become usable after the first release is published.

## v1.0.0 verification

Verified locally on 5 October 2026 using macOS 27.0.1:

- Python 3.14.8 and 3.9.6 each ran 82 tests: 74 passed and 8 skipped.
- Three native Windows checks and five PowerShell installer checks were skipped; no PowerShell executable was available in this run.
- A temporary installation from the built Unix release archive passed six live PNG downloads across command, interactive and UTF-16 BOM/CRLF batch modes, including output replacement and paths with spaces and brackets. `download --version` reported `download 1.0.0`.
- Both packaged runtimes passed direct Python version, help and interactive-exit checks from another working directory. Version output also worked with the catalogue removed.
- Shell syntax, workflow YAML parsing and Git whitespace checks passed.

Hosted CI, native Windows, Linux and Python 3.8 remain unverified in this preparation run. The published release URLs cannot be tested until the release is published.
