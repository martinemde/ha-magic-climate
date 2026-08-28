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
2. Pick the source `climate.*` entity to wrap. This is permanent for the entry.
3. (Optional) give the wrapper a display name.

## Defining presets

1. On your Magic Climate entry, click **Configure**.
2. **Basic Options** → pick which of HA's standard presets (Home, Away, Sleep, Eco,
   Comfort, Boost, Activity) to expose. Each enabled preset gets its own menu entry.
   The wrapped entity is not editable here — see below.
3. Open a preset → set its low/high temperatures, and optionally an HVAC mode and fan
   mode. Both are dropdowns of what the wrapped entity reports in `hvac_modes` and
   `fan_modes`, so a preset can only ask for a setting the hardware actually has.
   Leave either unchanged and the preset won't touch it.
4. **Save and exit** when done.

Presets persist in HA's config storage. No restart needed; the wrapper picks up changes immediately.

### Changing the wrapped entity

You can't. The source is fixed when the entry is created: the entry's unique id, the
entity's state subscription, and every stored mode and fan value are all tied to that
one source. To wrap something else, delete the entry and add a new one.

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
