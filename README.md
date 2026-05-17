# Magic Climate

A generic Home Assistant custom integration that wraps any `climate.*` entity to add:

- **UI-configured presets** — define `away`, `home`, `eco`, `sleep`, … in HA's options flow. No YAML. No device reflashing.
- **Dual-setpoint UI fix** — auto-detected. Climate entities that support `HEAT_COOL` with `target_temperature_range` get one slider in HEAT/COOL/AUTO and two in HEAT_COOL. The native HA thermostat card otherwise misbehaves for these entities.
- **Fahrenheit normalization** — auto-detected. Source entities that report °F on a °F HA instance no longer trigger HA's double-conversion bug.

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → Custom repositories.
2. Add `https://github.com/martinemde/ha-magic-climate` as an Integration.
3. Install **Magic Climate**.
4. Restart Home Assistant.

### Manual

Copy `custom_components/magic_climate/` to your HA config directory and restart.

## Setup

1. **Settings → Devices & Services → + Add Integration → Magic Climate.**
2. Pick the source `climate.*` entity to wrap.
3. (Optional) give the wrapper a display name.

## Defining presets

1. On your Magic Climate entry, click **Configure**.
2. **Add preset** → enter a name, low/high temperatures, and optional forced HVAC mode / fan.
3. Repeat for each preset you want. **Save and exit** when done.

Presets persist in HA's config storage. No restart needed; the wrapper picks up changes immediately.

## How presets apply

A preset always declares a comfort *band* (low + high). When you select a preset, the wrapper pushes setpoints to the source based on the source's **current** HVAC mode:

| Source mode | Pushed |
|---|---|
| `HEAT_COOL` | `target_temp_low = low`, `target_temp_high = high` |
| `AUTO` | `temperature = midpoint(low, high)` — unit applies its own deadband |
| `HEAT` | `temperature = low` (heat *to at least*) |
| `COOL` | `temperature = high` (cool *to at most*) |
| `DRY` | `temperature = high` |
| `FAN_ONLY` / `OFF` | no temperature push |

If the preset declares a forced `mode`, that mode is pushed first and then used for the mapping. A forced `fan` is pushed after the temps.

Selection is one-shot. Any manual change clears the preset label.

## How the dual-setpoint UI fix works

If the source's `supported_features` includes both `TARGET_TEMPERATURE_RANGE` and `HEAT_COOL` in `hvac_modes`, the wrapper masks `TARGET_TEMPERATURE_RANGE` out in modes other than `HEAT_COOL` (and `OFF` for entities that idle from `HEAT_COOL`). The thermostat card renders the right number of sliders for each mode.

Otherwise the wrapper does pure pass-through.

## License

MIT.
