"""devkit.system 系统标识检测测试。"""

from __future__ import annotations

import _common  # noqa: F401
import unittest
from unittest import mock

from devkit import system as system_mod
from devkit.system import detect_system


class TestOsName(unittest.TestCase):
    def test_windows_family(self):
        for plat in ("win32", "cygwin", "msys"):
            with mock.patch.object(system_mod.sys, "platform", plat):
                self.assertEqual(system_mod._os_name(), "win", plat)

    def test_darwin(self):
        with mock.patch.object(system_mod.sys, "platform", "darwin"):
            self.assertEqual(system_mod._os_name(), "macos")

    def test_linux_family(self):
        for plat in ("linux", "linux2"):
            with mock.patch.object(system_mod.sys, "platform", plat):
                self.assertEqual(system_mod._os_name(), "linux", plat)

    def test_unknown_passthrough(self):
        with mock.patch.object(system_mod.sys, "platform", "freebsd14"):
            self.assertEqual(system_mod._os_name(), "freebsd14")


class TestArchName(unittest.TestCase):
    def test_known_archs(self):
        cases = {
            "AMD64": "amd64",
            "x86_64": "amd64",
            "x64": "amd64",
            "arm64": "arm64",
            "aarch64": "arm64",
            "x86": "x86",
            "i386": "x86",
            "i686": "x86",
        }
        for machine, want in cases.items():
            with mock.patch.object(system_mod.platform, "machine", return_value=machine):
                self.assertEqual(system_mod._arch_name(), want, machine)

    def test_unknown_arch_lowercased(self):
        with mock.patch.object(system_mod.platform, "machine", return_value="RISCV64"):
            self.assertEqual(system_mod._arch_name(), "riscv64")

    def test_empty_arch(self):
        with mock.patch.object(system_mod.platform, "machine", return_value=""):
            self.assertEqual(system_mod._arch_name(), "unknown")


class TestDetectSystem(unittest.TestCase):
    def test_format(self):
        with mock.patch.object(system_mod.sys, "platform", "linux"), \
                mock.patch.object(system_mod.platform, "machine", return_value="x86_64"):
            self.assertEqual(detect_system(), "linux_amd64")
        with mock.patch.object(system_mod.sys, "platform", "win32"), \
                mock.patch.object(system_mod.platform, "machine", return_value="AMD64"):
            self.assertEqual(detect_system(), "win_amd64")
        with mock.patch.object(system_mod.sys, "platform", "darwin"), \
                mock.patch.object(system_mod.platform, "machine", return_value="arm64"):
            self.assertEqual(detect_system(), "macos_arm64")

    def test_current_machine_has_os_arch(self):
        value = detect_system()
        self.assertRegex(value, r"^[a-z0-9]+_[a-z0-9]+$")


if __name__ == "__main__":
    unittest.main()
