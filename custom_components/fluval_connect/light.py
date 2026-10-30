"""Aquasky RGBW light; state comes from the device, never optimistic updates."""
from homeassistant.components.light import ColorMode, LightEntity, LightEntityFeature
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import DeviceInfo

from . import DOMAIN


async def async_setup_entry(hass, entry, async_add_entities):
    async_add_entities([FluvalAquariumLight(hass, entry)])


class FluvalAquariumLight(LightEntity):
    _attr_has_entity_name = True
    _attr_name = None
    _attr_should_poll = False
    _attr_icon = "mdi:fish"
    _attr_supported_color_modes = {ColorMode.RGBW}
    _attr_color_mode = ColorMode.RGBW
    _attr_supported_features = LightEntityFeature.EFFECT
    _attr_effect_list = ["None"] + [f"Weather effect {n}" for n in range(1, 12)]

    def __init__(self, hass, entry):
        self._runtime = hass.data[DOMAIN][entry.entry_id]
        self._hub = self._runtime["hub"]
        self._attr_unique_id = entry.data["mac"] + "_light"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.data["mac"])},
            name=entry.data["name"], manufacturer="Fluval", model="Aquasky 3.0",
            sw_version=str(self._hub.properties.get(100, "Unknown")),
        )

    async def async_added_to_hass(self):
        self.async_on_remove(async_dispatcher_connect(
            self.hass, self._runtime["signal"], self.async_write_ha_state))

    @property
    def available(self):
        return self._hub.available

    @property
    def is_on(self):
        return self._hub.properties.get(104)

    @property
    def rgbw_color(self):
        levels = [self._hub.properties.get(key) for key in range(110, 114)]
        return tuple(round(v * 255 / 100) for v in levels) if all(type(v) is int and 0 <= v <= 100 for v in levels) else None

    @property
    def brightness(self):
        return max(self.rgbw_color) if self.rgbw_color is not None else None

    @property
    def effect(self):
        value = self._hub.properties.get(109)
        return self.effect_list[value] if type(value) is int and 0 <= value < len(self.effect_list) else None

    @property
    def extra_state_attributes(self):
        p = self._hub.properties
        return {"mode": {0: "Manual", 1: "Auto", 2: "Pro"}.get(p.get(103)),
                "channel_percentages": dict(zip(("red", "green", "blue", "white"), (p.get(k) for k in range(110, 114)))),
                "saved_colour_presets": {str(n): list(p[104+n]) for n in range(1, 5) if isinstance(p.get(104+n), bytes)}}

    async def _power(self, target):
        try:
            await self._hub.set_power(target)
        except (OSError, ValueError, TimeoutError) as err:
            raise HomeAssistantError("The aquarium did not confirm the requested light state") from err

    async def async_turn_on(self, **kwargs):
        if kwargs.get("brightness") == 0:
            await self._power(False)
            return
        values = {104: True}
        if "rgbw_color" in kwargs or "brightness" in kwargs:
            levels = kwargs.get("rgbw_color", self.rgbw_color)
            if levels is None:
                raise HomeAssistantError("Read the current channel levels first")
            if "brightness" in kwargs:
                highest = max(levels)
                levels = tuple(round(v * kwargs["brightness"] / highest) for v in levels) if highest else (kwargs["brightness"],) * 4
            values.update({110+i: max(0, min(100, round(v * 100 / 255))) for i, v in enumerate(levels)})
            values.update({103: 0, 109: 0})
        if "effect" in kwargs:
            values.update({103: 0, 109: self.effect_list.index(kwargs["effect"])})
        try:
            await self._hub.set_properties(values)
        except (OSError, ValueError, TimeoutError) as err:
            raise HomeAssistantError("The aquarium did not confirm the requested settings") from err

    async def async_turn_off(self, **kwargs):
        await self._power(False)
