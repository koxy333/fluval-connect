"""Configure an owner's Aquasky with user-supplied cloud connection settings."""
import asyncio
import secrets

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.helpers import selector

from . import DOMAIN
from .cloud import CannotConnect, InvalidAuth, discover_devices, validate_connection
from .protocol import FluvalClient


class FluvalConnectConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 2

    def __init__(self):
        self._connection = None
        self._devices = []

    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self._connection = validate_connection(user_input["instance_id"], user_input["access_key"], user_input["signing_key"])
            except ValueError:
                errors["base"] = "invalid_connection"
            else:
                return await self.async_step_account()
        return self.async_show_form(step_id="user", errors=errors, data_schema=vol.Schema({
            vol.Required("instance_id"): str,
            vol.Required("access_key"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
            vol.Required("signing_key"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }))

    async def async_step_account(self, user_input=None):
        errors = {}
        if user_input is not None:
            try:
                self._devices = await self.hass.async_add_executor_job(discover_devices, user_input["email"], user_input["password"])
            except InvalidAuth:
                errors["base"] = "invalid_auth"
            except CannotConnect:
                errors["base"] = "cannot_connect"
            finally:
                user_input.clear()
            if not errors:
                if not self._devices:
                    return self.async_abort(reason="no_supported_devices")
                return await self.async_step_device()
        return self.async_show_form(step_id="account", errors=errors, data_schema=vol.Schema({
            vol.Required("email"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL)),
            vol.Required("password"): selector.TextSelector(selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)),
        }))

    async def async_step_device(self, user_input=None):
        errors = {}
        devices = {device["wifiMac"]: device for device in self._devices}
        if not devices or self._connection is None:
            return self.async_abort(reason="device_not_verified")
        if user_input is not None:
            mac = user_input["device"]
            if mac not in devices:
                return self.async_abort(reason="device_not_verified")
            name = user_input["name"].strip()
            if not name:
                errors["name"] = "name_required"
            else:
                await self.async_set_unique_id(mac)
                self._abort_if_unique_id_configured()
                connection = {**self._connection, "verified_devices": [devices[mac]]}
                hub = FluvalClient(connection, mac, "setup_" + secrets.token_hex(6), lambda: None)
                task = asyncio.create_task(hub.run())
                try:
                    async with asyncio.timeout(35):
                        while not hub.available:
                            await asyncio.sleep(0.2)
                except TimeoutError:
                    errors["base"] = "cannot_connect"
                finally:
                    await hub.stop()
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
                if not errors:
                    return self.async_create_entry(title=name, data={"name": name, "mac": mac, "connection": connection})
        return self.async_show_form(step_id="device", errors=errors, data_schema=vol.Schema({
            vol.Required("device", default=next(iter(devices))): vol.In({mac: item["name"] for mac, item in devices.items()}),
            vol.Required("name", default="Aquarium light"): str,
        }))
