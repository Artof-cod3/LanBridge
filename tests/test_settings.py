"""Settings: defaults, coercion from loose TOML, and a lossless round-trip."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from lanbridge.settings import Settings, dumps, tomllib


class DefaultsTest(unittest.TestCase):
    def test_defaults_are_a_valid_lab_plan(self):
        cfg = Settings()
        self.assertEqual(str(cfg.network), "192.168.50.0/24")
        self.assertEqual(cfg.netmask, "255.255.255.0")
        self.assertEqual(cfg.broadcast, "192.168.50.255")
        self.assertEqual(cfg.wg_mtu, 1420)
        self.assertEqual(cfg.vxlan_mtu, 1370)

    def test_subnet_host_bits_are_normalised(self):
        cfg = Settings.from_dict({"site": {"subnet": "10.0.5.17/24"}})
        self.assertEqual(str(cfg.network), "10.0.5.0/24")


class FromDictTest(unittest.TestCase):
    def test_integers_are_coerced_from_strings(self):
        cfg = Settings.from_dict({"tunnel": {"listen_port": "5555"}})
        self.assertIsInstance(cfg.tunnel.listen_port, int)
        self.assertEqual(cfg.tunnel.listen_port, 5555)

    def test_unknown_keys_and_sections_are_ignored(self):
        cfg = Settings.from_dict({"site": {"nope": 1}, "mystery": {"x": 2}})
        self.assertFalse(hasattr(cfg.site, "nope"))
        self.assertFalse(hasattr(cfg, "mystery"))

    def test_partial_sections_keep_their_defaults(self):
        cfg = Settings.from_dict({"client": {"static_ip": "192.168.50.80"}})
        self.assertEqual(cfg.client.static_ip, "192.168.50.80")
        self.assertEqual(cfg.client.tap_iface, "tap0")
        self.assertEqual(cfg.site.gateway, "192.168.50.1")


class RoundTripTest(unittest.TestCase):
    def test_save_load_round_trip(self):
        cfg = Settings.from_dict(
            {
                "site": {"name": "office", "subnet": "10.20.0.0/23"},
                "tunnel": {"transport": "vxlan", "remote_port": 4433},
                "client": {"static_ip": "10.20.1.77"},
            }
        )
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "lanbridge.toml"
            cfg.save(path)
            again = Settings.load(path)
        self.assertEqual(again.to_dict(), cfg.to_dict())

    def test_missing_file_yields_defaults(self):
        with TemporaryDirectory() as tmp:
            loaded = Settings.load(Path(tmp) / "absent.toml")
        self.assertEqual(loaded.to_dict(), Settings().to_dict())

    def test_dumps_escapes_quotes_and_backslashes(self):
        cfg = Settings.from_dict({"client": {"vpn_adapter_name": 'A "B" \\ C'}})
        parsed = tomllib.loads(dumps(cfg))
        self.assertEqual(parsed["client"]["vpn_adapter_name"], 'A "B" \\ C')
