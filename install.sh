#!/bin/sh
# Install or update download for the current user; no sudo is needed.
# Manual equivalent: put download, download.py and catalogue.json together in
# ~/.local/bin, make download executable, and add ~/.local/bin to your PATH.
# Python 3.8+ must already be available as python3.

# Read the complete function before doing anything when piped from curl.
download_install() (
    set -eu

    if ! command -v python3 >/dev/null 2>&1 ||
       ! python3 -c 'import sys; sys.exit(sys.version_info < (3, 8))'; then
        printf '%s\n' 'Python 3.8+ is required: https://www.python.org/downloads/' >&2
        exit 1
    fi
    for download_tool in curl tar; do
        command -v "$download_tool" >/dev/null 2>&1 || {
            printf 'Required command is missing: %s\n' "$download_tool" >&2
            exit 1
        }
    done

    download_shell=${SHELL:-/bin/sh}
    download_shell=${download_shell##*/}
    case "$download_shell" in
        zsh) download_profile="${ZDOTDIR:-$HOME}/.zshrc" ;;
        bash)
            download_profile="$HOME/.profile"
            for download_candidate in "$HOME/.bash_profile" "$HOME/.bash_login"; do
                if [ -f "$download_candidate" ]; then
                    download_profile=$download_candidate
                    break
                fi
            done
            ;;
        sh|dash|ksh) download_profile="$HOME/.profile" ;;
        fish) download_profile="${XDG_CONFIG_HOME:-$HOME/.config}/fish/config.fish" ;;
        *)
            printf 'Unsupported login shell: %s. Use bash, zsh, sh, dash, ksh or fish.\n' "$download_shell" >&2
            exit 1
            ;;
    esac

    download_dir="$HOME/.local/bin"
    mkdir -p "$download_dir"
    # Staging beside the destination keeps each file replacement on one volume.
    download_stage=$(mktemp -d "$download_dir/.download-install.XXXXXX")
    download_changed=
    download_complete=false
    download_cleanup() {
        download_status=$?
        trap - 0 HUP INT TERM
        set +e
        download_recovery_failed=false
        if [ "$download_complete" = false ]; then
            for download_name in $download_changed; do
                if [ -f "$download_stage/backup/$download_name" ]; then
                    mv -f "$download_stage/backup/$download_name" "$download_dir/$download_name" || download_recovery_failed=true
                else
                    rm -f "$download_dir/$download_name" || download_recovery_failed=true
                fi
            done
        fi
        if [ "$download_recovery_failed" = true ]; then
            printf 'Could not restore the previous installation. Recovery files: %s\n' "$download_stage" >&2
            exit 1
        fi
        rm -rf "$download_stage"
        exit "$download_status"
    }
    trap download_cleanup 0
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    # One archive supplies matching code and catalogue, even if main changes.
    printf '%s\n' 'Downloading download...'
    curl -fsSL --connect-timeout 15 --max-time 120 --retry 2 \
        https://github.com/epoiisa/download/archive/refs/heads/main.tar.gz \
        -o "$download_stage/source.tar.gz"
    tar -xzf "$download_stage/source.tar.gz" -C "$download_stage" \
        download-main/download download-main/download.py download-main/catalogue.json
    download_source="$download_stage/download-main"
    for download_name in download download.py catalogue.json; do
        if [ ! -f "$download_source/$download_name" ] || [ -L "$download_source/$download_name" ]; then
            printf 'Archive is missing a regular file: %s\n' "$download_name" >&2
            exit 1
        fi
    done
    chmod 755 "$download_source/download" "$download_source/download.py"
    # Empty input validates startup and the catalogue without downloading icons.
    if ! python3 -B "$download_source/download" </dev/null; then
        printf '%s\n' 'The downloaded runtime failed validation; installation was not changed.' >&2
        exit 1
    fi

    # Back up existing files first, then replace each file with a rename.
    mkdir "$download_stage/backup"
    for download_name in download download.py catalogue.json; do
        download_target="$download_dir/$download_name"
        if [ -L "$download_target" ] || { [ -e "$download_target" ] && [ ! -f "$download_target" ]; }; then
            printf 'Installation target is not a regular file: %s\n' "$download_target" >&2
            exit 1
        fi
        if [ -f "$download_target" ]; then
            cp -p "$download_target" "$download_stage/backup/$download_name"
        fi
    done
    for download_name in download download.py catalogue.json; do
        download_changed="$download_changed $download_name"
        mv -f "$download_source/$download_name" "$download_dir/$download_name"
    done
    download_complete=true

    download_add_path() {
        if [ -f "$1" ] && grep -Fqx '# download: user command directory' "$1"; then
            return
        fi
        mkdir -p "$(dirname "$1")"
        # Append without rewriting the user's existing startup commands.
        if [ "$download_shell" = fish ]; then
            cat >>"$1" <<'FISH'

# download: user command directory
if not contains -- "$HOME/.local/bin" $PATH
    set -gx PATH "$HOME/.local/bin" $PATH
end
FISH
        else
            cat >>"$1" <<'PROFILE'

# download: user command directory
case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
PROFILE
        fi
        printf 'Updated PATH setup in %s\n' "$1"
    }
    download_add_path "$download_profile"
    # Bash uses different files for login and non-login interactive terminals.
    if [ "$download_shell" = bash ]; then
        download_add_path "$HOME/.bashrc"
    fi
    printf 'Installed download in %s\n' "$download_dir"
    printf '%s\n' 'Reopen your terminal, then run: download --help'
)

download_install
