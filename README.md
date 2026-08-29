# Magic Climate

A generic Home Assistant custom integration that wraps any `climate.*` entity to add:

- **UI-configured presets** — define Comfort, Away, Eco, Boost and Sleep bands in HA's options flow. No YAML. No device reflashing.
- **Dual-setpoint UI fix** — auto-detected. Climate entities that support `HEAT_COOL` with `target_temperature_range` get one slider in HEAT/COOL/AUTO and two in HEAT_COOL. The native HA thermostat card otherwise misbehaves for these entities.
- **Fahrenheit normalization** — auto-detected. Source entities that report °F on a °F HA instance no longer trigger HA's double-conversion bug.
- **A Home preset that follows the clock** — Home holds Comfort by default, swaps to Eco during peak hours, to Boost while preloading ahead of them, and to Sleep overnight, still reporting `home` throughout. Pick any of those yourself to pin it.

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

**Settings → Devices & Services → Magic Climate → Configure.** Everything is on
one page, because none of these bands means anything on its own: Eco is Comfort
minus what you will pay for at peak, Boost is what you do to Comfort just before
that, and Sleep is Comfort with nobody watching.

| Section | What it is |
|---|---|
| **Home Comfort** | Your standard band. Home holds it unless something below moves it. |
| **Away** | The band for an empty house. No schedule — wire an away trigger in HA to select it. |
| **Eco energy saver** | The band for when energy costs more, plus the peak window and the switch that has Home swap into it. |
| **Boost** | A band that drives the room past Comfort while power is cheap, plus how many minutes before peak to start. |
| **Sleep** | The overnight band and its window. |

Each band is a low, a high, and an optional fan mode taken from what the wrapped
entity reports in `fan_modes`. Presets carry no HVAC mode: heating or cooling is
a seasonal decision made once for the unit, not something a comfort band should
flip on the way past.

Eco, Boost and Sleep each have a checkbox that creates the preset. Creating one
only adds it to the picker — it starts applying on its own when you also give it
a window (and, for Eco, switch on "Home switches between Comfort and Eco"). Leave
the times blank and the band exists purely to be selected, by you or by an
automation.

Changes persist in HA's config storage and are picked up immediately. No restart.

## How Home resolves

Home holds no band of its own. It resolves by the wall clock, in this order:

| Window open | Home applies |
|---|---|
| Sleep | the Sleep band |
| Peak | the Eco band |
| Preload (the run-up to peak) | the Boost band |
| nothing | the Comfort band |

Sleep outranks peak on purpose: a night band exists because someone is asleep in
the room, and the saving from holding Eco through it is not worth waking up for.
Boost sits under peak only for completeness — the preload window ends exactly
where peak begins, so the two never actually overlap.

`preset_mode` reads `home` the whole time. That is the point: the label never
changes, so nothing downstream sees an event to react to, and drift is measured
against the band really pushed rather than against Comfort. The band in force is
published separately as `effective_preset`.

**Selecting anything else pins it.** Comfort during peak holds Comfort. Eco at
noon holds Eco. That is also what makes a preset with blank times useful — it is
reachable only that way.

While nothing is scheduled, Home *is* the Comfort band, and Comfort is left out
of the picker rather than sitting there as a second name for the same setpoints.

## How presets apply

A preset always declares a band (low + high). When one is applied, the wrapper
pushes setpoints to the source based on the source's **current** HVAC mode:

| Source mode | Pushed |
|---|---|
| `heat_cool` | `target_temp_low` = low, `target_temp_high` = high |
| `auto` | `temperature` = midpoint (the unit applies its own deadband) |
| `heat` | `temperature` = low |
| `cool` / `dry` | `temperature` = high |
| `fan_only` / `off` | nothing |

A declared `fan` is pushed after the temps.

Applying is one-shot — push, then walk away. There is no control loop and no
re-assertion on drift: any manual change clears the preset label, which is also
what stops the schedule from ever fighting a wall thermostat that has taken the
setpoint. The only write the wrapper makes unasked is moving Home across a window
boundary, and that fires only while Home is still the held preset.

The held preset survives a Home Assistant restart. The label alone would be a
claim about hardware nobody was watching, so the push it implies is reconstructed
and handed to the normal drift check. If something moved the setpoint while HA
was down, the preset clears on the first source update, exactly as it would have
without the restart. The one exception is a window boundary crossed while HA was
down, which has no boundary left to fire and is re-applied at startup.

## Published state

| Attribute | Example |
|---|---|
| `effective_preset` | `"eco"` while `preset_mode` reads `"home"` |
| `peak_active` | `true` |
| `peak_start` / `peak_end` | `"16:00:00"` / `"21:00:00"` |
| `next_peak_start` / `next_peak_end` | `"2026-08-29T16:00:00-07:00"` |

Peak boundaries are published as absolute timestamps so an automation can act
ahead of the window. They change at most a few times a day, so they do not churn
the recorder.

## How the dual-setpoint UI fix works

If the source's `supported_features` includes both `TARGET_TEMPERATURE_RANGE` and `HEAT_COOL` in `hvac_modes`, the wrapper masks `TARGET_TEMPERATURE_RANGE` out in modes other than `HEAT_COOL` (and `OFF` for entities that idle from `HEAT_COOL`). The thermostat card renders the right number of sliders for each mode.

Otherwise the wrapper does pure pass-through.

## License

MIT.
