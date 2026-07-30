import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import apple_hid_battery_override as override


class ParsingTests(unittest.TestCase):
    def test_parse_uevent(self):
        parsed = override.parse_uevent(
            "DRIVER=apple\nHID_ID=0005:0000004C:0000029A\nHID_NAME=Magic Keyboard\n"
        )
        self.assertEqual(parsed["DRIVER"], "apple")
        self.assertEqual(parsed["HID_NAME"], "Magic Keyboard")

    def test_parse_hid_id(self):
        self.assertEqual(
            override.parse_hid_id("0005:0000004C:00000324"),
            (0x0005, 0x004C, 0x0324),
        )

    def test_invalid_hid_id(self):
        with self.assertRaises(ValueError):
            override.parse_hid_id("not:a:valid:id")


class ClassificationTests(unittest.TestCase):
    def test_detects_offset_bug(self):
        report = override.HidReport(status=4, percentage=95)
        self.assertIs(
            override.classify(4, report),
            override.Verdict.NEEDS_OVERRIDE,
        )

    def test_detects_fixed_kernel(self):
        report = override.HidReport(status=4, percentage=95)
        self.assertIs(
            override.classify(95, report),
            override.Verdict.KERNEL_CORRECT,
        )

    def test_refuses_unexpected_mismatch(self):
        report = override.HidReport(status=4, percentage=95)
        self.assertIs(
            override.classify(50, report),
            override.Verdict.UNEXPECTED,
        )


class MountInfoTests(unittest.TestCase):
    def test_mountinfo_parser_decodes_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            mountinfo = Path(directory) / "mountinfo"
            mountinfo.write_text(
                "99 24 0:40 /apple-hid-battery/device "
                "/sys/devices/example/capacity rw - tmpfs tmpfs rw\n",
                encoding="utf-8",
            )
            mounts = override.read_mounts(mountinfo)
        self.assertEqual(len(mounts), 1)
        self.assertEqual(mounts[0].root, "/apple-hid-battery/device")
        self.assertEqual(
            mounts[0].mountpoint,
            Path("/sys/devices/example/capacity"),
        )

    def test_decode_mount_field(self):
        self.assertEqual(
            override.decode_mount_field("/path\\040with\\040spaces"),
            "/path with spaces",
        )

    def test_cleanup_preserves_source_for_active_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            source = runtime / "device"
            source.write_text("95\n", encoding="ascii")
            target = root / "capacity"
            os.link(source, target)
            old_runtime = override.RUNTIME_DIR
            override.RUNTIME_DIR = runtime
            try:
                mounts = [override.MountEntry(root="/device", mountpoint=target)]
                with mock.patch.object(override, "read_mounts", return_value=mounts):
                    override.cleanup_orphans({target})
                self.assertTrue(source.exists())
            finally:
                override.RUNTIME_DIR = old_runtime

    def test_cleanup_removes_orphan_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runtime = root / "runtime"
            runtime.mkdir()
            source = runtime / "device"
            source.write_text("95\n", encoding="ascii")
            target = root / "capacity"
            os.link(source, target)
            old_runtime = override.RUNTIME_DIR
            override.RUNTIME_DIR = runtime
            try:
                mounts = [override.MountEntry(root="/device", mountpoint=target)]
                with (
                    mock.patch.object(override, "read_mounts", return_value=mounts),
                    mock.patch.object(override, "unmount_path") as unmount,
                ):
                    override.cleanup_orphans(set())
                unmount.assert_called_once_with(target)
                self.assertFalse(source.exists())
            finally:
                override.RUNTIME_DIR = old_runtime


class DiscoveryTests(unittest.TestCase):
    def test_find_supply_matches_shared_hid_parent(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            old_power_supply = override.SYS_POWER_SUPPLY
            override.SYS_POWER_SUPPLY = root / "power_supply"
            try:
                hid_parent = root / "devices" / "0005:004C:0324.0001"
                hid_parent.mkdir(parents=True)
                hid_link = root / "hid-device"
                hid_link.symlink_to(hid_parent)
                supply = override.SYS_POWER_SUPPLY / "hid-aa:bb-battery"
                supply.mkdir(parents=True)
                (supply / "device").symlink_to(hid_parent)
                self.assertEqual(
                    override.find_supply_for_hid(hid_link),
                    supply,
                )
            finally:
                override.SYS_POWER_SUPPLY = old_power_supply


if __name__ == "__main__":
    unittest.main()
