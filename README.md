# Magic Climate

A generic Home Assistant custom integration that wraps any `climate.*` entity to add:

- **UI-configured presets** — define `away`, `home`, `eco`, `sleep`, … in HA's options flow. No YAML. No device reflashing.
- **Dual-setpoint UI fix** — auto-detected. Climate entities that support `HEAT_COOL` with `target_temperature_range` get one slider in HEAT/COOL/AUTO and two in HEAT_COOL. The native HA thermostat card otherwise misbehaves for these entities.
- **Fahrenheit normalization** — auto-detected. Source entities that report °F on a °F HA instance no longer trigger HA's double-conversion bug.
- **Peak-hour Eco substitution** — during a daily peak window, selecting Home quietly applies the Eco band instead, while still reporting `home`.

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
2. **Basic Options** → pick which of HA's standard presets (Home, Away, Sleep,
   Comfort, Boost, Activity) to expose. Each enabled preset gets its own menu entry.
   Eco is not here — it lives on the **Home & Eco** screen, beside the peak settings
   that depend on it. The wrapped entity is not editable here either; see below.
3. **Home & Eco** → both bands on one screen, plus the peak window. See
   [Peak hours](#peak-hours).
4. Open any other preset → set its low/high temperatures, and optionally an HVAC mode
   and fan mode. Both are dropdowns of what the wrapped entity reports in `hvac_modes`
   and `fan_modes`, so a preset can only ask for a setting the hardware actually has.
   Leave either unchanged and the preset won't touch it.
5. **Save and exit** when done.

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

The held preset survives a Home Assistant restart. The label alone would be a
claim about hardware nobody was watching, so the push it implies is reconstructed
at startup and handed to the same drift check — if something moved the setpoint
while HA was down, the preset clears on the first source update, exactly as it
would have without the restart. Restoring does not command the hardware; the one
exception is a peak boundary crossed while HA was down, which has no boundary
left to fire and is reconciled at startup.

## Peak hours

Utilities charge more during a few hours a day. The **Home & Eco** screen turns that
into a rule: while the peak window is open, selecting **Home** pushes the **Eco** band
instead.

The wrapper keeps reporting `home` the whole time. That is the point — the preset never
changes, so no automation, dashboard, or wall thermostat sees an event to react to, and
nothing can feed back into the wrapper's own drift detection.

| | |
|---|---|
| Requires | the Eco preset enabled, on the same screen |
| Applies to | `home` only — every other preset is pushed as configured |
| Choosing Eco yourself | holds Eco until you change it, peak or not |
| Schedule | every day; a window ending before it starts runs overnight |
| Boundaries | half-open — peak is over the instant it ends |

At the start and end of the window the wrapper re-pushes, but only if **Home is still
the held preset**. If anything else has touched the setpoint since, drift detection has
already cleared the preset, and peak leaves it alone until you pick a preset again. This
is the only time the wrapper writes without being asked.

Peak state is published as attributes, with boundaries as timestamps so an automation
can act *ahead* of the window (pre-cooling, say):

| attribute | example |
|---|---|
| `peak_active` | `true` |
| `peak_start` / `peak_end` | `"16:00:00"` / `"21:00:00"` |
| `next_peak_start` / `next_peak_end` | `"2026-08-29T16:00:00-07:00"` |
| `effective_preset` | `"eco"` while `preset_mode` reads `"home"` |

Turning peak off, or disabling Eco, restores plain preset behavior.

## How the dual-setpoint UI fix works

If the source's `supported_features` includes both `TARGET_TEMPERATURE_RANGE` and `HEAT_COOL` in `hvac_modes`, the wrapper masks `TARGET_TEMPERATURE_RANGE` out in modes other than `HEAT_COOL` (and `OFF` for entities that idle from `HEAT_COOL`). The thermostat card renders the right number of sliders for each mode.

Otherwise the wrapper does pure pass-through.

## License

MIT.
