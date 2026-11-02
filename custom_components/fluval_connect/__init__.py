"""Fluval Connect aquarium integration."""
import asyncio
import json
from pathlib import Path

from homeassistant.const import Platform
from homeassistant.components import frontend
from homeassistant.components.http import StaticPathConfig
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import config_validation as cv
from homeassistant.exceptions import HomeAssistantError
import voluptuous as vol

from .protocol import FluvalClient
from .cloud import validate_connection

DOMAIN = "fluval_connect"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
PLATFORMS = [Platform.LIGHT, Platform.NUMBER, Platform.SELECT, Platform.TIME, Platform.BUTTON, Platform.SENSOR]


def load_private():
    for path in (Path(__file__).with_name("_private_cloud.json"), Path(__file__).parents[2] / "fluval_connect_private.json"):
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    raise FileNotFoundError("The legacy Fluval connection profile is missing")


async def async_setup(hass, config):
    await hass.http.async_register_static_paths([
        StaticPathConfig("/fluval_connect_static", str(Path(__file__).parent / "frontend"), True)
    ])
    frontend.add_extra_js_url(hass, "/fluval_connect_static/fluval-connect-card.js?v=0.2.1")
    return True


async def async_migrate_entry(hass, entry):
    if entry.version > 2:
        return False
    if entry.version < 2:
        try:
            private = await hass.async_add_executor_job(load_private)
            devices = [d for d in private.get("verified_devices", []) if d.get("wifiMac") == entry.data["mac"]]
            connection = validate_connection(*(private[key] for key in ("instance_id", "access_key", "signing_key")))
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False
        if len(devices) != 1:
            return False
        connection["verified_devices"] = devices
        hass.config_entries.async_update_entry(entry, data={**entry.data, "connection": connection}, version=2)
    return True


async def async_setup_entry(hass, entry):
    private = entry.data.get("connection")
    if private is None:
        private = await hass.async_add_executor_job(load_private)
    signal = DOMAIN + "_" + entry.entry_id
    hub = FluvalClient(private, entry.data["mac"], entry.entry_id[:12],
                       lambda: async_dispatcher_send(hass, signal))
    task = entry.async_create_background_task(hass, hub.run(), "Fluval aquarium cloud")
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {"hub": hub, "signal": signal, "task": task}
    try:
        async with asyncio.timeout(30):
            while not hub.available:
                await asyncio.sleep(0.2)
    except TimeoutError:
        await hub.stop()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        hass.data[DOMAIN].pop(entry.entry_id, None)
        raise ConfigEntryNotReady("No live response from the aquarium cloud") from None
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    if not hass.services.has_service(DOMAIN, "set_pro_schedule"):
        async def set_schedule(call):
            from .models import auto_properties, pro_properties
            device = dr.async_get(hass).async_get(call.data["device_id"])
            owned = {identifier[1] for identifier in device.identifiers if identifier[0] == DOMAIN} if device else set()
            candidates = [item["hub"] for item in hass.data.get(DOMAIN, {}).values() if item["hub"].mac in owned]
            if len(candidates) != 1:
                raise HomeAssistantError("Select the verified Fluval aquarium device")
            try:
                values = pro_properties(call.data["points"]) if call.service == "set_pro_schedule" else auto_properties(call.data["schedule"])
                await candidates[0].set_properties(values)
            except (OSError, ValueError, TypeError, KeyError, TimeoutError) as err:
                raise HomeAssistantError(str(err)) from err
        for service, field in (("set_pro_schedule", "points"), ("set_auto_schedule", "schedule")):
            hass.services.async_register(DOMAIN, service, set_schedule, schema=vol.Schema({
                vol.Required("device_id"): str, vol.Required(field): object,
            }))
    return True


async def async_unload_entry(hass, entry):
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime = hass.data[DOMAIN].pop(entry.entry_id)
    await runtime["hub"].stop()
    runtime["task"].cancel()
    await asyncio.gather(runtime["task"], return_exceptions=True)
    return True
