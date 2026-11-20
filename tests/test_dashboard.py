"""Dashboard: the link state may only claim `linked` from observed facts."""

import unittest

from lanbridge.dashboard.server import _link
from lanbridge.settings import Settings


def iface(name, addresses):
    return {
        "name": name,
        "status": "Up",
        "description": "",
        "mac": "",
        "addresses": addresses,
    }


def snap(interfaces=(), virtual=()):
    return {
        "interfaces": list(interfaces),
        "virtual_devices": list(virtual),
        "processes": [],
    }


class HonestStateTest(unittest.TestCase):
    def test_no_virtual_device_is_offline(self):
        link = _link(Settings(), snap([iface("eth0", ["192.168.50.10/24"])]))
        self.assertEqual(link["state"], "offline")
        self.assertIsNone(link["device"])
        self.assertIsNone(link["address"])

    def test_virtual_device_without_a_lan_address_is_only_an_adapter(self):
        link = _link(Settings(), snap([iface("tap0", ["169.254.7.7/16"])], ["tap0"]))
        self.assertEqual(link["state"], "adapter")
        self.assertEqual(link["device"], "tap0")
        self.assertIsNone(link["address"])

    def test_in_subnet_address_on_a_virtual_device_links(self):
        link = _link(Settings(), snap([iface("tap0", ["192.168.50.77/24"])], ["tap0"]))
        self.assertEqual(link["state"], "linked")
        self.assertEqual(link["device"], "tap0")
        self.assertEqual(link["address"], "192.168.50.77/24")

    def test_malformed_addresses_are_skipped(self):
        link = _link(Settings(), snap([iface("tap0", ["not-an-ip", "192.168.50.78/24"])], ["tap0"]))
        self.assertEqual(link["state"], "linked")
        self.assertEqual(link["address"], "192.168.50.78/24")

    def test_mtu_follows_the_transport(self):
        vxlan = Settings.from_dict({"tunnel": {"transport": "vxlan"}})
        self.assertEqual(_link(vxlan, snap())["mtu"], 1370)
        openvpn = Settings.from_dict({"tunnel": {"transport": "openvpn", "mtu": 1400}})
        self.assertEqual(_link(openvpn, snap())["mtu"], 1400)
