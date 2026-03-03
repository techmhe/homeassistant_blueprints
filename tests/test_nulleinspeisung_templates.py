"""
Tests for the Marstek Venus A Nulleinspeisung (Zero Feed-In) blueprint.

These tests validate the Jinja2 template logic used in the blueprint by
simulating various scenarios (nighttime discharge, PV charging, SOC limits,
ramp-up, dead band, etc.) and checking that the computed variables and
force mode are correct.

The tests use the standard Jinja2 library with a minimal Home Assistant-like
`states()` function injected into the template context.
"""

import math
import pytest
from jinja2 import Environment


# ---------------------------------------------------------------------------
# Jinja2 environment that mimics Home Assistant's template engine
# ---------------------------------------------------------------------------
_env = Environment()


def render_template(template_str: str, context: dict) -> str:
    """Render a Jinja2 template string with the given context variables."""
    tpl = _env.from_string(template_str)
    return tpl.render(**context).strip()


def make_states(entity_values: dict):
    """
    Return a callable ``states(entity_id)`` function that looks up entity
    values from a dict. Unknown entities return 'unavailable'.
    """
    def states(entity_id):
        return str(entity_values.get(entity_id, "unavailable"))
    return states


# ---------------------------------------------------------------------------
# Default config values (can be overridden per-test)
# ---------------------------------------------------------------------------
DEFAULT_CONFIG = dict(
    min_grid_import_value=0,
    dead_band_value=10,
    max_discharge_value=800,
    max_charge_value=800,
    min_soc_value=10,
    max_soc_value=100,
    recovery_soc_value=0,
    discharge_delay_value=3,
    discharge_step_value=200,
)


def build_context(entity_values: dict, config_overrides: dict | None = None):
    """Build a full template context dict from entity values and config."""
    cfg = {**DEFAULT_CONFIG, **(config_overrides or {})}
    ctx = {
        "states": make_states(entity_values),
        "grid_power_entity": "sensor.grid_power",
        "pv_power_entity": "sensor.pv_power",
        "soc_entity": "sensor.soc",
        "discharge_entity": "number.discharge",
        "charge_entity": "number.charge",
        "force_mode_entity": "select.force_mode",
        "manual_power_entity": "input_number.manual_power",
        **cfg,
    }
    return ctx


# ===================================================================
# Helper: evaluate the zero-feed-in calculation chain
# ===================================================================

# Templates extracted from the blueprint (zero feed-in branch).
# They are evaluated in order, each building on the previous variables.

TPL_PV_POWER = """
{% set pv_raw = states(pv_power_entity) %}
{{ pv_raw | float(0) if pv_raw not in ['unknown', 'unavailable'] else 0 }}
"""

TPL_CURRENT_DISCHARGE = """
{% set raw = states(discharge_entity) %}
{{ raw | float(0) if raw not in ['unknown', 'unavailable'] else 0 }}
"""

TPL_CURRENT_CHARGE = """
{% set raw = states(charge_entity) %}
{{ raw | float(0) if raw not in ['unknown', 'unavailable'] else 0 }}
"""

TPL_CURRENT_NET = "{{ current_discharge | float(0) - current_charge | float(0) }}"

TPL_NEW_NET = """
{%- set g = grid | float(0) -%}
{%- set net = current_net | float(0) -%}
{%- set lo = lower_bound | float(0) -%}
{%- set hi = upper_bound | float(0) -%}
{%- if g > hi or g < lo -%}
  {{ net + g - target_import | float(0) }}
{%- else -%}
  {{ net }}
{%- endif -%}
"""

TPL_DISCHARGE_TARGET = """
{%- if soc | float(0) >= max_soc_value | float(0) and pv_power | float(0) > 0 -%}
  {{ [pv_power | float(0), max_discharge_value | float(0)] | min }}
{%- else -%}
  {%- set net = new_net | float(0) -%}
  {%- if net > 0 -%}
    {%- if soc | float(0) <= min_soc_value | float(0) -%}
      {{ 0 }}
    {%- else -%}
      {{ [net, max_discharge_value | float(0)] | min }}
    {%- endif -%}
  {%- else -%}
    {{ 0 }}
  {%- endif -%}
{%- endif -%}
"""

# Post-delay version: uses soc_current / pv_current / new_net_final
TPL_DISCHARGE_TARGET_FINAL = """
{%- if soc_current | float(0) >= max_soc_value | float(0) and pv_current | float(0) > 0 -%}
  {{ [pv_current | float(0), max_discharge_value | float(0)] | min }}
{%- else -%}
  {%- set net = new_net_final | float(0) -%}
  {%- if net > 0 -%}
    {%- if soc_current | float(0) <= min_soc_value | float(0) -%}
      {{ 0 }}
    {%- else -%}
      {{ [net, max_discharge_value | float(0)] | min }}
    {%- endif -%}
  {%- else -%}
    {{ 0 }}
  {%- endif -%}
{%- endif -%}
"""

TPL_CHARGE_TARGET = """
{%- set rec = recovery_soc_value | float(0) -%}
{%- set s = soc | float(0) -%}
{%- if rec > 0 and s <= rec -%}
  {{ max_charge_value | float(0) }}
{%- else -%}
  {%- set net = new_net | float(0) -%}
  {%- if net < 0 -%}
    {%- if s >= max_soc_value | float(0) -%}
      {{ 0 }}
    {%- else -%}
      {{ [net | abs, max_charge_value | float(0)] | min }}
    {%- endif -%}
  {%- else -%}
    {{ 0 }}
  {%- endif -%}
{%- endif -%}
"""

TPL_STARTING_DISCHARGE = """
{{ discharge_target | float(0) > 0 and
   current_discharge | float(0) == 0 and
   current_charge | float(0) == 0 }}
"""

TPL_RAMPED_DISCHARGE = """
{%- set target = discharge_target_final | float(0) -%}
{%- set current = current_discharge_actual | float(0) -%}
{%- set step = discharge_step_value | float(0) -%}
{%- if target > current -%}
  {{ [target, current + step] | min }}
{%- else -%}
  {{ target }}
{%- endif -%}
"""

TPL_FORCE_MODE = """
{%- set rec = recovery_soc_value | float(0) -%}
{%- set recovery_active = rec > 0 and soc_current | float(0) <= rec -%}
{%- if recovery_active -%}
  charge
{%- elif ramped_discharge | float(0) > 0 -%}
  discharge
{%- else -%}
  stop
{%- endif -%}
"""

TPL_MANUAL_EFFECTIVE_DISCHARGE = """
{%- if soc <= min_soc_value | float(0) -%}
  {{ 0 }}
{%- else -%}
  {{ [manual_power, max_discharge_value | float(0)] | min }}
{%- endif -%}
"""

TPL_MANUAL_FORCE_MODE = """
{%- if effective_discharge | float(0) > 0 -%}
  discharge
{%- else -%}
  stop
{%- endif -%}
"""


def run_zero_feed_in(
    grid: float,
    pv: float | str,
    soc: float,
    discharge_setting: float | str,
    charge_setting: float | str,
    config: dict | None = None,
):
    """
    Simulate one full zero-feed-in cycle (without delay re-read, using
    same values for both pre- and post-delay reads).

    Returns a dict with all key computed variables.
    """
    entities = {
        "sensor.grid_power": grid,
        "sensor.pv_power": pv,
        "sensor.soc": soc,
        "number.discharge": discharge_setting,
        "number.charge": charge_setting,
    }
    ctx = build_context(entities, config)

    # Step 1: read state
    grid_val = float(render_template("{{ states(grid_power_entity) | float(0) }}", ctx))
    pv_val = float(render_template(TPL_PV_POWER, ctx))
    soc_val = float(render_template("{{ states(soc_entity) | float(0) }}", ctx))
    cur_d = float(render_template(TPL_CURRENT_DISCHARGE, ctx))
    cur_c = float(render_template(TPL_CURRENT_CHARGE, ctx))

    ctx["grid"] = grid_val
    ctx["pv_power"] = pv_val
    ctx["soc"] = soc_val
    ctx["current_discharge"] = cur_d
    ctx["current_charge"] = cur_c

    # Step 2: calculate targets
    current_net = float(render_template(TPL_CURRENT_NET, ctx))
    ctx["current_net"] = current_net

    target_import = float(ctx["min_grid_import_value"])
    ctx["target_import"] = target_import
    tolerance = float(ctx["dead_band_value"])
    ctx["tolerance"] = tolerance
    lower_bound = target_import - tolerance
    upper_bound = target_import + tolerance
    ctx["lower_bound"] = lower_bound
    ctx["upper_bound"] = upper_bound

    new_net = float(render_template(TPL_NEW_NET, ctx))
    ctx["new_net"] = new_net

    discharge_target = float(render_template(TPL_DISCHARGE_TARGET, ctx))
    ctx["discharge_target"] = discharge_target

    charge_target = float(render_template(TPL_CHARGE_TARGET, ctx))
    ctx["charge_target"] = charge_target

    starting_discharge_str = render_template(TPL_STARTING_DISCHARGE, ctx)
    starting_discharge = starting_discharge_str.strip().lower() == "true"

    # For this test, we simulate "no delay" — use same values for post-delay read
    ctx["grid_current"] = grid_val
    ctx["soc_current"] = soc_val
    ctx["pv_current"] = pv_val
    ctx["current_discharge_actual"] = cur_d
    ctx["current_charge_actual"] = cur_c
    ctx["current_net_actual"] = current_net
    ctx["new_net_final"] = new_net

    discharge_target_final = float(render_template(TPL_DISCHARGE_TARGET_FINAL, ctx))
    ctx["discharge_target_final"] = discharge_target_final
    ctx["charge_target_final"] = charge_target

    ramped_discharge = float(render_template(TPL_RAMPED_DISCHARGE, ctx))
    ctx["ramped_discharge"] = ramped_discharge

    force_mode = render_template(TPL_FORCE_MODE, ctx)
    ctx["force_mode_option"] = force_mode

    # Reflect the fixed Modbus write behaviour: charge entity receives
    # charge_target only when force_mode == 'charge'; 0 otherwise.
    written_charge = charge_target if force_mode == "charge" else 0.0

    return {
        "grid": grid_val,
        "pv_power": pv_val,
        "soc": soc_val,
        "current_discharge": cur_d,
        "current_charge": cur_c,
        "current_net": current_net,
        "lower_bound": lower_bound,
        "upper_bound": upper_bound,
        "new_net": new_net,
        "discharge_target": discharge_target,
        "discharge_target_final": discharge_target_final,
        "charge_target": charge_target,
        "written_charge": written_charge,
        "starting_discharge": starting_discharge,
        "ramped_discharge": ramped_discharge,
        "force_mode": force_mode,
    }


# ===================================================================
# TESTS
# ===================================================================


class TestPVSensorUnavailable:
    """Critical bug fix: PV sensor unavailable should NOT block the automation."""

    def test_pv_unavailable_treated_as_zero(self):
        """PV unavailable → pv_power should be 0, not block execution."""
        result = run_zero_feed_in(
            grid=300, pv="unavailable", soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["pv_power"] == 0.0

    def test_pv_unknown_treated_as_zero(self):
        """PV unknown → pv_power should be 0."""
        result = run_zero_feed_in(
            grid=300, pv="unknown", soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["pv_power"] == 0.0

    def test_nighttime_discharge_with_pv_unavailable(self):
        """At night: PV unavailable, grid importing → battery should discharge."""
        result = run_zero_feed_in(
            grid=500, pv="unavailable", soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["ramped_discharge"] > 0
        assert result["force_mode"] == "discharge"

    def test_nighttime_no_charge_without_pv(self):
        """At night: PV unavailable → force mode should NOT be 'charge'."""
        result = run_zero_feed_in(
            grid=-200, pv="unavailable", soc=50,
            discharge_setting=0, charge_setting=0
        )
        # Even though grid exports, without PV the mode should be stop (not charge from grid)
        assert result["force_mode"] == "stop"

    def test_discharge_entity_unavailable_treated_as_zero(self):
        """Discharge entity unavailable → treated as 0."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting="unavailable", charge_setting=0
        )
        assert result["current_discharge"] == 0.0

    def test_charge_entity_unavailable_treated_as_zero(self):
        """Charge entity unavailable → treated as 0."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting=0, charge_setting="unavailable"
        )
        assert result["current_charge"] == 0.0


class TestZeroFeedInBasicScenarios:
    """Test basic zero-feed-in control scenarios."""

    def test_idle_grid_importing_should_discharge(self):
        """Battery idle, grid importing 300W → should start discharging."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["new_net"] == 300.0
        assert result["discharge_target"] == 300.0
        # Ramped from 0: min(300, 0+200) = 200
        assert result["ramped_discharge"] == 200.0
        assert result["force_mode"] == "discharge"

    def test_idle_grid_exporting_with_pv_mppt_charges(self):
        """Battery idle, grid exporting 300W, PV active → MPPT charges automatically.

        force_mode stays 'stop' because AC-side grid charging is not needed when
        PV is available. The MPPT (DC side) handles PV → battery charging autonomously.
        force_mode='charge' is reserved for recovery (grid charging) only.
        """
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["new_net"] == -300.0
        assert result["charge_target"] == 300.0
        assert result["ramped_discharge"] == 0.0
        assert result["force_mode"] == "stop"

    def test_grid_within_dead_band_no_change(self):
        """Grid at 5W (within [0, 10] dead band) → no adjustment."""
        result = run_zero_feed_in(
            grid=5, pv=0, soc=80,
            discharge_setting=300, charge_setting=0
        )
        # Grid 5W is within [0, 10], so new_net stays at current_net=300
        assert result["new_net"] == 300.0
        assert result["discharge_target"] == 300.0
        assert result["ramped_discharge"] == 300.0  # no ramp needed (same as current)

    def test_grid_exactly_at_zero(self):
        """Grid at 0W (edge of dead band) → should keep current state."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=80,
            discharge_setting=500, charge_setting=0
        )
        # 0 is at the lower_bound=0 boundary, which is NOT < 0, so it's within dead band
        assert result["new_net"] == 500.0  # keep current

    def test_grid_slightly_negative_within_band(self):
        """Grid at -5W (within [-10, 10] dead band) → no adjustment."""
        result = run_zero_feed_in(
            grid=-5, pv=0, soc=80,
            discharge_setting=500, charge_setting=0
        )
        # -5 is within [-10, 10] dead band → keep current
        assert result["new_net"] == 500.0
        assert result["discharge_target"] == 500.0

    def test_battery_fully_discharging_grid_near_zero(self):
        """Battery discharging 500W, grid at 0W → maintain state."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=80,
            discharge_setting=500, charge_setting=0
        )
        assert result["ramped_discharge"] == 500.0
        assert result["force_mode"] == "discharge"


class TestSOCProtection:
    """Test SOC-based limits."""

    def test_low_soc_prevents_discharge(self):
        """SOC at min (10%) → should NOT discharge even if grid is importing."""
        result = run_zero_feed_in(
            grid=500, pv=0, soc=10,
            discharge_setting=0, charge_setting=0
        )
        assert result["discharge_target"] == 0.0
        assert result["ramped_discharge"] == 0.0
        assert result["force_mode"] == "stop"

    def test_soc_below_min_prevents_discharge(self):
        """SOC below min (5%) → should NOT discharge."""
        result = run_zero_feed_in(
            grid=500, pv=0, soc=5,
            discharge_setting=0, charge_setting=0
        )
        assert result["discharge_target"] == 0.0
        assert result["force_mode"] == "stop"

    def test_soc_just_above_min_allows_discharge(self):
        """SOC just above min (11%) → should discharge normally."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=11,
            discharge_setting=0, charge_setting=0
        )
        assert result["discharge_target"] == 300.0
        assert result["ramped_discharge"] > 0

    def test_high_soc_triggers_pv_passthrough(self):
        """SOC at max (100%) with PV → charge blocked, PV discharged to house/grid.

        Battery is full; MPPT cannot absorb more PV. The failsafe discharges at
        PV power through the AC inverter so solar energy is not curtailed.
        """
        result = run_zero_feed_in(
            grid=-500, pv=600, soc=100,
            discharge_setting=0, charge_setting=0
        )
        assert result["charge_target"] == 0.0
        # PV pass-through: discharge_target_final = min(600, 800) = 600
        assert result["discharge_target_final"] == 600.0
        # Ramped from 0: min(600, 0+200) = 200
        assert result["ramped_discharge"] == 200.0
        assert result["force_mode"] == "discharge"

    def test_soc_just_below_max_pv_charges_via_mppt(self):
        """SOC just below max (99%) with PV surplus → MPPT charges automatically.

        force_mode='stop' because no recovery is active; MPPT handles PV charging.
        """
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=99,
            discharge_setting=0, charge_setting=0
        )
        assert result["charge_target"] == 300.0
        assert result["force_mode"] == "stop"

    def test_full_soc_pv_producing_triggers_passthrough(self):
        """SOC=100%, PV producing → PV pass-through to house/grid via AC inverter.

        Battery is full, so MPPT would otherwise curtail PV. The failsafe sets
        discharge_target = pv_power so the AC inverter routes PV to house/grid.
        """
        result = run_zero_feed_in(
            grid=-500, pv=500, soc=100,
            discharge_setting=0, charge_setting=0
        )
        assert result["new_net"] == -500.0      # controller normally wants to charge
        assert result["charge_target"] == 0.0   # blocked by max SOC
        # PV pass-through overrides: discharge_target_final = min(500, 800) = 500
        assert result["discharge_target_final"] == 500.0
        assert result["ramped_discharge"] == 200.0  # ramped from 0
        assert result["force_mode"] == "discharge"

    def test_below_min_soc_no_pv_stays_stopped(self):
        """SOC below min (18% < 20%), grid importing, no PV → stop, no recovery.

        The controller wants to discharge (new_net > 0) but SOC protection
        blocks it. charge_target stays 0 because new_net >= 0. Battery is idle.
        """
        result = run_zero_feed_in(
            grid=300, pv=0, soc=18,
            discharge_setting=0, charge_setting=0,
            config={"min_soc_value": 20}
        )
        assert result["discharge_target"] == 0.0
        assert result["charge_target"] == 0.0
        assert result["ramped_discharge"] == 0.0
        assert result["force_mode"] == "stop"

    def test_below_min_soc_with_pv_surplus_mppt_charges(self):
        """SOC below min (18% < 20%), grid exporting, PV active → MPPT charges.

        min_soc only blocks discharge. PV surplus charges the battery automatically
        via MPPT (DC side). force_mode stays 'stop' — no AC-side grid charging needed.
        """
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=18,
            discharge_setting=0, charge_setting=0,
            config={"min_soc_value": 20}
        )
        assert result["discharge_target"] == 0.0
        assert result["charge_target"] == 300.0
        assert result["force_mode"] == "stop"

    def test_above_min_soc_after_recovery_allows_discharge(self):
        """SOC just above min (21% > 20%), grid importing → discharge resumes.

        Once SOC crosses back above min_soc the protection lifts immediately
        on the next cycle.
        """
        result = run_zero_feed_in(
            grid=300, pv=0, soc=21,
            discharge_setting=0, charge_setting=0,
            config={"min_soc_value": 20}
        )
        assert result["discharge_target"] == 300.0
        assert result["ramped_discharge"] > 0
        assert result["force_mode"] == "discharge"


class TestRampUp:
    """Test discharge ramp-up logic."""

    def test_ramp_from_zero(self):
        """Starting from 0, should ramp up by discharge_step (200W)."""
        result = run_zero_feed_in(
            grid=500, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["ramped_discharge"] == 200.0

    def test_ramp_from_200(self):
        """Starting from 200W, should ramp to 400W."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting=200, charge_setting=0
        )
        # current_net = 200, grid = 300 > 10 → new_net = 200+300-0 = 500
        # discharge_target = 500, ramped = min(500, 200+200) = 400
        assert result["ramped_discharge"] == 400.0

    def test_ramp_target_below_step(self):
        """Target 150W from 0 → should ramp to 150W (target < step)."""
        result = run_zero_feed_in(
            grid=150, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        # new_net = 150, ramped = min(150, 0+200) = 150
        assert result["ramped_discharge"] == 150.0

    def test_ramp_down_is_immediate(self):
        """Reducing discharge is immediate (no ramp-down limit)."""
        result = run_zero_feed_in(
            grid=-50, pv=0, soc=80,
            discharge_setting=500, charge_setting=0
        )
        # current_net=500, grid=-50 < 0 → new_net = 500+(-50)-0 = 450
        # ramped: target=450 < current=500 → 450 (immediate)
        assert result["ramped_discharge"] == 450.0

    def test_custom_step_size(self):
        """Custom step size of 100W."""
        result = run_zero_feed_in(
            grid=500, pv=0, soc=80,
            discharge_setting=0, charge_setting=0,
            config={"discharge_step_value": 100}
        )
        assert result["ramped_discharge"] == 100.0

    def test_ramp_at_max_discharge(self):
        """Already at max discharge, should not exceed."""
        result = run_zero_feed_in(
            grid=100, pv=0, soc=80,
            discharge_setting=800, charge_setting=0
        )
        # new_net = 800+100-0 = 900, but discharge capped at 800
        assert result["discharge_target"] <= 800.0
        assert result["ramped_discharge"] <= 800.0


class TestDeadBand:
    """Test the dead band (tolerance) behavior."""

    def test_within_dead_band_default(self):
        """Grid 5W, target 0, dead band [-10, 10] → no change."""
        result = run_zero_feed_in(
            grid=5, pv=0, soc=80,
            discharge_setting=200, charge_setting=0
        )
        assert result["new_net"] == 200.0  # unchanged

    def test_above_dead_band(self):
        """Grid 15W, target 0, dead band [0, 10] → adjust."""
        result = run_zero_feed_in(
            grid=15, pv=0, soc=80,
            discharge_setting=200, charge_setting=0
        )
        # 15 > 10 → new_net = 200 + 15 - 0 = 215
        assert result["new_net"] == 215.0

    def test_below_dead_band(self):
        """Grid -15W, target 0, dead band [-10, 10] → adjust."""
        result = run_zero_feed_in(
            grid=-15, pv=0, soc=80,
            discharge_setting=200, charge_setting=0
        )
        # -15 < -10 → new_net = 200 + (-15) - 0 = 185
        assert result["new_net"] == 185.0

    def test_custom_target_dead_band(self):
        """Custom target=50 → dead band [40, 60]."""
        result = run_zero_feed_in(
            grid=45, pv=0, soc=80,
            discharge_setting=200, charge_setting=0,
            config={"min_grid_import_value": 50}
        )
        # dead band = [50-10, 50+10] = [40, 60]
        # grid=45 is within [40, 60] → no change
        assert result["lower_bound"] == 40.0
        assert result["upper_bound"] == 60.0
        assert result["new_net"] == 200.0

    def test_custom_target_above_band(self):
        """Custom target=50, grid=70, dead band [40, 60] → adjust."""
        result = run_zero_feed_in(
            grid=70, pv=0, soc=80,
            discharge_setting=200, charge_setting=0,
            config={"min_grid_import_value": 50}
        )
        # 70 > 60 → new_net = 200 + 70 - 50 = 220
        assert result["new_net"] == 220.0

    def test_custom_target_below_band(self):
        """Custom target=50, grid=20, dead band [40, 60] → reduce discharge."""
        result = run_zero_feed_in(
            grid=20, pv=0, soc=80,
            discharge_setting=200, charge_setting=0,
            config={"min_grid_import_value": 50}
        )
        # 20 < 40 → new_net = 200 + 20 - 50 = 170
        assert result["lower_bound"] == 40.0
        assert result["upper_bound"] == 60.0
        assert result["new_net"] == 170.0
        assert result["discharge_target"] == 170.0

    def test_custom_dead_band(self):
        """Custom dead band of 30W → dead band [-30, 30]; grid=20 is within → no change."""
        result = run_zero_feed_in(
            grid=20, pv=0, soc=80,
            discharge_setting=300, charge_setting=0,
            config={"dead_band_value": 30}
        )
        assert result["lower_bound"] == -30.0
        assert result["upper_bound"] == 30.0
        assert result["new_net"] == 300.0  # unchanged, grid within band

    def test_grid_at_exact_upper_bound_stays_inside(self):
        """Grid exactly at upper bound → still inside dead band (strict > comparison)."""
        result = run_zero_feed_in(
            grid=10, pv=0, soc=80,
            discharge_setting=300, charge_setting=0
        )
        # g=10, hi=10: condition is g > hi → 10 > 10 = False → inside band, no change
        assert result["new_net"] == 300.0

    def test_grid_at_exact_lower_bound_stays_inside(self):
        """Grid exactly at lower bound → still inside dead band (strict < comparison)."""
        result = run_zero_feed_in(
            grid=-10, pv=0, soc=80,
            discharge_setting=300, charge_setting=0
        )
        # g=-10, lo=-10: condition is g < lo → -10 < -10 = False → inside band, no change
        assert result["new_net"] == 300.0

    def test_grid_one_watt_above_upper_bound_adjusts(self):
        """Grid 1W above upper bound → outside dead band, adjustment is made."""
        result = run_zero_feed_in(
            grid=11, pv=0, soc=80,
            discharge_setting=300, charge_setting=0
        )
        # 11 > 10 → new_net = 300 + 11 - 0 = 311
        assert result["new_net"] == 311.0

    def test_grid_one_watt_below_lower_bound_adjusts(self):
        """Grid 1W below lower bound → outside dead band, adjustment is made."""
        result = run_zero_feed_in(
            grid=-11, pv=0, soc=80,
            discharge_setting=300, charge_setting=0
        )
        # -11 < -10 → new_net = 300 + (-11) - 0 = 289
        assert result["new_net"] == 289.0


class TestPowerLimits:
    """Test max discharge and charge power limits."""

    def test_discharge_capped_at_max(self):
        """Discharge target should not exceed max_discharge_value."""
        result = run_zero_feed_in(
            grid=1500, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["discharge_target"] == 800.0  # capped at default max

    def test_charge_capped_at_max(self):
        """Charge target should not exceed max_charge_value."""
        result = run_zero_feed_in(
            grid=-1500, pv=2000, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["charge_target"] == 800.0  # capped at default max

    def test_custom_max_discharge(self):
        """Custom max discharge of 400W."""
        result = run_zero_feed_in(
            grid=600, pv=0, soc=80,
            discharge_setting=0, charge_setting=0,
            config={"max_discharge_value": 400}
        )
        assert result["discharge_target"] == 400.0

    def test_custom_max_charge(self):
        """Custom max charge of 500W."""
        result = run_zero_feed_in(
            grid=-600, pv=800, soc=50,
            discharge_setting=0, charge_setting=0,
            config={"max_charge_value": 500}
        )
        assert result["charge_target"] == 500.0


class TestForceMode:
    """Test force mode determination."""

    def test_force_mode_discharge(self):
        """Positive discharge target → discharge mode."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["force_mode"] == "discharge"

    def test_force_mode_stop_with_pv_surplus(self):
        """Charge target > 0 and PV > 0 → stop (MPPT handles PV charging automatically).

        force_mode='charge' is reserved for recovery (grid charging) only.
        """
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["force_mode"] == "stop"

    def test_force_mode_stop_charge_without_pv(self):
        """Charge target > 0 but PV = 0 → stop (don't charge from grid)."""
        result = run_zero_feed_in(
            grid=-300, pv=0, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["force_mode"] == "stop"

    def test_force_mode_stop_when_idle(self):
        """No discharge or charge needed → stop."""
        result = run_zero_feed_in(
            grid=5, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        # Grid within dead band [0, 10], net=0 → all zero
        assert result["force_mode"] == "stop"

    def test_force_mode_no_whitespace(self):
        """Force mode output should not contain leading/trailing whitespace."""
        for grid, pv, expected in [
            (300, 0, "discharge"),
            (-300, 500, "stop"),   # PV surplus: MPPT handles, force_mode=stop
            (5, 0, "stop"),
        ]:
            result = run_zero_feed_in(
                grid=grid, pv=pv, soc=50,
                discharge_setting=0, charge_setting=0
            )
            assert result["force_mode"] == expected
            assert result["force_mode"] == result["force_mode"].strip()


class TestManualFeedIn:
    """Test manual feed-in mode templates."""

    def test_manual_discharge_normal(self):
        """Manual discharge at specified power level."""
        entities = {
            "sensor.soc": 80,
            "input_number.manual_power": 400,
        }
        ctx = build_context(entities)
        ctx["soc"] = 80
        ctx["manual_power"] = 400

        effective = float(render_template(TPL_MANUAL_EFFECTIVE_DISCHARGE, ctx))
        assert effective == 400.0

        ctx["effective_discharge"] = effective
        mode = render_template(TPL_MANUAL_FORCE_MODE, ctx)
        assert mode == "discharge"

    def test_manual_discharge_capped_at_max(self):
        """Manual discharge capped at max_discharge_value."""
        entities = {
            "sensor.soc": 80,
            "input_number.manual_power": 1000,
        }
        ctx = build_context(entities)
        ctx["soc"] = 80
        ctx["manual_power"] = 1000

        effective = float(render_template(TPL_MANUAL_EFFECTIVE_DISCHARGE, ctx))
        assert effective == 800.0  # capped at default max

    def test_manual_discharge_blocked_by_low_soc(self):
        """Manual discharge blocked when SOC is at or below minimum."""
        entities = {
            "sensor.soc": 10,
            "input_number.manual_power": 400,
        }
        ctx = build_context(entities)
        ctx["soc"] = 10
        ctx["manual_power"] = 400

        effective = float(render_template(TPL_MANUAL_EFFECTIVE_DISCHARGE, ctx))
        assert effective == 0.0

        ctx["effective_discharge"] = effective
        mode = render_template(TPL_MANUAL_FORCE_MODE, ctx)
        assert mode == "stop"

    def test_manual_discharge_zero_power(self):
        """Manual discharge with 0W → should stop."""
        entities = {
            "sensor.soc": 80,
            "input_number.manual_power": 0,
        }
        ctx = build_context(entities)
        ctx["soc"] = 80
        ctx["manual_power"] = 0

        effective = float(render_template(TPL_MANUAL_EFFECTIVE_DISCHARGE, ctx))
        assert effective == 0.0

        ctx["effective_discharge"] = effective
        mode = render_template(TPL_MANUAL_FORCE_MODE, ctx)
        assert mode == "stop"


class TestStartingDischarge:
    """Test the starting_discharge detection for delay."""

    def test_starting_from_idle(self):
        """Starting discharge from idle state → True."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["starting_discharge"] is True

    def test_already_discharging(self):
        """Already discharging → False (no delay needed)."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting=200, charge_setting=0
        )
        assert result["starting_discharge"] is False

    def test_currently_charging(self):
        """Currently charging → False (charge entity != 0)."""
        result = run_zero_feed_in(
            grid=300, pv=0, soc=80,
            discharge_setting=0, charge_setting=200
        )
        # Discharge target > 0 but current_charge != 0
        assert result["starting_discharge"] is False

    def test_no_discharge_needed(self):
        """Grid within dead band, no discharge needed → False."""
        result = run_zero_feed_in(
            grid=5, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["starting_discharge"] is False


class TestMultiCycleSimulation:
    """Simulate multiple control loop cycles to verify convergence."""

    def test_ramp_up_to_steady_state(self):
        """Simulate multiple cycles: battery ramps up to match load."""
        house_load = 500  # constant 500W house consumption
        pv = 0
        soc = 80
        cur_discharge = 0.0
        cur_charge = 0.0
        step = 200

        for cycle in range(10):
            # Grid = house_load - battery_discharge + battery_charge - PV
            grid = house_load - cur_discharge + cur_charge - pv

            result = run_zero_feed_in(
                grid=grid, pv=pv, soc=soc,
                discharge_setting=cur_discharge, charge_setting=cur_charge,
                config={"discharge_step_value": step}
            )

            cur_discharge = result["ramped_discharge"]
            cur_charge = result["charge_target"]

        # After enough cycles, discharge should match house load
        assert abs(cur_discharge - house_load) < step
        # Grid should be near zero
        final_grid = house_load - cur_discharge + cur_charge - pv
        assert abs(final_grid) <= 10  # within dead band

    def test_ramp_up_exact_convergence(self):
        """Verify exact ramp steps: 0 → 200 → 400 → 500."""
        house_load = 500
        pv = 0
        soc = 80
        cur_discharge = 0.0
        cur_charge = 0.0

        # Cycle 1: grid=500, new_net=500, ramped=min(500, 0+200)=200
        grid = house_load - cur_discharge
        r = run_zero_feed_in(grid=grid, pv=pv, soc=soc,
                             discharge_setting=cur_discharge, charge_setting=cur_charge)
        assert r["ramped_discharge"] == 200.0
        cur_discharge = r["ramped_discharge"]

        # Cycle 2: grid=300, new_net=200+300=500, ramped=min(500, 200+200)=400
        grid = house_load - cur_discharge
        r = run_zero_feed_in(grid=grid, pv=pv, soc=soc,
                             discharge_setting=cur_discharge, charge_setting=cur_charge)
        assert r["ramped_discharge"] == 400.0
        cur_discharge = r["ramped_discharge"]

        # Cycle 3: grid=100, new_net=400+100=500, ramped=min(500, 400+200)=500
        grid = house_load - cur_discharge
        r = run_zero_feed_in(grid=grid, pv=pv, soc=soc,
                             discharge_setting=cur_discharge, charge_setting=cur_charge)
        assert r["ramped_discharge"] == 500.0
        cur_discharge = r["ramped_discharge"]

        # Cycle 4: grid=0, within dead band → maintain
        grid = house_load - cur_discharge
        r = run_zero_feed_in(grid=grid, pv=pv, soc=soc,
                             discharge_setting=cur_discharge, charge_setting=cur_charge)
        assert r["ramped_discharge"] == 500.0
        assert r["force_mode"] == "discharge"

    def test_pv_surplus_charging_scenario(self):
        """Simulate PV surplus scenario: house=200W, PV=500W → MPPT charges 300W.

        force_mode stays 'stop' because PV charging is handled automatically by MPPT.
        The controller computes charge_target=300W but does not activate AC-side charging.
        """
        house_load = 200
        pv = 500
        soc = 50
        cur_discharge = 0.0
        cur_charge = 0.0

        # Grid = house - pv + charge - discharge = 200 - 500 = -300
        grid = house_load - pv + cur_charge - cur_discharge
        r = run_zero_feed_in(grid=grid, pv=pv, soc=soc,
                             discharge_setting=cur_discharge, charge_setting=cur_charge)
        assert r["charge_target"] == 300.0
        assert r["force_mode"] == "stop"  # MPPT handles PV, no AC-side charging needed
        cur_charge = r["written_charge"]  # 0 (not written when force_mode=stop)

        # Next cycle: charge entity stays 0 (written_charge=0 with stop), grid still -300
        grid = house_load - pv + cur_charge
        r = run_zero_feed_in(grid=grid, pv=pv, soc=soc,
                             discharge_setting=0, charge_setting=cur_charge)
        assert r["charge_target"] == 300.0
        assert r["force_mode"] == "stop"


class TestDelayAndReRead:
    """Test that post-delay re-read uses fresh sensor values.

    The blueprint reads sensors before and after the discharge delay.
    If the grid situation normalises during the delay, the final targets
    should reflect the new state, not the pre-delay state.
    """

    def run_with_different_post_delay_values(
        self,
        pre_grid: float,
        post_grid: float,
        soc: float,
        config: dict | None = None,
        pre_pv: float = 0,
        post_pv: float | None = None,
    ) -> dict:
        """
        Simulate a cycle where pre-delay and post-delay readings differ.
        The pre-delay values trigger starting_discharge; post-delay values
        may cause the controller to re-evaluate.

        pre_pv / post_pv: PV power before and after the delay. post_pv defaults
        to pre_pv (no change) when not specified.
        """
        entities_pre = {
            "sensor.grid_power": pre_grid,
            "sensor.pv_power": pre_pv,
            "sensor.soc": soc,
            "number.discharge": 0,
            "number.charge": 0,
        }
        ctx = build_context(entities_pre, config)

        # Pre-delay: read current state (same as run_zero_feed_in step 1)
        grid_val = float(render_template("{{ states(grid_power_entity) | float(0) }}", ctx))
        pv_val = float(render_template(TPL_PV_POWER, ctx))
        soc_val = float(render_template("{{ states(soc_entity) | float(0) }}", ctx))
        cur_d = float(render_template(TPL_CURRENT_DISCHARGE, ctx))
        cur_c = float(render_template(TPL_CURRENT_CHARGE, ctx))

        ctx["grid"] = grid_val
        ctx["pv_power"] = pv_val
        ctx["soc"] = soc_val
        ctx["current_discharge"] = cur_d
        ctx["current_charge"] = cur_c

        current_net = float(render_template(TPL_CURRENT_NET, ctx))
        ctx["current_net"] = current_net

        target_import = float(ctx["min_grid_import_value"])
        ctx["target_import"] = target_import
        tolerance = float(ctx["dead_band_value"])
        ctx["tolerance"] = tolerance
        lower_bound = target_import - tolerance
        upper_bound = target_import + tolerance
        ctx["lower_bound"] = lower_bound
        ctx["upper_bound"] = upper_bound

        new_net = float(render_template(TPL_NEW_NET, ctx))
        ctx["new_net"] = new_net

        discharge_target = float(render_template(TPL_DISCHARGE_TARGET, ctx))
        ctx["discharge_target"] = discharge_target
        charge_target = float(render_template(TPL_CHARGE_TARGET, ctx))
        ctx["charge_target"] = charge_target

        starting_discharge_str = render_template(TPL_STARTING_DISCHARGE, ctx)
        starting_discharge = starting_discharge_str.strip().lower() == "true"

        # Simulate post-delay re-read with DIFFERENT grid / PV values
        effective_post_pv = post_pv if post_pv is not None else pv_val
        ctx["grid_current"] = post_grid
        ctx["soc_current"] = soc_val
        ctx["pv_current"] = effective_post_pv
        ctx["current_discharge_actual"] = cur_d
        ctx["current_charge_actual"] = cur_c

        current_net_actual = float(render_template(TPL_CURRENT_NET, ctx))
        ctx["current_net_actual"] = current_net_actual

        # Recalculate new_net with post-delay grid
        ctx["grid"] = post_grid
        new_net_final = float(render_template(TPL_NEW_NET, ctx))
        ctx["new_net_final"] = new_net_final

        # Recalculate targets with fresh values (post-delay versions use soc_current/pv_current)
        discharge_target_final = float(render_template(TPL_DISCHARGE_TARGET_FINAL, ctx))
        ctx["discharge_target_final"] = discharge_target_final
        # TPL_CHARGE_TARGET uses ctx["new_net"]; point it at the post-delay value
        ctx["new_net"] = new_net_final
        charge_target_final = float(render_template(TPL_CHARGE_TARGET, ctx))
        ctx["charge_target_final"] = charge_target_final

        ramped_discharge = float(render_template(TPL_RAMPED_DISCHARGE, ctx))
        ctx["ramped_discharge"] = ramped_discharge

        force_mode = render_template(TPL_FORCE_MODE, ctx)

        return {
            "starting_discharge": starting_discharge,
            "pre_discharge_target": discharge_target,
            "new_net_final": new_net_final,
            "discharge_target_final": discharge_target_final,
            "charge_target_final": charge_target_final,
            "ramped_discharge": ramped_discharge,
            "force_mode": force_mode,
        }

    def test_grid_normalises_during_delay(self):
        """
        Pre-delay: grid=400W → starting_discharge=True, delay triggered.
        Post-delay: grid=5W (within dead band) → no discharge needed.
        Final output should be 0W / stop, not the pre-delay target.
        """
        result = self.run_with_different_post_delay_values(
            pre_grid=400, post_grid=5, soc=80
        )
        assert result["starting_discharge"] is True  # delay was triggered
        # After re-read: grid=5 is within [-10, 10] → new_net_final stays at 0
        assert result["new_net_final"] == 0.0
        assert result["discharge_target_final"] == 0.0
        assert result["ramped_discharge"] == 0.0
        assert result["force_mode"] == "stop"

    def test_grid_still_high_after_delay(self):
        """
        Pre-delay and post-delay grid both high → discharge starts normally.
        """
        result = self.run_with_different_post_delay_values(
            pre_grid=400, post_grid=350, soc=80
        )
        assert result["starting_discharge"] is True
        assert result["discharge_target_final"] == 350.0
        assert result["ramped_discharge"] == 200.0  # ramped from 0
        assert result["force_mode"] == "discharge"

    def test_grid_partially_resolved_during_delay(self):
        """
        Pre-delay: grid=400W.
        Post-delay: grid=50W (above dead band but reduced).
        Final discharge should be based on the post-delay value.
        """
        result = self.run_with_different_post_delay_values(
            pre_grid=400, post_grid=50, soc=80
        )
        assert result["starting_discharge"] is True
        # post_delay grid=50 > upper_bound=10 → adjust: new_net = 0 + 50 - 0 = 50
        assert result["new_net_final"] == 50.0
        assert result["discharge_target_final"] == 50.0
        assert result["ramped_discharge"] == 50.0  # min(50, 0+200)=50
        assert result["force_mode"] == "discharge"

    def test_pv_appears_during_discharge_delay(self):
        """
        Pre-delay: grid importing 400W, no PV → discharge delay triggered.
        During delay, PV comes online, reversing grid to export (-200W).
        Post-delay: controller sees PV surplus; MPPT will handle charging.
        force_mode=stop (no recovery active; AC-side charging not needed).
        """
        result = self.run_with_different_post_delay_values(
            pre_grid=400, post_grid=-200, soc=80,
            pre_pv=0, post_pv=500,
        )
        assert result["starting_discharge"] is True  # delay was triggered
        # Post-delay: PV=500W, grid=-200W → new_net = 0 + (-200) - 0 = -200
        assert result["new_net_final"] == -200.0
        assert result["discharge_target_final"] == 0.0
        assert result["charge_target_final"] == 200.0
        assert result["ramped_discharge"] == 0.0
        assert result["force_mode"] == "stop"  # MPPT handles PV, no AC charging


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_all_zero_idle(self):
        """Everything at zero → idle/stop."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["ramped_discharge"] == 0.0
        assert result["charge_target"] == 0.0
        assert result["force_mode"] == "stop"

    def test_very_large_grid_import(self):
        """Very large grid import → capped at max discharge."""
        result = run_zero_feed_in(
            grid=5000, pv=0, soc=80,
            discharge_setting=0, charge_setting=0
        )
        assert result["discharge_target"] == 800.0
        assert result["ramped_discharge"] == 200.0  # still ramped from 0

    def test_very_large_grid_export(self):
        """Very large grid export → capped at max charge."""
        result = run_zero_feed_in(
            grid=-5000, pv=6000, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["charge_target"] == 800.0

    def test_transition_discharge_to_charge(self):
        """Transition from discharge to charge (cloud comes, PV surplus)."""
        # Was discharging 300W, now PV kicks in and grid exports
        result = run_zero_feed_in(
            grid=-200, pv=500, soc=50,
            discharge_setting=300, charge_setting=0
        )
        # new_net = 300 + (-200) - 0 = 100 → still discharge
        assert result["discharge_target"] == 100.0
        assert result["force_mode"] == "discharge"

    def test_transition_charge_to_discharge(self):
        """Transition from charge to discharge (cloud covers PV)."""
        result = run_zero_feed_in(
            grid=200, pv=0, soc=80,
            discharge_setting=0, charge_setting=300
        )
        # current_net = 0-300 = -300
        # new_net = -300 + 200 - 0 = -100 → still charge
        assert result["charge_target"] == 100.0
        assert result["discharge_target"] == 0.0

    def test_negative_pv_treated_as_zero(self):
        """Negative PV value (sensor error) → treated as is by float()."""
        result = run_zero_feed_in(
            grid=300, pv=-10, soc=80,
            discharge_setting=0, charge_setting=0
        )
        # PV=-10 is a valid float, so pv_power=-10. Force mode check: pv > 0 → false
        assert result["pv_power"] == -10.0
        assert result["force_mode"] == "discharge"  # discharge, not charge

    def test_large_grid_export_does_not_prevent_discharge_reduction(self):
        """Regression: large grid export should not prevent discharge reduction.

        Previously, max_feed_in_value was used for the dead band lower bound,
        causing the dead band to become [-800, 10] when max_feed_in=800. This
        meant the controller never reduced discharge when exporting to grid.
        The dead band is now always ±10 W (symmetric).
        """
        # Scenario from issue: discharging 570W, grid at -204W (exporting)
        result = run_zero_feed_in(
            grid=-204.3, pv=0, soc=41,
            discharge_setting=570, charge_setting=0,
        )
        # Grid at -204.3 is outside dead band [-10, 10] → must adjust
        # new_net = 570 + (-204.3) - 0 = 365.7
        assert abs(result["new_net"] - 365.7) < 0.1
        assert abs(result["discharge_target"] - 365.7) < 0.1
        # Ramp-down is immediate: target < current
        assert abs(result["ramped_discharge"] - 365.7) < 0.1
        assert result["force_mode"] == "discharge"

    def test_discharge_stuck_at_max_after_load_drop(self):
        """Regression: discharge at max should reduce when load drops.

        Simulates: load was 800W → discharge=800, then load drops to 200W.
        Grid becomes -600W (exporting). Controller must reduce discharge.
        """
        result = run_zero_feed_in(
            grid=-600, pv=0, soc=80,
            discharge_setting=800, charge_setting=0,
        )
        # Grid at -600 is outside [-10, 10] → adjust
        # new_net = 800 + (-600) - 0 = 200
        assert result["new_net"] == 200.0
        assert result["discharge_target"] == 200.0
        # Ramp-down is immediate
        assert result["ramped_discharge"] == 200.0
        assert result["force_mode"] == "discharge"


class TestSOCRecovery:
    """Test SOC recovery charging feature.

    When SOC <= recovery_soc (and recovery_soc > 0), the controller forces
    charging at max_charge_power — even from the grid — until SOC reaches
    min_soc. This prevents the battery from staying critically depleted.
    """

    # Common config: min_soc=20%, recovery_soc=10%
    RECOVERY_CFG = {"min_soc_value": 20, "recovery_soc_value": 10}

    def test_recovery_triggers_below_threshold(self):
        """SOC=9% <= recovery_soc=10% → forced charge at max_charge_power."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=9,
            discharge_setting=0, charge_setting=0,
            config=self.RECOVERY_CFG,
        )
        assert result["charge_target"] == 800.0  # max_charge_value default
        assert result["force_mode"] == "charge"

    def test_recovery_allows_grid_charging(self):
        """Recovery active with pv=0 → force_mode=charge (no PV required)."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=9,
            discharge_setting=0, charge_setting=0,
            config=self.RECOVERY_CFG,
        )
        # PV is 0, but recovery bypasses PV requirement
        assert result["pv_power"] == 0.0
        assert result["force_mode"] == "charge"

    def test_recovery_charges_at_max_power(self):
        """Recovery charge power equals max_charge_power (800 W default)."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=5,
            discharge_setting=0, charge_setting=0,
            config=self.RECOVERY_CFG,
        )
        assert result["charge_target"] == 800.0

    def test_recovery_ends_at_min_soc(self):
        """SOC=20% == min_soc=20%, recovery_soc=10% → recovery NOT active."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=20,
            discharge_setting=0, charge_setting=0,
            config=self.RECOVERY_CFG,
        )
        # soc=20 > recovery_soc=10 → recovery inactive, normal logic
        # grid=0 within dead band → no charge or discharge
        assert result["charge_target"] == 0.0
        assert result["force_mode"] == "stop"

    def test_recovery_inactive_above_threshold(self):
        """SOC=11% > recovery_soc=10% → normal zero-feed-in logic, no forced charge."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=11,
            discharge_setting=0, charge_setting=0,
            config=self.RECOVERY_CFG,
        )
        # Recovery not active; grid=0 within dead band → no adjustment
        assert result["charge_target"] == 0.0
        assert result["force_mode"] == "stop"

    def test_recovery_disabled_at_zero(self):
        """recovery_soc=0 → feature disabled, even at critically low SOC."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=5,
            discharge_setting=0, charge_setting=0,
            config={"min_soc_value": 20, "recovery_soc_value": 0},
        )
        # recovery_soc=0 → rec > 0 is False → no forced charge
        assert result["charge_target"] == 0.0
        assert result["force_mode"] == "stop"

    def test_recovery_no_discharge_during_recovery(self):
        """Recovery active: grid importing 500W, but SOC too low → no discharge."""
        result = run_zero_feed_in(
            grid=500, pv=0, soc=9,
            discharge_setting=0, charge_setting=0,
            config=self.RECOVERY_CFG,
        )
        # soc=9 <= min_soc=20 → discharge blocked
        assert result["discharge_target"] == 0.0
        assert result["ramped_discharge"] == 0.0
        # Recovery forces charge instead
        assert result["charge_target"] == 800.0
        assert result["force_mode"] == "charge"

    def test_recovery_triggers_at_exact_threshold(self):
        """SOC exactly equals recovery_soc → recovery triggers (condition is soc <= rec)."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=10,
            discharge_setting=0, charge_setting=0,
            config=self.RECOVERY_CFG,  # min_soc=20, recovery_soc=10
        )
        # soc=10 <= recovery_soc=10 → True → forced charge at max power
        assert result["charge_target"] == 800.0
        assert result["force_mode"] == "charge"

    def test_recovery_soc_above_min_soc_takes_priority_over_discharge(self):
        """recovery_soc > min_soc: recovery wins even when grid is importing.

        Config: recovery_soc=30, min_soc=10.
        At SOC=20 (below recovery_soc but above min_soc):
        - Recovery activates → charge_target = max_charge_value
        - Discharge is NOT blocked by min_soc (soc=20 > min_soc=10)
        - Grid importing 300W → discharge_target=300W
        - But force_mode gives recovery priority → 'charge' wins
        """
        result = run_zero_feed_in(
            grid=300, pv=0, soc=20,
            discharge_setting=0, charge_setting=0,
            config={"min_soc_value": 10, "recovery_soc_value": 30},
        )
        assert result["charge_target"] == 800.0     # recovery wants to charge
        assert result["discharge_target"] == 300.0  # discharge target still computed
        assert result["force_mode"] == "charge"     # recovery takes priority

    def test_recovery_soc_above_min_soc_works_without_grid_import(self):
        """recovery_soc > min_soc: recovery succeeds when grid is balanced (no import)."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=20,
            discharge_setting=0, charge_setting=0,
            config={"min_soc_value": 10, "recovery_soc_value": 30},
        )
        # Grid=0 is within dead band → new_net=0 → discharge_target=0 (no conflict)
        assert result["discharge_target"] == 0.0
        assert result["charge_target"] == 800.0
        assert result["force_mode"] == "charge"


class TestModbusWriteConsistency:
    """Tests that expose the charge entity ratchet bug.

    When force_mode == 'stop' (grid exporting, PV=0, no recovery), the blueprint
    computes a non-zero charge_target_final but the Modbus write sequence (else
    branch) still writes that value to the charge entity:

        else:
          - number.set_value(discharge_power_number, ramped_discharge)   # 0, correct
          - number.set_value(charge_power_number, charge_target_final)   # > 0, BUG

    On the next cycle, states(charge_entity) returns this stale non-zero value,
    which the controller reads as current_charge. This makes current_net negative,
    so new_net grows even more negative, and charge_target_final grows further —
    a runaway feedback loop that pins the charge entity at max_charge_value.

    When PV eventually arrives, force_mode becomes 'charge' at max power regardless
    of actual surplus, causing a large grid import spike and subsequent oscillation.
    """

    def test_charge_target_nonzero_when_force_mode_stop_no_pv(self):
        """Grid exporting, PV=0, no recovery → force_mode=stop, written_charge must be 0.

        charge_target (the raw template value) may still be non-zero — that is
        what the controller *would* charge if allowed. But the Modbus entity must
        receive 0 so that the next cycle's current_charge stays clean.
        """
        result = run_zero_feed_in(
            grid=-300, pv=0, soc=50,
            discharge_setting=0, charge_setting=0,
        )
        assert result["force_mode"] == "stop"
        assert result["written_charge"] == 0.0

    def test_charge_entity_ratchets_up_without_pv(self):
        """Multi-cycle: with the fix, charge entity stays at 0 when grid exports and PV=0.

        Uses written_charge (what the blueprint actually sends to Modbus) as the
        charge_setting for the next cycle. Pre-fix this ratcheted to 800W; post-fix
        it must stay at 0 every cycle.
        """
        cur_discharge = 0.0
        cur_charge = 0.0

        for cycle in range(5):
            result = run_zero_feed_in(
                grid=-300, pv=0, soc=50,
                discharge_setting=cur_discharge, charge_setting=cur_charge,
            )
            assert result["force_mode"] == "stop", \
                f"force_mode should be stop on cycle {cycle + 1}"
            # Use written_charge — what the blueprint actually writes to Modbus.
            cur_charge = result["written_charge"]
            cur_discharge = result["ramped_discharge"]

        assert cur_charge == 0.0

    def test_charge_no_overcorrect_when_pv_arrives(self):
        """With the fix, PV arrival computes correct surplus; force_mode stays stop.

        Pre-fix: 3 stop-mode cycles ratcheted charge entity to 800W, causing the
        controller to read 800W current_charge when PV arrived, distorting new_net.
        Post-fix: charge entity stays at 0 during stop cycles, so when PV arrives
        current_net=0 and charge_target is computed correctly ≈ 300W (the surplus).
        force_mode='stop' because PV surplus is handled by MPPT, not AC charging.
        """
        cur_discharge = 0.0
        cur_charge = 0.0

        # Phase 1: 3 cycles without PV — charge entity must stay at 0.
        for _ in range(3):
            result = run_zero_feed_in(
                grid=-300, pv=0, soc=50,
                discharge_setting=cur_discharge, charge_setting=cur_charge,
            )
            cur_charge = result["written_charge"]   # 0 after the fix
            cur_discharge = result["ramped_discharge"]

        # Phase 2: PV arrives, creating a 300W surplus (grid=-300W).
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=50,
            discharge_setting=cur_discharge, charge_setting=cur_charge,
        )
        assert result["force_mode"] == "stop"  # MPPT handles PV surplus
        # current_net = 0 (cur_charge stayed 0), new_net = -300, charge_target = 300.
        assert result["charge_target"] == 300.0


class TestFullBatteryPVPassthrough:
    """Test PV pass-through when battery is full (SOC >= max_soc).

    When the battery is at max SOC and PV is producing, the MPPT cannot absorb
    more energy. The failsafe sets discharge_target = pv_power so the AC inverter
    routes PV through to the house/grid, preventing curtailment.
    """

    def test_full_battery_pv_discharges_to_grid(self):
        """SOC=100%, PV=500W → discharge_target_final=500, force_mode=discharge."""
        result = run_zero_feed_in(
            grid=-500, pv=500, soc=100,
            discharge_setting=0, charge_setting=0,
        )
        assert result["discharge_target_final"] == 500.0
        assert result["ramped_discharge"] == 200.0  # ramped from 0: min(500, 200)
        assert result["force_mode"] == "discharge"

    def test_full_battery_pv_capped_at_max_discharge(self):
        """SOC=100%, PV=1000W → discharge_target_final capped at max_discharge (800W)."""
        result = run_zero_feed_in(
            grid=-800, pv=1000, soc=100,
            discharge_setting=0, charge_setting=0,
        )
        assert result["discharge_target_final"] == 800.0  # capped at max

    def test_full_battery_no_pv_stays_stopped(self):
        """SOC=100%, no PV → no pass-through, force_mode=stop."""
        result = run_zero_feed_in(
            grid=0, pv=0, soc=100,
            discharge_setting=0, charge_setting=0,
        )
        assert result["discharge_target_final"] == 0.0
        assert result["force_mode"] == "stop"

    def test_passthrough_only_at_max_soc(self):
        """SOC=99% (below max_soc=100%), PV active → normal control, no passthrough."""
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=99,
            discharge_setting=0, charge_setting=0,
        )
        # soc=99 < max_soc=100 → no passthrough; normal logic: charge_target=300
        assert result["charge_target"] == 300.0
        assert result["discharge_target_final"] == 0.0
        assert result["force_mode"] == "stop"

    def test_passthrough_custom_max_soc(self):
        """Custom max_soc=90%: SOC=90% with PV → pass-through activates."""
        result = run_zero_feed_in(
            grid=-200, pv=400, soc=90,
            discharge_setting=0, charge_setting=0,
            config={"max_soc_value": 90},
        )
        # soc=90 >= max_soc=90 and pv=400 > 0 → passthrough
        assert result["discharge_target_final"] == 400.0
        assert result["force_mode"] == "discharge"

    def test_passthrough_continues_while_already_discharging(self):
        """Battery full with PV: ramped discharge from existing discharge setting."""
        result = run_zero_feed_in(
            grid=-500, pv=600, soc=100,
            discharge_setting=400, charge_setting=0,  # already discharging 400W
        )
        # PV=600, max_discharge=800 → discharge_target_final=600
        assert result["discharge_target_final"] == 600.0
        # Ramped: target=600 > current=400 → min(600, 400+200) = 600
        assert result["ramped_discharge"] == 600.0
        assert result["force_mode"] == "discharge"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
