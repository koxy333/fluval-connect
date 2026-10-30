"""Offline tests: never connect to a broker or change a physical light."""
import asyncio
import importlib.util
from pathlib import Path
import time
import unittest

BASE = Path(__file__).resolve().parents[1] / "custom_components/fluval_connect"


def load(name):
    spec = importlib.util.spec_from_file_location(name, BASE / (name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


p, models, cloud = (load(name) for name in ("protocol", "models", "cloud"))
MAC = "001122334455"


class ProtocolTests(unittest.TestCase):
    def test_power_wire_and_roundtrip(self):
        self.assertEqual(p.encode_cbor({104: True}).hex(), "a11868f5")
        for value in ({104: False}, {114: [420, 480]}, {117: bytes([0, 5, 20, 100])}):
            raw = p.encode_cbor(value)
            self.assertEqual(p._cbor(raw), (value, len(raw)))

    def test_command_boundaries(self):
        for value in ({102: 0}, {110: 101}, {103: 3}, {109: 12}, {114: [480, 420]},
                      {120: 2}, {120: 2, 121: [480, 480], 122: bytes(8)}, {104: 1}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                p.validate_properties(value)

    def test_schedule_roundtrip_and_order(self):
        points = [dict(time=t, red=0, green=10, blue=20, white=100) for t in ("08:00", "20:00")]
        value = models.pro_properties(points)
        self.assertEqual(models.timeline(p.validate_properties(value)), points)
        with self.assertRaises(ValueError):
            models.pro_properties(points[::-1])

    def test_malformed_status(self):
        for raw in (b"", b"{}", b"param:zz;;", b"param:01;;", b"param:BF;;", b"x" * 65537):
            with self.subTest(raw=raw[:20]), self.assertRaises(ValueError):
                p.decode_status(raw)

    def test_owner_scope(self):
        with self.assertRaises(ValueError):
            p.FluvalClient({"verified_devices": []}, MAC, "offline", lambda: None)


class DiscoveryTests(unittest.TestCase):
    def test_only_supported_owned_devices_and_no_token_returned(self):
        def fake(path, **kwargs):
            if path.startswith("/login"):
                return {"data": {"access_token": "test-transient-token"}}
            self.assertEqual(kwargs["token"], "test-transient-token")
            return {"data": [{"lightWifiMac": "00:11:22:33:44:55", "lightName": "Aquasky 3.0"},
                             {"wifiMac": "112233445566", "name": "Other", "productId": 999}]}
        self.assertEqual(cloud.discover_devices("owner@example.invalid", "test-password", fake),
                         [{"wifiMac": MAC, "name": "Aquasky 3.0", "product_id": 532}])

    def test_rejected_signin(self):
        with self.assertRaises(cloud.InvalidAuth):
            cloud.discover_devices("owner@example.invalid", "wrong", lambda *a, **k: {"data": {}})

    def test_connection_validation(self):
        for instance in ("example.com", "mqtt-ok/../../", "mqtt-" + "x" * 81):
            with self.assertRaises(ValueError):
                cloud.validate_connection(instance, "test-access", "test-signing")


class TransportTests(unittest.IsolatedAsyncioTestCase):
    def client(self):
        return p.FluvalClient({"verified_devices": [{"wifiMac": MAC, "product_id": 532}]}, MAC, "offline", lambda: None)

    async def test_confirmation_and_partial_report(self):
        c = self.client()
        c.connected = True
        c.last_state = time.monotonic()
        c.properties = {104: True, 103: 1}
        sent = []
        async def send(header, data):
            sent.append(data)
        async def status():
            c._last_update = {103: 2}
            c.properties.update(c._last_update)
            c._update_event.set()
        c._send, c.request_status = send, status
        await c.set_properties({103: 2})
        self.assertTrue(c.properties[104])
        self.assertIn(b"A1186702", sent[0])
        before = c.last_state
        await c._receive(0x30, p._string(c.status_topic) + b"id:123;type:property_post;param:A1186E185A;;")
        self.assertEqual(c.properties[110], 90)
        self.assertEqual(c.last_state, before)

    async def test_retained_and_wrong_topic_ignored(self):
        c = self.client()
        report = b"id:123;type:property_post;param:A11868F5;;"
        await c._receive(0x31, p._string(c.status_topic) + report)
        await c._receive(0x30, p._string("other/device/uplink") + report)
        self.assertEqual(c.properties, {})
        self.assertFalse(c.available)


if __name__ == "__main__":
    unittest.main()
