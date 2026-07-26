#!/bin/sh
set -eu

case "$0" in
    */*) test_directory=${0%/*} ;;
    *) test_directory=. ;;
esac
project_root=$(CDPATH= cd -- "$test_directory/.." && pwd)
PATH="${KLIPPER_TEST_TOOLS:+$KLIPPER_TEST_TOOLS:}$project_root/tests/tools:$PATH"
export PATH
if [ -n "${KLIPPER_TEST_TEMP_PARENT:-}" ]; then
    mkdir -p "$KLIPPER_TEST_TEMP_PARENT"
    test_root=$(mktemp -d "$KLIPPER_TEST_TEMP_PARENT/installer.XXXXXX")
else
    test_root=$(mktemp -d)
fi
trap 'rm -rf -- "$test_root"' EXIT INT TERM

fake_home="$test_root/home"
printer_data="$fake_home/printer_data"
moonraker_root="$fake_home/moonraker"
fluidd_root="$fake_home/fluidd"
python_wrapper="$fake_home/moonraker-env/bin/python"

mkdir -p \
    "$printer_data/config" \
    "$moonraker_root/moonraker/components" \
    "$fluidd_root/assets" \
    "$(dirname "$python_wrapper")"

cat > "$printer_data/config/moonraker.conf" <<'EOF'
[server]
host: 0.0.0.0
EOF
printf 'original printer config\n' > "$printer_data/config/printer.cfg"
printf 'original fluidd\n' > "$fluidd_root/index.html"
printf 'original asset\n' > "$fluidd_root/assets/original.js"
cat > "$python_wrapper" <<'EOF'
#!/bin/sh
exec python3 "$@"
EOF
chmod +x "$python_wrapper"

run_installer() {
    KLIPPER_BACKUP_HOME="$fake_home" \
    KLIPPER_BACKUP_PRINTER_DATA="$printer_data" \
    KLIPPER_BACKUP_MOONRAKER_ROOT="$moonraker_root" \
    KLIPPER_BACKUP_FLUIDD_ROOT="$fluidd_root" \
    KLIPPER_BACKUP_MOONRAKER_PYTHON="$python_wrapper" \
    KLIPPER_BACKUP_ALLOW_OFFLINE=1 \
    KLIPPER_BACKUP_NO_RESTART=1 \
    KLIPPER_BACKUP_SKIP_DEPENDENCIES=1 \
        sh "$project_root/install.sh" "$@"
}

run_installer install

test -s "$moonraker_root/moonraker/components/cloud_backup.py"
test -s "$moonraker_root/moonraker/components/cloud_backup_web.py"
grep -q '^\[cloud_backup\]$' "$printer_data/config/moonraker.conf"
find "$fluidd_root/assets" -maxdepth 1 -name 'CloudBackup-*.js' \
    -print -quit | grep . >/dev/null
test -s "$printer_data/cloud_backup/installer/baseline/SHA256SUMS"

printf 'user change after installation\n' >> "$printer_data/config/printer.cfg"
run_installer uninstall

test ! -e "$moonraker_root/moonraker/components/cloud_backup.py"
test ! -e "$moonraker_root/moonraker/components/cloud_backup_web.py"
! grep -q '^\[cloud_backup\]$' "$printer_data/config/moonraker.conf"
grep -q 'user change after installation' "$printer_data/config/printer.cfg"
grep -q 'original fluidd' "$fluidd_root/index.html"
test -s "$printer_data/cloud_backup/installer/state"
grep -q '^STATUS=uninstalled$' "$printer_data/cloud_backup/installer/state"

printf 'installer lifecycle test passed\n'
