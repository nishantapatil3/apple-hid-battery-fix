#!/usr/bin/python3
"""Correct affected Apple Bluetooth HID battery percentages for UPower.

Some Linux HID versions query Apple input report 0x90 correctly but read the
status-flags byte as the percentage. The next byte contains the actual state
of charge. This program detects that exact mismatch and bind-mounts a live
percentage file over the affected sysfs capacity attribute.

The workaround is intentionally self-disabling: if the kernel already reports
the same percentage as the raw HID report, no override is mounted.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
import fcntl
import logging
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import threading
from typing import Iterable


VERSION = "1.0.0"
HIDIOCGINPUT_4 = 0xC004480A  # _IOWR('H', 0x0a, 4)
BATTERY_REPORT_ID = 0x90
BLUETOOTH_HID_BUS = 0x0005
APPLE_HID_VENDOR_IDS = {0x004C, 0x05AC}
POLL_SECONDS = 60
DISCOVERY_SECONDS = 5
QUERY_TIMEOUT_SECONDS = 5

SYS_HIDRAW = Path(os.environ.get("APPLE_HID_SYS_HIDRAW", "/sys/class/hidraw"))
SYS_POWER_SUPPLY = Path(
    os.environ.get("APPLE_HID_SYS_POWER_SUPPLY", "/sys/class/power_supply")
)
DEV_ROOT = Path(os.environ.get("APPLE_HID_DEV_ROOT", "/dev"))
RUNTIME_DIR = Path(
    os.environ.get("APPLE_HID_RUNTIME_DIR", "/run/apple-hid-battery")
)
MOUNTINFO = Path(os.environ.get("APPLE_HID_MOUNTINFO", "/proc/self/mountinfo"))
QUERY_LOCK = Path(
    os.environ.get(
        "APPLE_HID_QUERY_LOCK",
        "/run/lock/apple-hid-battery-override.lock",
    )
)

stop_event = threading.Event()


class Verdict(Enum):
    NEEDS_OVERRIDE = "affected"
    KERNEL_CORRECT = "kernel-fixed"
    UNEXPECTED = "unexpected"


@dataclass(frozen=True)
class HidReport:
    status: int
    percentage: int


@dataclass(frozen=True)
class MountEntry:
    root: str
    mountpoint: Path


@dataclass(frozen=True)
class AppleHidDevice:
    name: str
    address: str
    vendor: int
    product: int
    hidraw: Path
    supply: Path

    @property
    def capacity(self) -> Path:
        return self.supply / "capacity"

    @property
    def source(self) -> Path:
        return RUNTIME_DIR / self.supply.name


def parse_uevent(text: str) -> dict[str, str]:
    return {
        key: value
        for line in text.splitlines()
        if "=" in line
        for key, value in (line.split("=", 1),)
    }


def parse_hid_id(value: str) -> tuple[int, int, int]:
    parts = value.split(":")
    if len(parts) != 3:
        raise ValueError(f"invalid HID_ID: {value!r}")
    return tuple(int(part, 16) for part in parts)  # type: ignore[return-value]


def read_int(path: Path) -> int:
    return int(path.read_text(encoding="ascii").strip())


def classify(kernel_percentage: int, report: HidReport) -> Verdict:
    if kernel_percentage == report.percentage:
        return Verdict.KERNEL_CORRECT
    if kernel_percentage == report.status:
        return Verdict.NEEDS_OVERRIDE
    return Verdict.UNEXPECTED


def find_supply_for_hid(hid_device_path: Path) -> Path | None:
    try:
        resolved_hid = hid_device_path.resolve(strict=True)
    except OSError:
        return None

    for supply in SYS_POWER_SUPPLY.glob("hid-*-battery"):
        try:
            if (supply / "device").resolve(strict=True) == resolved_hid:
                return supply
        except OSError:
            continue
    return None


def discover_devices() -> list[AppleHidDevice]:
    devices = []
    for hidraw_class in sorted(SYS_HIDRAW.glob("hidraw*")):
        try:
            properties = parse_uevent(
                (hidraw_class / "device" / "uevent").read_text(encoding="utf-8")
            )
            bus, vendor, product = parse_hid_id(properties["HID_ID"])
        except (KeyError, OSError, UnicodeError, ValueError):
            continue

        if bus != BLUETOOTH_HID_BUS or vendor not in APPLE_HID_VENDOR_IDS:
            continue

        supply = find_supply_for_hid(hidraw_class / "device")
        if supply is None or not (supply / "capacity").exists():
            continue

        devices.append(
            AppleHidDevice(
                name=properties.get("HID_NAME", hidraw_class.name),
                address=properties.get("HID_UNIQ", "unknown"),
                vendor=vendor,
                product=product,
                hidraw=DEV_ROOT / hidraw_class.name,
                supply=supply,
            )
        )
    return devices


def query_timed_out(_signum: int, _frame: object) -> None:
    raise TimeoutError("battery query timed out")


@contextmanager
def query_timeout(seconds: int = QUERY_TIMEOUT_SECONDS):
    old_handler = signal.getsignal(signal.SIGALRM)
    signal.signal(signal.SIGALRM, query_timed_out)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old_handler)


def read_battery_report(hidraw: Path) -> HidReport:
    # BlueZ's UHID transport does not handle concurrent GET_REPORT requests
    # reliably. Serialize queries across the daemon, --status, and --once.
    QUERY_LOCK.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    with query_timeout():
        with QUERY_LOCK.open("a+", encoding="ascii") as query_lock:
            fcntl.flock(query_lock.fileno(), fcntl.LOCK_EX)
            report = bytearray((BATTERY_REPORT_ID, 0, 0, 0))
            descriptor = os.open(hidraw, os.O_RDWR | os.O_CLOEXEC)
            try:
                returned = fcntl.ioctl(descriptor, HIDIOCGINPUT_4, report, True)
            finally:
                os.close(descriptor)

    if returned < 3 or report[0] != BATTERY_REPORT_ID:
        raise OSError(f"invalid report 0x90 response: {report.hex(' ')}")
    if report[2] > 100:
        raise OSError(f"invalid battery percentage: {report[2]}")
    return HidReport(status=report[1], percentage=report[2])


def decode_mount_field(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
    )


def read_mounts(path: Path = MOUNTINFO) -> list[MountEntry]:
    mounts = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return mounts

    for line in lines:
        fields = line.split()
        if len(fields) < 6:
            continue
        mounts.append(
            MountEntry(
                root=decode_mount_field(fields[3]),
                mountpoint=Path(decode_mount_field(fields[4])),
            )
        )
    return mounts


def mount_at(target: Path, mounts: Iterable[MountEntry] | None = None) -> MountEntry | None:
    try:
        resolved = target.resolve(strict=True)
    except OSError:
        return None
    for entry in mounts if mounts is not None else read_mounts():
        if entry.mountpoint == resolved:
            return entry
    return None


def same_file(left: Path, right: Path) -> bool:
    try:
        return os.path.samefile(left, right)
    except OSError:
        return False


def write_live_value(source: Path, percentage: int) -> bool:
    RUNTIME_DIR.mkdir(mode=0o755, parents=True, exist_ok=True)
    previous = None
    try:
        previous = source.read_text(encoding="ascii").strip()
    except OSError:
        pass

    # Truncate in place because a bind mount pins this inode.
    with source.open("w", encoding="ascii") as output:
        output.write(f"{percentage}\n")
    source.chmod(0o644)
    return previous != str(percentage)


def run_quiet(command: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        check=check,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


def trigger_upower(device: AppleHidDevice) -> None:
    udevadm = shutil.which("udevadm")
    if not udevadm:
        return
    run_quiet(
        [udevadm, "trigger", "--action=change", str(device.supply)],
        check=False,
    )


def mount_override(device: AppleHidDevice, percentage: int) -> bool:
    mounts = read_mounts()
    existing = mount_at(device.capacity, mounts)
    owned = existing is not None and same_file(device.source, device.capacity)

    if existing is not None and not owned:
        logging.warning("Skipping %s: capacity has a foreign mount", device.name)
        return False

    changed = write_live_value(device.source, percentage)
    mounted_now = False
    if existing is None:
        mount = shutil.which("mount")
        if not mount:
            raise RuntimeError("mount command not found")
        result = run_quiet(
            [mount, "--bind", str(device.source), str(device.capacity)],
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "bind mount failed")
        mounted_now = True

    if mounted_now or changed:
        logging.info(
            "%s (%04x:%04x): %d%%",
            device.name,
            device.vendor,
            device.product,
            percentage,
        )
        trigger_upower(device)
    return True


def unmount_path(target: Path) -> None:
    umount = shutil.which("umount")
    if not umount:
        logging.warning("Cannot unmount %s: umount command not found", target)
        return
    result = run_quiet([umount, str(target)], check=False)
    if result.returncode != 0:
        logging.warning(
            "Cannot unmount %s: %s",
            target,
            result.stderr.strip() or "unknown error",
        )


def cleanup_orphans(active_targets: set[Path]) -> None:
    mounts = read_mounts()
    if not RUNTIME_DIR.exists():
        return

    for source in RUNTIME_DIR.iterdir():
        if not source.is_file() or source.name.startswith("."):
            continue
        owns_active_mount = any(
            entry.mountpoint in active_targets
            and same_file(source, entry.mountpoint)
            for entry in mounts
        )
        if owns_active_mount:
            continue
        for entry in mounts:
            if same_file(source, entry.mountpoint):
                unmount_path(entry.mountpoint)
        try:
            source.unlink()
        except OSError:
            pass


def update_device(device: AppleHidDevice) -> bool:
    report = read_battery_report(device.hidraw)
    existing = mount_at(device.capacity)
    owned = existing is not None and same_file(device.source, device.capacity)

    if existing is not None and not owned:
        logging.warning("Skipping %s: capacity has a foreign mount", device.name)
        return False

    if not owned:
        kernel_percentage = read_int(device.capacity)
        verdict = classify(kernel_percentage, report)
        if verdict is Verdict.KERNEL_CORRECT:
            logging.debug("%s: kernel already reports %d%%", device.name, report.percentage)
            return False
        if verdict is Verdict.UNEXPECTED:
            logging.warning(
                "Skipping %s: kernel=%d, status=%d, actual=%d",
                device.name,
                kernel_percentage,
                report.status,
                report.percentage,
            )
            return False

    return mount_override(device, report.percentage)


def update_all() -> int:
    devices = discover_devices()
    active_targets = set()
    for device in devices:
        try:
            active_targets.add(device.capacity.resolve(strict=True))
        except OSError:
            continue
    active = 0
    for device in devices:
        try:
            if update_device(device):
                active += 1
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            logging.warning("Cannot update %s: %s", device.name, error)
    cleanup_orphans(active_targets)
    return active


def cleanup_all() -> None:
    cleanup_orphans(set())
    try:
        RUNTIME_DIR.rmdir()
    except OSError:
        pass


def print_status() -> int:
    devices = discover_devices()
    if not devices:
        print("No compatible connected Apple Bluetooth HID devices found.")
        return 0

    print(f"{'DEVICE':32} {'KERNEL':>7} {'ACTUAL':>7} {'STATE':>14}")
    for device in devices:
        try:
            report = read_battery_report(device.hidraw)
            existing = mount_at(device.capacity)
            owned = existing is not None and same_file(device.source, device.capacity)
            kernel = read_int(device.capacity)
            if owned:
                state = "overridden"
            else:
                state = classify(kernel, report).value
            print(
                f"{device.name[:32]:32} {kernel:6d}% {report.percentage:6d}% {state:>14}"
            )
        except OSError as error:
            print(f"{device.name[:32]:32} {'-':>7} {'-':>7} {'error':>14}")
            logging.debug("%s: %s", device.name, error)
    return 0


def stop(*_args: object) -> None:
    stop_event.set()


def require_root() -> None:
    if os.geteuid() != 0:
        raise SystemExit("This operation requires root privileges.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Correct affected Apple Bluetooth HID battery percentages."
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--once",
        action="store_true",
        help="detect and update devices once, then exit",
    )
    action.add_argument(
        "--status",
        action="store_true",
        help="show kernel and raw HID percentages without changing anything",
    )
    action.add_argument(
        "--cleanup",
        action="store_true",
        help="remove mounts created by this program",
    )
    parser.add_argument(
        "--keep-on-exit",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
        stream=sys.stdout,
    )

    require_root()
    if args.status:
        return print_status()
    if args.cleanup:
        cleanup_all()
        return 0
    if args.once:
        update_all()
        return 0

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    active = update_all()
    delay = POLL_SECONDS if active else DISCOVERY_SECONDS
    while not stop_event.wait(delay):
        active = update_all()
        delay = POLL_SECONDS if active else DISCOVERY_SECONDS

    if not args.keep_on_exit:
        cleanup_all()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
