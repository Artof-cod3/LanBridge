"""Doctor: the address-plan rules and adapter checks, driven by a fake snapshot."""

import unittest

from lanbridge import doctor
from lanbridge.settings import Settings


def snap(**over):
    base = {
        "platform": "Test",
        "os": "test",
        "admin": False,
        "python": "3.12",
        "interfaces": [],
        "virtual_devices": [],
        "routes": [],
        "processes": [],
        "tools": {},
        "forwarding": None,
        "kernel": "test",
    }
    base.update(over)
    return base


def iface(name, **over):
    row = {"name": name, "status": "Up", "description": "", "mac": "", "addresses": []}
    row.update(over)
    return row


def by_id(checks):
    return {check.id: check for check in checks}


class AddressPlanTest(unittest.TestCase):
    def test_default_plan_is_clean(self):
        checks = by_id(doctor.run_checks(Settings(), snap()))
        self.assertEqual(checks["static-ip"].level, "ok")
        self.assertEqual(checks["gateway"].level, "ok")
        self.assertEqual(checks["bridge-address"].level, "ok")

    def test_static_ip_inside_the_dhcp_pool_fails(self):
        cfg = Settings.from_dict({"client": {"static_ip": "192.168.50.205"}})
        check = by_id(doctor.run_checks(cfg, snap()))["static-ip"]
        self.assertEqual(check.level, "fail")
        self.assertIn("DHCP pool", check.detail)

    def test_static_ip_outside_the_subnet_fails(self):
        cfg = Settings.from_dict({"client": {"static_ip": "10.1.1.5"}})
        checks = by_id(doctor.run_checks(cfg, snap()))
        self.assertEqual(checks["static-ip"].level, "fail")
        self.assertIn("outside", checks["static-ip"].detail)

    def test_static_ip_colliding_with_the_gateway_fails(self):
        cfg = Settings.from_dict({"client": {"static_ip": "192.168.50.1"}})
        check = by_id(doctor.run_checks(cfg, snap()))["static-ip"]
        self.assertEqual(check.level, "fail")
        self.assertIn("collides", check.detail)

    def test_gateway_outside_the_subnet_fails(self):
        cfg = Settings.from_dict({"site": {"gateway": "192.168.51.1"}})
        self.assertEqual(by_id(doctor.run_checks(cfg, snap()))["gateway"].level, "fail")

    def test_documentation_range_remote_host_warns(self):
        cfg = Settings.from_dict({"tunnel": {"remote_host": "203.0.113.99"}})
        self.assertEqual(by_id(doctor.run_checks(cfg, snap()))["remote"].level, "warn")

    def test_real_remote_host_is_ok(self):
        cfg = Settings.from_dict({"tunnel": {"remote_host": "198.51.100.7"}})
        self.assertEqual(by_id(doctor.run_checks(cfg, snap()))["remote"].level, "ok")


class AdapterTest(unittest.TestCase):
    def test_matching_lan_iface_is_ok(self):
        checks = by_id(doctor.run_checks(Settings(), snap(interfaces=[iface("eth0")])))
        self.assertEqual(checks["lan-iface"].level, "ok")
        self.assertIn("eth0", checks["lan-iface"].title)

    def test_wireless_lan_iface_warns_for_openvpn(self):
        cfg = Settings.from_dict({"tunnel": {"transport": "openvpn"}, "host": {"lan_iface": "Wi-Fi"}})
        env = snap(interfaces=[iface("Wi-Fi", description="Intel 802.11ac")])
        check = by_id(doctor.run_checks(cfg, env))["lan-iface"]
        self.assertEqual(check.level, "warn")
        self.assertIn("three MAC", check.fix)

    def test_wireless_lan_iface_is_fine_for_softether(self):
        cfg = Settings.from_dict({"host": {"lan_iface": "Wi-Fi"}})
        env = snap(interfaces=[iface("Wi-Fi", description="Intel 802.11ac")])
        self.assertEqual(by_id(doctor.run_checks(cfg, env))["lan-iface"].level, "ok")

    def test_virtual_device_is_detected_from_the_snapshot(self):
        env = snap(interfaces=[iface("tap0", addresses=["192.168.50.77/24"])])
        check = by_id(doctor.run_checks(Settings(), env))["virtual-device"]
        self.assertEqual(check.level, "ok")
        self.assertIn("tap0", check.detail)


class SummaryTest(unittest.TestCase):
    def test_verdict_and_counts(self):
        checks = [
            doctor.Check("a", "ok", "A", ""),
            doctor.Check("b", "warn", "B", ""),
        ]
        out = doctor.summary(checks)
        self.assertEqual(out["verdict"], "warn")
        self.assertEqual(out["counts"], {"ok": 1, "warn": 1, "fail": 0})
        self.assertEqual(out["checks"][0]["id"], "a")
