# Apple HID Battery Fix

A small Linux workaround for Apple Bluetooth keyboards, trackpads, and mice
whose battery percentage is shown incorrectly by UPower and GNOME.

For affected devices, Linux reads the status-flags byte from Apple HID input
report `0x90` as the battery percentage. The next byte contains the actual
state of charge. A typical broken report looks like this:

```text
raw report:       90 04 5f
kernel displays:      4%
actual charge:           95%
```

This project detects that exact mismatch and supplies the correct percentage
through the existing kernel `power_supply` entry. GNOME Settings, KDE, and
other UPower clients continue using their normal interfaces.

## Safety properties

- Automatically detects compatible Apple Bluetooth HID devices.
- Does not contain device names, Bluetooth addresses, or user-specific data.
- Only activates when the kernel value exactly matches the report's status
  byte and differs from the real percentage.
- Refuses to replace an unexpected value or a mount owned by another process.
- Automatically becomes a no-op when a fixed kernel reports the right value.
- Portable mode removes its mounts on exit; the uninstaller removes service
  mounts explicitly.
- Does not patch or replace the kernel, UPower, BlueZ, or GNOME.

## Tested hardware

- Magic Keyboard with Touch ID, Bluetooth product ID `004c:029a`
- Magic Trackpad, Bluetooth product ID `004c:0324`

Other Apple Bluetooth HID devices are probed conservatively. Unsupported
devices are skipped if input report `0x90` is absent or does not match the
affected layout.

## Requirements

- Linux with `systemd`, BlueZ, and UPower
- Python 3.10 or newer
- `mount`, `umount`, and `udevadm`
- Root access for `/dev/hidraw*` and file-level bind mounts

The installer targets standard systemd distributions such as Ubuntu, Debian,
Fedora, and Arch Linux. Package names for the requirements vary by
distribution.

## Installation

Clone the repository and run the installer:

```sh
git clone https://github.com/nishantapatil3/apple-hid-battery-fix.git
cd apple-hid-battery-fix
sudo ./install.sh
```

Check the detected devices:

```sh
sudo /usr/local/libexec/apple-hid-battery-override --status
```

Example:

```text
DEVICE                            KERNEL  ACTUAL          STATE
Magic Keyboard                       94%     94%     overridden
Magic Trackpad                       95%     95%     overridden
```

Open or reopen the desktop's Power settings after installation.

## Portable use

The program is a standalone Python file and can run without installation:

```sh
sudo python3 src/apple_hid_battery_override.py --status
sudo python3 src/apple_hid_battery_override.py
```

Keep the second command running. Press `Ctrl+C` to remove its temporary
overrides cleanly.

## Commands

```sh
# Inspect without changing anything
sudo /usr/local/libexec/apple-hid-battery-override --status

# Apply one update
sudo /usr/local/libexec/apple-hid-battery-override --once

# Remove mounts created by this project
sudo /usr/local/libexec/apple-hid-battery-override --cleanup

# Follow service logs
journalctl -u apple-hid-battery-override.service -f
```

## Uninstallation

From the cloned repository:

```sh
sudo ./uninstall.sh
```

The uninstaller stops the service, removes its mounts, deletes the installed
program and unit, and asks UPower to refresh.

## How it works

1. Match Bluetooth HID devices with Apple vendor IDs.
2. Match each HID device to its own `power_supply` entry through sysfs.
3. Query HID input report `0x90` through `HIDIOCGINPUT`.
4. Compare the kernel percentage, status byte, and real percentage.
5. For the precise offset bug only, bind-mount a live value file over the
   affected read-only `capacity` attribute.
6. Emit a scoped udev change event so UPower refreshes immediately.

The service polls active devices once per minute. Disconnected devices are
removed automatically. During a service restart or package upgrade, the last
good values remain mounted to avoid falling back to a known-bad kernel value.
The uninstaller removes them explicitly.

## Upstream status

The underlying HID offset issue is addressed by the proposed Linux patch
[HID: input: read battery capacity from its actual report offset][offset-fix].
Related patches are also improving Apple battery polling over Bluetooth.

This workaround is intended for distributions that have not shipped the
kernel fixes yet.

[offset-fix]: https://patchew.org/linux/20260702192139.114809-1-pepemontfort%40gmail.com/

## Development

The project uses only the Python standard library.

```sh
make lint
make test
```

See [CONTRIBUTING.md](CONTRIBUTING.md) and [SECURITY.md](SECURITY.md).

## License

MIT. See [LICENSE](LICENSE).

Apple, Magic Keyboard, Magic Mouse, and Magic Trackpad are trademarks of
Apple Inc. This project is independent and is not affiliated with Apple.
