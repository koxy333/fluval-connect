"""Native controls mapped from the Aquasky Wi-Fi app's property handlers."""
from datetime import datetime, time, timezone

from homeassistant.components.button import ButtonEntity
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity, SensorDeviceClass
from homeassistant.components.time import TimeEntity
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo, Entity, EntityCategory

from . import DOMAIN
from .models import clock, timeline

CHANNELS = ("Red", "Green", "Blue", "White")
MODES = ("Manual", "Auto", "Pro")
WEATHER = ("None",) + tuple(f"Weather effect {n}" for n in range(1, 12))












class FluvalEntity(Entity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, hass, entry, key, name, icon):
        self.runtime = hass.data[DOMAIN][entry.entry_id]
        self.hub = self.runtime["hub"]
        self._attr_unique_id = entry.data["mac"] + "_" + key
        self._attr_name, self._attr_icon = name, icon
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, entry.data["mac"])})

    @property
    def available(self):
        return self.hub.available

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(self.hass, self.runtime["signal"], self.async_write_ha_state))

    async def write(self, properties):
        try:
            await self.hub.set_properties(properties)
        except (OSError, ValueError, TimeoutError) as err:
            raise HomeAssistantError(str(err)) from err


class FluvalLevel(FluvalEntity, NumberEntity):
    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = "%"
    _attr_mode = NumberMode.SLIDER

    def __init__(self, hass, entry, group, channel):
        self.group, self.channel = group, channel
        super().__init__(hass, entry, f"{group}_{channel}", f"{group.title()} {CHANNELS[channel].lower()}", "mdi:palette")

    @property
    def native_value(self):
        if self.group == "manual":
            return self.hub.properties.get(110 + self.channel)
        data = self.hub.properties.get(117 if self.group == "day" else 118, b"")
        return data[self.channel] if len(data) == 4 else None

    async def async_set_native_value(self, value):
        level = round(value)
        if self.group == "manual":
            await self.write({103: 0, 110 + self.channel: level, 109: 0})
        else:
            key = 117 if self.group == "day" else 118
            data = bytearray(self.hub.properties.get(key, b""))
            if len(data) != 4:
                raise HomeAssistantError("Read the current schedule colours first")
            data[self.channel] = level
            await self.write({key: bytes(data)})


class FluvalChoice(FluvalEntity, SelectEntity):
    def __init__(self, hass, entry, key):
        self.key = key
        name, choices, icon = {103: ("Mode", MODES, "mdi:calendar-clock"),
                              109: ("Weather effect", WEATHER, "mdi:weather-partly-cloudy"),
                              99: ("Daylight saving", ("Off", "On"), "mdi:clock-outline")}[key]
        self._attr_options = list(choices)
        super().__init__(hass, entry, str(key), name, icon)

    @property
    def current_option(self):
        value = self.hub.properties.get(self.key)
        if self.key == 99 and type(value) is bool:
            return self.options[int(value)]
        if type(value) is int and 0 <= value < len(self.options):
            return self.options[value]
        return None

    async def async_select_option(self, option):
        value = self.options.index(option)
        properties = {self.key: bool(value) if self.key == 99 else value}
        if self.key == 109:
            properties[103] = 0
        await self.write(properties)


class FluvalClock(FluvalEntity, TimeEntity):
    def __init__(self, hass, entry, key, index, name):
        self.key, self.index = key, index
        super().__init__(hass, entry, f"time_{key}_{index}", name, "mdi:weather-sunset")

    @property
    def native_value(self):
        value = self.hub.properties.get(self.key)
        if self.index is not None:
            value = value[self.index] if isinstance(value, list) and len(value) == 2 else None
        text = clock(value)
        return time.fromisoformat(text) if text else None

    async def async_set_value(self, value):
        if value.second or value.microsecond:
            raise HomeAssistantError("Schedule times use whole minutes")
        target = value.hour * 60 + value.minute
        if self.index is None:
            await self.write({self.key: target})
            return
        old = self.hub.properties.get(self.key)
        if not isinstance(old, list) or len(old) != 2:
            raise HomeAssistantError("Read the current schedule first")
        pair = list(old)
        pair[self.index] = target
        sunrise = pair if self.key == 114 else self.hub.properties.get(114)
        sunset = pair if self.key == 115 else self.hub.properties.get(115)
        if not sunrise or not sunset or sunrise[1] > sunset[0]:
            raise HomeAssistantError("Sunrise must finish before sunset starts; use Set Auto schedule to change all times together")
        await self.write({self.key: pair})


class FluvalAction(FluvalEntity, ButtonEntity):
    def __init__(self, hass, entry, preset):
        self.preset = preset
        super().__init__(hass, entry, f"preset_{preset}" if preset else "refresh",
                         f"Apply colour preset {preset}" if preset else "Refresh status",
                         "mdi:palette" if preset else "mdi:refresh")

    async def async_press(self):
        if not self.preset:
            await self.hub.request_status()
            return
        levels = self.hub.properties.get(104 + self.preset)
        if not isinstance(levels, bytes) or len(levels) != 4:
            raise HomeAssistantError("No saved colour preset was received")
        await self.write({103: 0, 109: 0, **{110+i: v for i, v in enumerate(levels)}})


class FluvalStatus(FluvalEntity, SensorEntity):
    def __init__(self, hass, entry, kind):
        self.kind = kind
        name, icon = {"firmware": ("Firmware", "mdi:chip"), "clock": ("Device clock", "mdi:clock-outline"),
                      "auto": ("Auto schedule", "mdi:weather-sunset"), "pro": ("Pro schedule", "mdi:chart-timeline-variant"),
                      "dynamic": ("Scheduled weather", "mdi:weather-partly-cloudy")}[kind]
        if kind == "clock":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_entity_registry_enabled_default = False
        super().__init__(hass, entry, kind, name, icon)

    @property
    def native_value(self):
        p = self.hub.properties
        if self.kind == "firmware":
            return str(p[100]) if 100 in p else None
        if self.kind == "clock":
            return datetime.fromtimestamp(p[102] / 1000, timezone.utc) if type(p.get(102)) is int else None
        if self.kind == "pro":
            return f"{len(timeline(p))} points"
        if self.kind == "dynamic":
            raw = p.get(123, b"")
            return "Configured" if any(raw) else "Disabled"
        start, end = p.get(114, []), p.get(115, [])
        return f"{clock(start[0])}–{clock(end[1])}" if len(start) == len(end) == 2 else None

    @property
    def extra_state_attributes(self):
        p = self.hub.properties
        if self.kind == "auto":
            return {"sunrise": [clock(v) for v in p.get(114, [])], "sunset": [clock(v) for v in p.get(115, [])],
                    "night_off": clock(p.get(116)), "day": dict(zip(CHANNELS, p.get(117, b""))),
                    "night": dict(zip(CHANNELS, p.get(118, b"")))}
        if self.kind == "pro":
            return {"points": timeline(p)}
        if self.kind == "dynamic":
            return {"device_data_hex": p.get(123, b"").hex(), "editable": False}
        return {}


async def setup_numbers(hass, entry, add):
    add([FluvalLevel(hass, entry, group, channel) for group in ("manual", "day", "night") for channel in range(4)])


async def setup_selects(hass, entry, add):
    add([FluvalChoice(hass, entry, key) for key in (103, 109, 99)])


async def setup_times(hass, entry, add):
    add([FluvalClock(hass, entry, key, index, name) for key, index, name in (
        (114, 0, "Sunrise start"), (114, 1, "Sunrise end"), (115, 0, "Sunset start"),
        (115, 1, "Sunset end"), (116, None, "Night off"))])


async def setup_buttons(hass, entry, add):
    add([FluvalAction(hass, entry, preset) for preset in range(5)])


async def setup_sensors(hass, entry, add):
    add([FluvalStatus(hass, entry, kind) for kind in ("firmware", "clock", "auto", "pro", "dynamic")])
