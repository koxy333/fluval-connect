"""Regression tests of entity decisions with a synthetic Home Assistant surface."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock

BASE = Path(__file__).resolve().parents[1] / 'custom_components/fluval_connect'


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    sys.modules[name] = result
    return result


class Entity:
    @property
    def options(self):
        return self._attr_options

    @property
    def effect_list(self):
        return self._attr_effect_list


for name in ('homeassistant', 'homeassistant.components', 'homeassistant.helpers'):
    module(name)
for platform, entity in (('light', 'LightEntity'), ('button', 'ButtonEntity'),
                         ('number', 'NumberEntity'), ('select', 'SelectEntity'),
                         ('sensor', 'SensorEntity'), ('time', 'TimeEntity')):
    module('homeassistant.components.' + platform, **{entity: type(entity, (Entity,), {})})
sys.modules['homeassistant.components.light'].ColorMode = SimpleNamespace(RGBW='rgbw')
sys.modules['homeassistant.components.light'].LightEntityFeature = SimpleNamespace(EFFECT=4)
sys.modules['homeassistant.components.number'].NumberMode = SimpleNamespace(SLIDER='slider')
sys.modules['homeassistant.components.sensor'].SensorDeviceClass = SimpleNamespace(TIMESTAMP='timestamp')
module('homeassistant.exceptions', HomeAssistantError=type('HomeAssistantError', (Exception,), {}))
module('homeassistant.helpers.dispatcher', async_dispatcher_connect=lambda *a: None)
module('homeassistant.helpers.entity', Entity=Entity, DeviceInfo=dict,
       EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic'))
package = module('fluval_entity_test', DOMAIN='fluval_connect')
package.__path__ = [str(BASE)]


def load(name):
    spec = importlib.util.spec_from_file_location('fluval_entity_test.' + name, BASE / (name + '.py'))
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


light, controls = load('light'), load('controls')


class EntityRegressionTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, mode=0, power=False):
        hub = SimpleNamespace(available=True, properties={103: mode, 104: power, 109: 0,
            110: 100, 111: 50, 112: 25, 113: 0}, set_properties=AsyncMock())
        entry = SimpleNamespace(entry_id='test-entry', data={'mac': '001122334455', 'name': 'Test aquarium'})
        hass = SimpleNamespace(data={'fluval_connect': {entry.entry_id: {'hub': hub, 'signal': 'test'}}})
        return hass, entry, hub, light.FluvalAquariumLight(hass, entry)

    async def test_scheduled_output_is_not_saved_manual_power_or_colour(self):
        for mode in (1, 2):
            for power in (False, True):
                _, _, _, entity = self.fixture(mode, power)
                self.assertTrue(entity.available)
                self.assertIsNone(entity.is_on)
                self.assertIsNone(entity.brightness)
                self.assertIsNone(entity.rgbw_color)
                self.assertIsNone(entity.effect)
                self.assertTrue(entity.assumed_state)
                self.assertFalse(entity.extra_state_attributes['output_state_reported'])

    async def test_manual_output_is_reported_normally(self):
        _, _, _, entity = self.fixture(0, True)
        self.assertTrue(entity.is_on)
        self.assertEqual(entity.rgbw_color, (255, 128, 64, 0))
        self.assertEqual(entity.brightness, 255)

    async def test_power_is_an_explicit_manual_override(self):
        _, _, hub, entity = self.fixture(1)
        await entity.async_turn_off()
        hub.set_properties.assert_awaited_with({103: 0, 104: False})
        await entity.async_turn_on()
        hub.set_properties.assert_awaited_with({103: 0, 104: True})

    async def test_brightness_from_auto_uses_saved_manual_mix(self):
        _, _, hub, entity = self.fixture(1)
        await entity.async_turn_on(brightness=128)
        values = hub.set_properties.await_args.args[0]
        self.assertEqual(values, {103: 0, 104: True, 109: 0, 110: 50, 111: 25, 112: 13, 113: 0})

    async def test_auto_selection_changes_only_mode(self):
        hass, entry, hub, _ = self.fixture()
        selector = controls.FluvalChoice(hass, entry, 103)
        await selector.async_select_option('Auto')
        hub.set_properties.assert_awaited_once_with({103: 1})

    async def test_clock_is_opt_in_diagnostic(self):
        hass, entry, _, _ = self.fixture()
        clock = controls.FluvalStatus(hass, entry, 'clock')
        self.assertEqual(clock._attr_entity_category, 'diagnostic')
        self.assertFalse(clock._attr_entity_registry_enabled_default)
