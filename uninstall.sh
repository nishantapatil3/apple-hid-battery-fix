#!/bin/sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
    echo "Run this uninstaller as root: sudo ./uninstall.sh" >&2
    exit 1
fi

systemctl disable --now apple-hid-battery-override.service 2>/dev/null || true

program=/usr/local/libexec/apple-hid-battery-override
unit=/etc/systemd/system/apple-hid-battery-override.service

if [ -x "$program" ]; then
    "$program" --cleanup || true
fi

rm -f "$unit" "$program"
systemctl daemon-reload

if systemctl list-unit-files upower.service >/dev/null 2>&1; then
    systemctl try-restart upower.service
fi

echo "Removed Apple HID battery override."
