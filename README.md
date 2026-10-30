# Fluval Connect for Home Assistant

Community integration and aquarium dashboard card for the **Wi-Fi Aquasky 3.0**, tested with firmware 10 in the UK/EU cloud region. This is an independent project and is not affiliated with Fluval or Hagen.

**Experimental, advanced setup:** a working, authorized Fluval cloud connection profile is required. The project does not distribute manufacturer signing credentials or provide a way to obtain them. A Fluval account password alone is insufficient for a new installation. Other models, Bluetooth-only lights and other cloud regions are untested.

## Controls

- Power and RGBW brightness, Manual / Auto / Pro modes.
- Twelve individual Manual, daytime and nighttime colour controls.
- Sunrise and sunset start/end, night-off time, complete 2–12 point Pro timeline.
- Four saved colour presets, numbered weather effects and daylight saving.
- Firmware, device clock, Auto and Pro schedule readouts, read-only scheduled weather.
- Branded dark dashboard card with compact pastel panels and a responsive layout.

Power changes preserve the current mode. Manual colour, brightness, preset and weather changes select Manual mode. Editing Auto or Pro schedules preserves mode and power. Commands wait up to 15 seconds for matching device reports. Clock/timezone, reset and firmware update commands are not exposed. A response confirms the device's reported setting, not independent measurement of emitted light.

## Install

Requires Home Assistant 2026.3 or newer and internet access to the Fluval EU cloud. This uses MQTT over TLS; it is **not local Wi-Fi control**.

1. In HACS, open **Custom repositories**, add `https://github.com/koxy333/fluval-connect` with category **Integration**, and download Fluval Connect. Alternatively copy `custom_components/fluval_connect` into your Home Assistant `custom_components` directory.
2. Restart Home Assistant.
3. Under **Settings → Devices & services → Add integration**, select **Fluval Connect**.
4. Supply your authorized broker instance ID, access key and signing key. These remain in Home Assistant's configuration entry and must be treated as secrets.
5. Sign into your Fluval account to verify ownership, then select your light. The password and sign-in token are used transiently and are not saved. Fluval may sign the phone app out when a new session starts. Setup performs a read-only connectivity check.

Existing version 1 installations migrate their selected device's private profile into the configuration entry. Before replacing a personal installation through HACS, back up its `_private_cloud.json` outside the integration folder as `/config/fluval_connect_private.json`. Keep this backup private. Back up Home Assistant before updating.

HACS custom repository installation is separate from inclusion in the default HACS catalogue. Catalogue availability is subject to HACS review.

## Dashboard card

The integration loads the card automatically. After installation, refresh your browser. Add a manual card:

```yaml
type: custom:fluval-connect-card
name: Aquarium light
title: Aquarium
location: Aquarium
device_id: YOUR_HOME_ASSISTANT_DEVICE_ID
```

`name` must match the Home Assistant device/entity naming prefix; `device_id` is the Home Assistant device ID, not the MAC. A panel view gives the card room to display both columns. The Pro editor keeps a local draft until **Save timeline**; **Cancel** sends nothing. Advanced colour and weather controls are folded away.

For a standalone card installation, copy `frontend/fluval-connect-card.js` from the integration directory into `/config/www`, then add `/local/fluval-connect-card.js` as a JavaScript module dashboard resource. Use one loading method; remove any old manually registered card resource before switching to the integration's automatic loading.

## Actions

`fluval_connect.set_pro_schedule` accepts a Home Assistant `device_id` and `points`, each containing `time` (`HH:MM`) and integer `red`, `green`, `blue`, `white` percentages. Supply the whole timeline with strictly increasing times.

`fluval_connect.set_auto_schedule` accepts `device_id` and `schedule`: `sunrise_start`, `sunrise_end`, `sunset_start`, `sunset_end`, `night_off` (null disables it), and `day` / `night` objects with the same four colour percentages.

## Validation and limitations

Run `python -m unittest discover -s tests -v` and `node --test tests/card.test.cjs`. GitHub Actions also runs HACS validation and Home Assistant hassfest. Tests use synthetic device identifiers and mocked transport; they do not control an aquarium. The dashboard has been visually checked in Home Assistant, including narrow layouts, refresh, Pro draft editing and cancellation. Expanded writes have offline protocol and service-dispatch tests; physical validation of every control is still outstanding.

If the light is unavailable, verify Wi-Fi in FluvalConnect, internet access and the selected cloud region. An Espressif router entry alone does not establish device identity. Report issues without credentials, tokens or full diagnostics containing private connection information.

## Attribution

Code is MIT licensed. Fluval names and logos belong to their respective owners and are used to identify the supported product; they are not covered by the code's MIT licence. The wordmark source is [Fluval aquarium on Seeklogo](https://seeklogo.com/vector-logo/521543/fluval-aquarium); the integration icon identifies the FluvalConnect app.
