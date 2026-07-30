#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this installer as root: sudo ./install.sh" >&2
    exit 1
fi

for command in python3 mount umount udevadm systemctl install; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 1
    fi
done

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
program=/usr/local/libexec/apple-hid-battery-override
unit=/etc/systemd/system/apple-hid-battery-override.service

install -D -m 0755 "$script_dir/src/apple_hid_battery_override.py" "$program"
install -D -m 0644 "$script_dir/systemd/apple-hid-battery-override.service" "$unit"

systemctl daemon-reload
systemctl enable apple-hid-battery-override.service
systemctl restart apple-hid-battery-override.service

echo
echo "Installed apple-hid-battery-override.service"
echo "Check detected devices with:"
echo "  sudo $program --status"
