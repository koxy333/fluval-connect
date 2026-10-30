"""Small, device-scoped Fluval Wi-Fi client using verified MQTT over TLS."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import re
import ssl
import struct
import time
from typing import Callable

STATUS_REQUEST = b"id:123;type:property_get;;"


def encode_cbor(value):
    def head(major, size):
        if size < 24:
            return bytes([(major << 5) | size])
        for tag, width in ((24, 1), (25, 2), (26, 4), (27, 8)):
            if size < 1 << (width * 8):
                return bytes([(major << 5) | tag]) + size.to_bytes(width, "big")
        raise ValueError("CBOR integer too large")
    if type(value) is bool:
        return b"\xf5" if value else b"\xf4"
    if type(value) is int:
        return head(0, value) if value >= 0 else head(1, -1 - value)
    if isinstance(value, bytes):
        return head(2, len(value)) + value
    if isinstance(value, (list, tuple)):
        return head(4, len(value)) + b"".join(encode_cbor(v) for v in value)
    if isinstance(value, dict):
        return head(5, len(value)) + b"".join(encode_cbor(k) + encode_cbor(v) for k, v in value.items())
    raise ValueError("Unsupported command value")


def validate_properties(values):
    if not isinstance(values, dict) or not values:
        raise ValueError("Empty command")
    for key, value in values.items():
        valid = False
        if key in (99, 104):
            valid = type(value) is bool
        elif key in (103, 109, 120) or 110 <= key <= 113:
            low, high = {103: (0, 2), 109: (0, 11), 120: (2, 12)}.get(key, (0, 100))
            valid = type(value) is int and low <= value <= high
        elif key in (114, 115):
            valid = isinstance(value, list) and len(value) == 2 and all(type(v) is int and 0 <= v < 1440 for v in value) and value[0] < value[1]
        elif key == 116:
            valid = type(value) is int and (0 <= value < 1440 or value == 65535)
        elif key in (117, 118):
            valid = isinstance(value, bytes) and len(value) == 4 and all(v <= 100 for v in value)
        elif key == 121:
            valid = isinstance(value, list) and 2 <= len(value) <= 12 and all(type(v) is int and 0 <= v < 1440 for v in value) and all(a < b for a, b in zip(value, value[1:]))
        elif key == 122:
            valid = isinstance(value, bytes) and 8 <= len(value) <= 48 and len(value) % 4 == 0 and all(v <= 100 for v in value)
        if not valid:
            raise ValueError("Unsupported property or value")
    if any(k in values for k in (120, 121, 122)):
        if not all(k in values for k in (120, 121, 122)) or values[120] != len(values[121]) or len(values[122]) != values[120] * 4:
            raise ValueError("The complete Pro timeline must be provided together")
    return values


def _cbor(data: bytes, pos=0, depth=0):
    if depth > 20 or pos >= len(data):
        raise ValueError("Invalid CBOR")
    head = data[pos]
    pos += 1
    major, info = head >> 5, head & 31
    if info < 24:
        value = info
    elif info in (24, 25, 26, 27):
        size = 1 << (info - 24)
        if pos + size > len(data):
            raise ValueError("Truncated CBOR")
        raw = data[pos:pos + size]
        value = int.from_bytes(raw, "big")
        pos += size
    else:
        raise ValueError("Unsupported CBOR encoding")
    if major == 0:
        return value, pos
    if major == 1:
        return -1 - value, pos
    if major in (2, 3):
        if pos + value > len(data):
            raise ValueError("Truncated CBOR string")
        raw = data[pos:pos + value]
        return (raw if major == 2 else raw.decode("utf-8")), pos + value
    if major == 4:
        result = []
        for _ in range(value):
            item, pos = _cbor(data, pos, depth + 1)
            result.append(item)
        return result, pos
    if major == 5:
        result = {}
        for _ in range(value):
            key, pos = _cbor(data, pos, depth + 1)
            item, pos = _cbor(data, pos, depth + 1)
            if not isinstance(key, (int, str)) or key in result:
                raise ValueError("Invalid CBOR map key")
            result[key] = item
        return result, pos
    if major == 7:
        if info in (20, 21, 22):
            return {20: False, 21: True, 22: None}[info], pos
        if info in (25, 26, 27):
            return struct.unpack({25: ">e", 26: ">f", 27: ">d"}[info], raw)[0], pos
    raise ValueError("Unsupported CBOR type")


def decode_status(payload: bytes) -> dict[int, object]:
    if not payload or len(payload) > 65536:
        raise ValueError("Invalid status size")
    text = payload.decode("utf-8").strip()
    if text.endswith(";;"):
        text = text[:-2].strip()
    if not text.startswith("{"):
        text = "{" + text + "}"
    try:
        envelope = json.loads(text)
    except json.JSONDecodeError:
        inner, envelope = text[1:-1].strip(), {}
        while inner:
            match = re.match(r'''\s*(id|type|param|version)\s*:\s*("[^"\\]*"|'[^'\\]*'|[^,;{}\s]+)\s*(?:[,;]\s*|$)''', inner)
            if not match or match[1] in envelope:
                raise ValueError("Invalid status envelope")
            envelope[match[1]] = match[2].strip("\"'")
            inner = inner[match.end():]
    if not isinstance(envelope, dict) or not isinstance(envelope.get("param"), str):
        raise ValueError("Status has no CBOR parameter")
    raw = bytes.fromhex(envelope["param"])
    properties, pos = {}, 0
    while pos < len(raw):
        mapping, pos = _cbor(raw, pos)
        if not isinstance(mapping, dict) or any(type(k) is not int for k in mapping):
            raise ValueError("Invalid status property map")
        properties.update(mapping)
    return properties


def _string(value: str) -> bytes:
    raw = value.encode("utf-8")
    if len(raw) > 65535:
        raise ValueError("MQTT string too long")
    return len(raw).to_bytes(2, "big") + raw


def packet(header: int, payload: bytes) -> bytes:
    remaining, length = len(payload), bytearray()
    if remaining > 65536:
        raise ValueError("MQTT packet too large")
    while True:
        digit = remaining % 128
        remaining //= 128
        length.append(digit | (128 if remaining else 0))
        if not remaining:
            return bytes([header]) + bytes(length) + payload


async def read_packet(reader):
    header = (await reader.readexactly(1))[0]
    size, multiplier = 0, 1
    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        size += (digit & 127) * multiplier
        if size > 65536:
            raise ValueError("MQTT packet too large")
        if not digit & 128:
            return header, await reader.readexactly(size)
        multiplier *= 128
    raise ValueError("Invalid MQTT length")


class FluvalClient:
    def __init__(self, private: dict, mac: str, client_suffix: str, changed: Callable):
        if not re.fullmatch(r"[0-9A-F]{12}", mac):
            raise ValueError("Invalid aquarium MAC")
        # Only devices confirmed by the account sign-in may use this client.
        approved = {d["wifiMac"]: d for d in private["verified_devices"]}
        if mac not in approved:
            raise ValueError("Aquarium was not verified by the signed-in account")
        self._private, self.mac, self._changed = private, mac, changed
        self.product = int(approved[mac]["product_id"])
        prefix = f"cyd/dev/Fluval/Fluval-{self.product}/{mac}"
        self.status_topic, self.command_topic = prefix + "/uplink", prefix + "/downlink"
        self.client_id = "GID_CYD@@@ha_" + mac + "_" + client_suffix
        self.connected = False
        self.properties: dict[int, object] = {}
        self.last_state = 0.0
        self._writer = None
        self._write_lock = asyncio.Lock()
        self._control_lock = asyncio.Lock()
        self._last_update = {}
        self._update_event = asyncio.Event()
        self._stopped = False

    @property
    def available(self):
        return self.connected and time.monotonic() - self.last_state < 180

    async def _send(self, header, payload=b""):
        async with self._write_lock:
            if self._writer is None:
                raise ConnectionError("Aquarium cloud is disconnected")
            self._writer.write(packet(header, payload))
            await self._writer.drain()

    async def request_status(self):
        await self._send(0x30, _string(self.command_topic) + STATUS_REQUEST)

    async def set_power(self, power: bool):
        await self.set_properties({104: power})

    async def set_properties(self, values):
        validate_properties(values)
        if not self.available:
            raise ConnectionError("Aquarium status is unavailable")
        async with self._control_lock:
            command = b"id:123;type:property_set;param:" + encode_cbor(values).hex().upper().encode() + b";;"
            self._update_event.clear()
            await self._send(0x30, _string(self.command_topic) + command)
            await self.request_status()
            deadline, confirmed = time.monotonic() + 15, set()
            while time.monotonic() < deadline:
                try:
                    await asyncio.wait_for(self._update_event.wait(), deadline - time.monotonic())
                except TimeoutError:
                    break
                self._update_event.clear()
                for key, target in values.items():
                    actual = self._last_update.get(key)
                    if key in (121, 122) and isinstance(actual, type(target)):
                        actual = actual[:len(target)]
                    if key in self._last_update and type(actual) is type(target) and actual == target:
                        confirmed.add(key)
                    elif key in self._last_update:
                        confirmed.discard(key)
                if confirmed == values.keys():
                    return
            raise TimeoutError("The aquarium did not confirm the requested settings")

    async def _receive(self, header, payload):
        if header >> 4 != 3:
            return
        if len(payload) < 2:
            raise ValueError("Invalid MQTT publish")
        size = int.from_bytes(payload[:2], "big")
        if size + 2 > len(payload):
            raise ValueError("Invalid MQTT topic")
        topic, data = payload[2:2 + size].decode(), payload[2 + size:]
        qos = (header >> 1) & 3
        if qos == 1:
            if len(data) < 2:
                raise ValueError("Invalid MQTT packet ID")
            await self._send(0x40, data[:2])
            data = data[2:]
        elif qos != 0:
            raise ValueError("Unsupported MQTT QoS")
        if topic != self.status_topic or header & 1:
            return  # Never count retained messages as live device confirmation.
        try:
            properties = decode_status(data)
        except (ValueError, UnicodeError, TypeError):
            return
        if not properties or any(k < 99 or k > 123 for k in properties):
            return
        self.properties.update(properties)
        self._last_update = properties
        if type(properties.get(104)) is bool:
            self.last_state = time.monotonic()
        self._update_event.set()
        self._changed()

    async def run(self):
        delay = 5
        tls_context = await asyncio.to_thread(ssl.create_default_context)
        while not self._stopped:
            pending = None
            try:
                instance = self._private["instance_id"]
                reader, self._writer = await asyncio.wait_for(asyncio.open_connection(
                    instance + ".mqtt.aliyuncs.com", 8883, ssl=tls_context), 15)
                username = "Signature|" + self._private["access_key"] + "|" + instance + "|"
                password = base64.b64encode(hmac.new(self._private["signing_key"].encode(),
                    self.client_id.encode(), hashlib.sha1).digest()).decode()
                await self._send(0x10, b"\x00\x04MQTT\x04\xc2\x00\x2d" +
                                 _string(self.client_id) + _string(username) + _string(password))
                del username, password
                header, payload = await asyncio.wait_for(read_packet(reader), 10)
                if header != 0x20 or payload != b"\x00\x00":
                    raise ConnectionError("Aquarium cloud connection rejected")
                await self._send(0x82, b"\x00\x01" + _string(self.status_topic) + b"\x00")
                header, payload = await asyncio.wait_for(read_packet(reader), 10)
                if header != 0x90 or payload != b"\x00\x01\x00":
                    raise ConnectionError("Aquarium status subscription rejected")
                self.connected, delay = True, 5
                self._changed()
                now = time.monotonic()
                next_read, next_ping, last_rx = now, now + 20, now
                pending = asyncio.create_task(read_packet(reader))
                while not self._stopped:
                    now = time.monotonic()
                    if now - last_rx > 75:
                        raise TimeoutError("Aquarium cloud stopped responding")
                    if now >= next_read:
                        await self.request_status()
                        next_read = now + 60
                        self._changed()
                    if now >= next_ping:
                        await self._send(0xC0)
                        next_ping = now + 20
                    ready, _ = await asyncio.wait({pending}, timeout=max(0.1, min(next_read, next_ping) - now))
                    if ready:
                        header, payload = pending.result()
                        last_rx = time.monotonic()
                        await self._receive(header, payload)
                        pending = asyncio.create_task(read_packet(reader))
            except asyncio.CancelledError:
                raise
            except (OSError, ValueError, TimeoutError, asyncio.IncompleteReadError):
                pass  # Never log raw broker data or signing material.
            finally:
                if pending:
                    pending.cancel()
                    await asyncio.gather(pending, return_exceptions=True)
                self.connected = False
                self._changed()
                if self._writer:
                    self._writer.close()
                    try:
                        await self._writer.wait_closed()
                    except (OSError, TimeoutError):
                        pass
                    self._writer = None
            if not self._stopped:
                await asyncio.sleep(delay)
                delay = min(delay * 2, 120)

    async def stop(self):
        self._stopped = True
        if self._writer:
            self._writer.close()
