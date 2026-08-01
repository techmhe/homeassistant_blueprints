# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

Neither `python` nor `python3 -m pytest` work directly — `jinja2` and `pytest` only exist
inside Homebrew formula prefixes. Resolve the paths with globs rather than hardcoding
versions; Homebrew bumps them (a hardcoded `pytest/9.0.2` broke once it became `9.0.2_1`).

### Run all tests
```bash
PYTHONPATH=$(ls -d /opt/homebrew/Cellar/jinja2-cli/*/libexec/lib/python3.*/site-packages | tail -1) \
  $(ls -d /opt/homebrew/Cellar/pytest/*/libexec/bin/pytest | tail -1) tests/ -v
```

### Run a single test class / single test
Append `-k TestDeadBand` or use a node ID:
```bash
... tests/test_nulleinspeisung_templates.py::TestDeadBand::test_within_dead_band_default -v
```

### Parse the blueprint YAML (`!input` needs a custom loader)
```bash
PYTHONPATH=$(ls -d /opt/homebrew/Cellar/jinja2-cli/*/libexec/lib/python3.*/site-packages | tail -1) \
  python3 -c "
import yaml
class L(yaml.SafeLoader): pass
L.add_constructor('!input', lambda l,n: ('input', l.construct_scalar(n)))
print(yaml.load(open('marstek_venus_a_zero_feed_in.yaml'), Loader=L).keys())"
```

## Architecture

### Single-file blueprint
The entire automation is in `marstek_venus_a_zero_feed_in.yaml` — a Home Assistant blueprint (YAML) that controls a Marstek Venus A battery via Modbus. The logic is written as Jinja2 templates inside the YAML.

### Control algorithm (zero feed-in branch)
A proportional controller with a ±10 W symmetric dead band around `min_grid_import`:

1. **Read sensors** — grid power (W), PV power (W, 0 if unavailable), SOC (%), current discharge/charge settings
2. **Compute `current_net`** = `discharge_setting - charge_setting` (positive = net discharging)
3. **Dead band check** — if grid is within `[target - 10, target + 10]`, keep current settings unchanged
4. **Adjust** — `new_net = current_net + grid - target` (outside dead band only)
5. **Split** — `new_net > 0` → discharge target; `new_net < 0` → charge target
6. **Discharge delay** — if starting from idle (both settings = 0), wait `discharge_delay` seconds to filter load spikes, then re-read all sensors
7. **Ramp-up** — discharge increases by at most `discharge_step` per cycle; ramp-down is immediate
8. **Force mode** — `discharge` if ramped_discharge > 0; `charge` only if charge_target > 0 **and** PV > 0 (prevents charging from grid); else `stop`
9. **Write to Modbus** — zero the opposing power first, then set the new value, then set force mode

### Control algorithm (max-yield branch)
For setups where every kWh through the AC port is worth the same and a small battery meets
a lot of module power. Ignores the grid sensor, the dead band and the discharge delay —
it follows PV and SOC only. Rule order is binding:

1. `soc <= min_soc` → 0 W, force mode `stop` (protection outranks every dump rule)
2. `dumping` (latched) **or** `pv <= 0` (night) **or** `soc >= max_soc` → `max_discharge_power`
3. otherwise → `min(pv, max_discharge_power)` — lossless pass-through, the MPPT stores only
   the surplus above the cap
4. Same ramp as zero feed-in; force mode is only ever `discharge` or `stop` (never `charge`)

### Four operating modes (choose/default structure)
- **Manual feed-in** (highest priority): fixed discharge at user-specified power, SOC-limited
- **Max yield** (`max_yield_enabled`): algorithm above
- **Zero feed-in**: proportional controller
- **Stop** (default): all toggles off → sets all power to 0 and force mode to stop

### Test architecture
Tests in `tests/test_nulleinspeisung_templates.py` extract each Jinja2 template verbatim from the blueprint and evaluate them using Python's `jinja2` library with minimal `states()` / `is_state()` stubs. The helper `run_zero_feed_in()` simulates one complete control cycle (without delay re-read); `run_max_yield()` does the same for the max-yield branch and returns `dump_helper_next` so multi-cycle tests can feed the latch state forward. `TestDelayAndReRead` separately simulates pre- and post-delay sensor reads with different values.

Because the templates are copied into the test file, they can drift from the blueprint. The
copies are checked by normalising whitespace on both sides and comparing — worth re-running
after editing any template.

### Key design decisions / known pitfalls
- **`max_grid_feed_in` is a dead parameter** — it appears in the UI but is NOT used in the control logic. The dead band is always ±10 W (symmetric). Using it asymmetrically previously caused a regression where discharge reduction got stuck (dead band [-800, +10] prevented reduction at -200 W export).
- **PV sensor unavailable** is treated as 0 W (not a failure) — this is critical so nighttime discharge works when the solar inverter goes offline.
- **Charge has no ramp-up** — intentional; only discharge is hardware-sensitive.
- **The max-yield dump latch needs an `input_boolean` helper** — a blueprint has no memory across runs, and the state cannot be derived from the commanded discharge power: at `pv > max_discharge_power` that value equals `max_discharge_power` too, without any dumping going on. The helper is also visible in HA, which makes the latch debuggable. It is written only on state changes (the automation runs every 10 s).
- **`dump_stop_soc` is clamped to `min_soc`** (`dump_floor`) so a misconfigured value cannot bypass the hard discharge limit.
- **The grid sensor is only required by the zero-feed-in branch** — the availability condition lets the manual and max-yield modes run without it.
- **`mode: single` + `max_exceeded: silent`** — overlapping automation runs are silently dropped to prevent conflicting Modbus writes.
