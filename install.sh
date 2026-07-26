#!/bin/sh
set -eu

PROJECT_VERSION="0.1.0-alpha"
REPOSITORY_URL="https://github.com/Luomo520/Klipper-config-autobackup.git"
SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PAYLOAD_DIR="$SCRIPT_DIR/payload"
CONFIG_TOOL="$SCRIPT_DIR/scripts/configure_moonraker.py"

log() {
    printf '[Klipper-config-autobackup] %s\n' "$*"
}

warn() {
    printf '[Klipper-config-autobackup] WARNING: %s\n' "$*" >&2
}

die() {
    printf '[Klipper-config-autobackup] ERROR: %s\n' "$*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Klipper-config-autobackup installer

Usage:
  ./install.sh install [--with-web-login] [--skip-bypy]
  ./install.sh update  [--with-web-login] [--skip-bypy]
  ./install.sh uninstall
  ./install.sh status

Commands:
  install     Back up the current installation, then install this repository.
  update      Run git pull --ff-only, then perform a backed-up installation.
  uninstall   Back up current state and restore the first-install baseline.
  status      Show paths, installed hashes, service state, and API state.

Options:
  --with-web-login  Install Playwright and Chromium for Baidu password login.
  --skip-bypy       Do not install the recommended lightweight bypy client.

Path overrides:
  KLIPPER_BACKUP_HOME
  KLIPPER_BACKUP_PRINTER_DATA
  KLIPPER_BACKUP_MOONRAKER_ROOT
  KLIPPER_BACKUP_MOONRAKER_CONFIG
  KLIPPER_BACKUP_FLUIDD_ROOT
  KLIPPER_BACKUP_MOONRAKER_PYTHON

Advanced controls:
  KLIPPER_BACKUP_ALLOW_OFFLINE=1   Permit installation when Moonraker is offline.
  KLIPPER_BACKUP_NO_RESTART=1      Do not restart Moonraker (testing only).
  KLIPPER_BACKUP_SKIP_DEPENDENCIES=1
EOF
}

action=${1:-help}
if [ "$#" -gt 0 ]; then
    shift
fi

with_web_login=${KLIPPER_BACKUP_WITH_WEB_LOGIN:-0}
skip_bypy=${KLIPPER_BACKUP_SKIP_BYPY:-0}
while [ "$#" -gt 0 ]; do
    case "$1" in
        --with-web-login) with_web_login=1 ;;
        --skip-bypy) skip_bypy=1 ;;
        -h|--help) usage; exit 0 ;;
        *) die "Unknown option: $1" ;;
    esac
    shift
done

target_home=${KLIPPER_BACKUP_HOME:-$HOME}
printer_data=${KLIPPER_BACKUP_PRINTER_DATA:-$target_home/printer_data}
moonraker_root=${KLIPPER_BACKUP_MOONRAKER_ROOT:-$target_home/moonraker}
moonraker_components="$moonraker_root/moonraker/components"
moonraker_config=${KLIPPER_BACKUP_MOONRAKER_CONFIG:-$printer_data/config/moonraker.conf}
moonraker_python=${KLIPPER_BACKUP_MOONRAKER_PYTHON:-$target_home/moonraker-env/bin/python}
installer_root="$printer_data/cloud_backup/installer"
baseline_dir="$installer_root/baseline"
backup_root="$installer_root/backups"
state_file="$installer_root/state"

detect_fluidd_root() {
    if [ -n "${KLIPPER_BACKUP_FLUIDD_ROOT:-}" ]; then
        printf '%s\n' "$KLIPPER_BACKUP_FLUIDD_ROOT"
        return
    fi
    for candidate in "$target_home/fluidd" /usr/share/fluidd /var/www/fluidd; do
        if [ -f "$candidate/index.html" ]; then
            printf '%s\n' "$candidate"
            return
        fi
    done
    die "Fluidd web root was not found. Set KLIPPER_BACKUP_FLUIDD_ROOT."
}

fluidd_root=$(detect_fluidd_root)

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command is missing: $1"
}

require_common_tools() {
    for command_name in python3 tar sha256sum find sort xargs grep cp mv chmod curl; do
        require_command "$command_name"
    done
}

verify_paths() {
    [ -d "$printer_data/config" ] || die "Printer config directory not found: $printer_data/config"
    [ -f "$moonraker_config" ] || die "moonraker.conf not found: $moonraker_config"
    [ -d "$moonraker_components" ] || die "Moonraker components not found: $moonraker_components"
    [ -x "$moonraker_python" ] || die "Moonraker Python not executable: $moonraker_python"
    [ -f "$fluidd_root/index.html" ] || die "Fluidd index.html not found: $fluidd_root"
    [ -w "$moonraker_components" ] || die "Moonraker components directory is not writable"
    [ -w "$moonraker_config" ] || die "moonraker.conf is not writable"
    [ -w "$fluidd_root" ] || die "Fluidd root is not writable"
}

verify_payload() {
    [ -f "$PAYLOAD_DIR/SHA256SUMS" ] || die "Payload checksum file is missing"
    (
        cd "$PAYLOAD_DIR"
        sha256sum -c SHA256SUMS
    ) || die "Payload SHA-256 verification failed"
    [ -s "$PAYLOAD_DIR/moonraker/cloud_backup.py" ] || die "cloud_backup.py is missing"
    [ -s "$PAYLOAD_DIR/moonraker/cloud_backup_web.py" ] || die "cloud_backup_web.py is missing"
    [ -s "$PAYLOAD_DIR/fluidd/fluidd-dist.tar.gz" ] || die "Fluidd payload is missing"
}

query_print_state() {
    curl --max-time 5 -fsS \
        'http://127.0.0.1:7125/printer/objects/query?print_stats' |
        python3 -c 'import json,sys; print(json.load(sys.stdin)["result"]["status"]["print_stats"]["state"])'
}

require_safe_runtime() {
    if print_state=$(query_print_state 2>/dev/null); then
        case "$print_state" in
            standby|complete|error|cancelled) ;;
            *) die "Refusing to change files while print_stats is $print_state" ;;
        esac
        log "Printer state: $print_state"
    elif [ "${KLIPPER_BACKUP_ALLOW_OFFLINE:-0}" = 1 ]; then
        warn "Moonraker is offline; print-state protection was explicitly bypassed"
    else
        die "Cannot query print_stats. Start Moonraker or set KLIPPER_BACKUP_ALLOW_OFFLINE=1."
    fi

    status_json=$(curl --max-time 5 -fsS \
        http://127.0.0.1:7125/server/cloud_backup/status 2>/dev/null || true)
    if [ -n "$status_json" ]; then
        printf '%s' "$status_json" | python3 -c \
            'import json,sys; data=json.load(sys.stdin)["result"]; raise SystemExit(data.get("active_job") is not None or data.get("download_in_progress", False))' \
            || die "A cloud backup upload or download is currently active"
    fi
}

write_checksums() {
    destination=$1
    (
        cd "$destination"
        find . -type f ! -name SHA256SUMS -print0 |
            sort -z |
            xargs -0 sha256sum > SHA256SUMS
    )
}

snapshot_installation() {
    destination=$1
    label=$2
    [ ! -e "$destination" ] || die "Backup destination already exists: $destination"
    mkdir -p "$destination/moonraker-components"
    chmod 700 "$destination"
    cp -a "$printer_data/config" "$destination/printer_data-config"
    cp -a "$fluidd_root" "$destination/fluidd"
    for component in cloud_backup.py cloud_backup_web.py; do
        if [ -f "$moonraker_components/$component" ]; then
            cp -a "$moonraker_components/$component" \
                "$destination/moonraker-components/$component"
        else
            : > "$destination/moonraker-components/$component.absent"
        fi
    done
    cat > "$destination/BACKUP_INFO.txt" <<EOF
Klipper-config-autobackup installer backup
Time: $(date '+%Y-%m-%d %H:%M:%S %z')
Stage: $label
Project version: $PROJECT_VERSION
Printer config: $printer_data/config
Moonraker components: $moonraker_components
Moonraker config: $moonraker_config
Fluidd root: $fluidd_root
Purpose: restore files after a failed install/update/uninstall operation.
EOF
    write_checksums "$destination"
    log "Backup created: $destination" >&2
}

baseline_created=0
ensure_baseline() {
    if [ -f "$baseline_dir/SHA256SUMS" ]; then
        return
    fi
    [ ! -e "$baseline_dir" ] || \
        die "The uninstall baseline exists but is incomplete: $baseline_dir"
    mkdir -p "$installer_root"
    temporary_baseline="$installer_root/.baseline-$(date +%Y%m%d_%H%M%S)-$$"
    snapshot_installation "$temporary_baseline" first-install-baseline
    mv "$temporary_baseline" "$baseline_dir"
    baseline_created=1
    log "Permanent uninstall baseline created: $baseline_dir" >&2
}

prepare_change_backup() {
    label=$1
    ensure_baseline
    if [ "$baseline_created" = 1 ]; then
        printf '%s\n' "$baseline_dir"
        return
    fi
    stamp=$(date +%Y%m%d_%H%M%S)
    destination="$backup_root/$stamp-$label"
    mkdir -p "$backup_root"
    snapshot_installation "$destination" "$label"
    printf '%s\n' "$destination"
}

restore_components() {
    source_dir=$1
    for component in cloud_backup.py cloud_backup_web.py; do
        if [ -f "$source_dir/moonraker-components/$component" ]; then
            cp -a "$source_dir/moonraker-components/$component" \
                "$moonraker_components/$component"
        elif [ -f "$source_dir/moonraker-components/$component.absent" ]; then
            rm -f "$moonraker_components/$component"
        else
            die "Backup is missing component state: $component"
        fi
    done
}

safe_remove_tree() {
    path=$1
    case "$path" in
        *klipper-config-autobackup-new-*|*klipper-config-autobackup-old-*|*klipper-config-autobackup-restore-*)
            [ ! -e "$path" ] || rm -rf -- "$path"
            ;;
        *) die "Refusing to remove unexpected path: $path" ;;
    esac
}

restart_moonraker() {
    if [ "${KLIPPER_BACKUP_NO_RESTART:-0}" = 1 ]; then
        warn "Moonraker restart was skipped by KLIPPER_BACKUP_NO_RESTART=1"
        return
    fi
    curl --max-time 5 -fsS -X POST \
        'http://127.0.0.1:7125/machine/services/restart?service=moonraker' \
        >/dev/null 2>&1 || true
}

wait_for_moonraker() {
    endpoint=$1
    if [ "${KLIPPER_BACKUP_NO_RESTART:-0}" = 1 ]; then
        return 0
    fi
    attempt=0
    while [ "$attempt" -lt 60 ]; do
        attempt=$((attempt + 1))
        if curl --max-time 3 -fsS "$endpoint" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

install_bypy() {
    if [ "$skip_bypy" = 1 ] || [ "${KLIPPER_BACKUP_SKIP_DEPENDENCIES:-0}" = 1 ]; then
        warn "bypy dependency installation was skipped"
        return
    fi
    bypy_env="$printer_data/cloud_backup/bypy-env"
    if [ -x "$bypy_env/bin/bypy" ]; then
        log "bypy is already installed: $bypy_env"
        return
    fi
    log "Installing lightweight bypy client"
    python3 -m venv "$bypy_env" || die "python3-venv is required to install bypy"
    "$bypy_env/bin/pip" install --disable-pip-version-check 'bypy==1.8.9'
}

install_web_login() {
    if [ "$with_web_login" != 1 ]; then
        return
    fi
    if [ "${KLIPPER_BACKUP_SKIP_DEPENDENCIES:-0}" = 1 ]; then
        warn "Web-login dependencies were skipped"
        return
    fi
    log "Installing optional Playwright and Chromium support"
    "$moonraker_python" -m pip install --disable-pip-version-check \
        'playwright>=1.40,<2'
    "$moonraker_python" -m playwright install chromium
}

write_state() {
    status=$1
    mkdir -p "$installer_root"
    temporary_state="$installer_root/.state-$$"
    cat > "$temporary_state" <<EOF
STATUS=$status
VERSION=$PROJECT_VERSION
UPDATED_AT=$(date '+%Y-%m-%dT%H:%M:%S%z')
REPOSITORY=$REPOSITORY_URL
MOONRAKER_COMPONENTS=$moonraker_components
MOONRAKER_CONFIG=$moonraker_config
FLUIDD_ROOT=$fluidd_root
BASELINE=$baseline_dir
EOF
    chmod 600 "$temporary_state"
    mv "$temporary_state" "$state_file"
}

rollback_install() {
    backup_dir=$1
    old_fluidd=$2
    warn "Restoring pre-change files from $backup_dir"
    cp -a "$backup_dir/printer_data-config/moonraker.conf" "$moonraker_config"
    restore_components "$backup_dir"
    if [ -d "$old_fluidd" ]; then
        failed_fluidd="$backup_dir/failed-fluidd-$(date +%Y%m%d_%H%M%S)"
        [ ! -e "$failed_fluidd" ] || die "Rollback destination already exists"
        if [ -d "$fluidd_root" ]; then
            mv "$fluidd_root" "$failed_fluidd"
        fi
        mv "$old_fluidd" "$fluidd_root"
        chmod -R a+rX "$fluidd_root"
    fi
    restart_moonraker
    wait_for_moonraker 'http://127.0.0.1:7125/server/info' || true
}

perform_install() {
    require_common_tools
    verify_paths
    verify_payload
    require_safe_runtime
    change_label=before-install
    if [ -f "$state_file" ]; then
        change_label=before-update
    fi
    change_backup=$(prepare_change_backup "$change_label")
    install_bypy
    install_web_login

    stamp=$(date +%Y%m%d_%H%M%S)
    fluidd_new="${fluidd_root}.klipper-config-autobackup-new-$stamp"
    fluidd_old="${fluidd_root}.klipper-config-autobackup-old-$stamp"
    [ ! -e "$fluidd_new" ] || die "Temporary Fluidd path already exists"
    [ ! -e "$fluidd_old" ] || die "Previous Fluidd path already exists"
    mkdir -p "$fluidd_new"
    tar -xzf "$PAYLOAD_DIR/fluidd/fluidd-dist.tar.gz" -C "$fluidd_new"
    [ -s "$fluidd_new/index.html" ] || die "Fluidd payload does not contain index.html"
    find "$fluidd_new/assets" -maxdepth 1 -name 'CloudBackup-*.js' -print -quit |
        grep . >/dev/null || die "Fluidd payload does not contain CloudBackup assets"
    chmod -R a+rX "$fluidd_new"

    cp "$PAYLOAD_DIR/moonraker/cloud_backup.py" \
        "$moonraker_components/cloud_backup.py.new"
    cp "$PAYLOAD_DIR/moonraker/cloud_backup_web.py" \
        "$moonraker_components/cloud_backup_web.py.new"
    "$moonraker_python" -m py_compile \
        "$moonraker_components/cloud_backup.py.new" \
        "$moonraker_components/cloud_backup_web.py.new"

    python3 "$CONFIG_TOOL" ensure "$moonraker_config"
    mv "$moonraker_components/cloud_backup.py.new" \
        "$moonraker_components/cloud_backup.py"
    mv "$moonraker_components/cloud_backup_web.py.new" \
        "$moonraker_components/cloud_backup_web.py"

    mv "$fluidd_root" "$fluidd_old"
    if ! mv "$fluidd_new" "$fluidd_root"; then
        mv "$fluidd_old" "$fluidd_root"
        rollback_install "$change_backup" "$fluidd_old"
        die "Could not activate the new Fluidd build"
    fi
    chmod -R a+rX "$fluidd_root"

    restart_moonraker
    if ! wait_for_moonraker \
        'http://127.0.0.1:7125/server/cloud_backup/status'; then
        rollback_install "$change_backup" "$fluidd_old"
        die "Moonraker or cloud_backup API did not become ready; rollback completed"
    fi

    safe_remove_tree "$fluidd_old"
    write_state installed
    log "Installation complete: $PROJECT_VERSION"
    log "Pre-change backup: $change_backup"
    log "Fluidd versions are not restricted by this installer"
}

restore_baseline_fluidd() {
    destination=$1
    restore_dir="${fluidd_root}.klipper-config-autobackup-restore-$(date +%Y%m%d_%H%M%S)"
    old_dir="${fluidd_root}.klipper-config-autobackup-old-$(date +%Y%m%d_%H%M%S)-$$"
    [ ! -e "$restore_dir" ] || die "Restore staging path already exists"
    cp -a "$baseline_dir/fluidd" "$restore_dir"
    chmod -R a+rX "$restore_dir"
    mv "$fluidd_root" "$old_dir"
    if ! mv "$restore_dir" "$fluidd_root"; then
        mv "$old_dir" "$fluidd_root"
        die "Could not activate baseline Fluidd"
    fi
    printf '%s\n' "$old_dir" > "$destination/old-fluidd-path"
}

perform_uninstall() {
    require_common_tools
    verify_paths
    require_safe_runtime
    [ -f "$baseline_dir/SHA256SUMS" ] || \
        die "First-install baseline is missing; refusing an unsafe uninstall"
    uninstall_backup=$(prepare_change_backup before-uninstall)
    baseline_config="$baseline_dir/printer_data-config/moonraker.conf"
    python3 "$CONFIG_TOOL" restore-section "$moonraker_config" "$baseline_config"
    restore_components "$baseline_dir"
    restore_baseline_fluidd "$uninstall_backup"
    old_fluidd=$(cat "$uninstall_backup/old-fluidd-path")

    restart_moonraker
    if ! wait_for_moonraker 'http://127.0.0.1:7125/server/info'; then
        rollback_install "$uninstall_backup" "$old_fluidd"
        die "Moonraker did not recover after uninstall; rollback completed"
    fi
    safe_remove_tree "$old_fluidd"
    write_state uninstalled
    log "Uninstall complete"
    log "Pre-uninstall backup: $uninstall_backup"
    log "Cloud backup data and credentials were preserved in $printer_data/cloud_backup"
}

perform_update() {
    require_command git
    git -C "$SCRIPT_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1 || \
        die "Update requires a git clone of $REPOSITORY_URL"
    git -C "$SCRIPT_DIR" diff --quiet || die "Repository has uncommitted changes"
    git -C "$SCRIPT_DIR" diff --cached --quiet || die "Repository has staged changes"
    log "Downloading the latest source with git"
    git -C "$SCRIPT_DIR" pull --ff-only
    KLIPPER_BACKUP_WITH_WEB_LOGIN=$with_web_login \
    KLIPPER_BACKUP_SKIP_BYPY=$skip_bypy \
        exec "$SCRIPT_DIR/install.sh" install
}

perform_status() {
    printf 'Project version: %s\n' "$PROJECT_VERSION"
    printf 'Repository: %s\n' "$REPOSITORY_URL"
    printf 'Moonraker components: %s\n' "$moonraker_components"
    printf 'Moonraker config: %s\n' "$moonraker_config"
    printf 'Fluidd root: %s\n' "$fluidd_root"
    printf 'Baseline: %s\n' "$baseline_dir"
    if [ -f "$state_file" ]; then
        cat "$state_file"
    else
        printf 'STATUS=not-installed-by-this-script\n'
    fi
    for component in cloud_backup.py cloud_backup_web.py; do
        if [ -f "$moonraker_components/$component" ]; then
            sha256sum "$moonraker_components/$component"
        else
            printf 'missing: %s\n' "$moonraker_components/$component"
        fi
    done
    if [ -f "$moonraker_config" ] && \
       grep -q '^\[cloud_backup\][[:space:]]*$' "$moonraker_config"; then
        printf 'CONFIG_SECTION=present\n'
    else
        printf 'CONFIG_SECTION=absent\n'
    fi
    if find "$fluidd_root/assets" -maxdepth 1 -name 'CloudBackup-*.js' \
        -print -quit 2>/dev/null | grep . >/dev/null; then
        printf 'FLUIDD_MODULE=present\n'
    else
        printf 'FLUIDD_MODULE=absent\n'
    fi
    curl --max-time 5 -fsS \
        http://127.0.0.1:7125/server/cloud_backup/status 2>/dev/null || true
    printf '\n'
}

case "$action" in
    install) perform_install ;;
    update) perform_update ;;
    uninstall) perform_uninstall ;;
    status) perform_status ;;
    help|-h|--help) usage ;;
    *) usage; die "Unknown command: $action" ;;
esac
