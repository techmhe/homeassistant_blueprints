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
    max_discharge_value=800,
    max_charge_value=800,
    min_soc_value=10,
    max_soc_value=100,
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
"""

TPL_CHARGE_TARGET = """
{%- set net = new_net | float(0) -%}
{%- if net < 0 -%}
  {%- if soc | float(0) >= max_soc_value | float(0) -%}
    {{ 0 }}
  {%- else -%}
    {{ [net | abs, max_charge_value | float(0)] | min }}
  {%- endif -%}
{%- else -%}
  {{ 0 }}
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
{%- if ramped_discharge | float(0) > 0 -%}
  discharge
{%- elif charge_target_final | float(0) > 0 and pv_current | float(0) > 0 -%}
  charge
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
    tolerance = 10
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
    ctx["discharge_target_final"] = discharge_target
    ctx["charge_target_final"] = charge_target

    ramped_discharge = float(render_template(TPL_RAMPED_DISCHARGE, ctx))
    ctx["ramped_discharge"] = ramped_discharge

    force_mode = render_template(TPL_FORCE_MODE, ctx)
    ctx["force_mode_option"] = force_mode

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
        "charge_target": charge_target,
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

    def test_idle_grid_exporting_with_pv_should_charge(self):
        """Battery idle, grid exporting 300W, PV active → should charge."""
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["new_net"] == -300.0
        assert result["charge_target"] == 300.0
        assert result["ramped_discharge"] == 0.0
        assert result["force_mode"] == "charge"

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

    def test_high_soc_prevents_charging(self):
        """SOC at max (100%) → should NOT charge even if PV is exporting."""
        result = run_zero_feed_in(
            grid=-500, pv=600, soc=100,
            discharge_setting=0, charge_setting=0
        )
        assert result["charge_target"] == 0.0
        assert result["force_mode"] == "stop"

    def test_soc_just_below_max_allows_charging(self):
        """SOC just below max (99%) → should charge normally."""
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=99,
            discharge_setting=0, charge_setting=0
        )
        assert result["charge_target"] == 300.0
        assert result["force_mode"] == "charge"

    def test_full_soc_no_load_pv_producing_does_not_discharge(self):
        """SOC=100%, no load, PV producing → stop, no discharge into grid.

        With PV=500W and no load, grid reads -500W (exporting).
        The controller wants to charge (new_net=-500) but SOC is full → blocked.
        Discharge target must stay 0; battery must not export solar to grid.
        """
        result = run_zero_feed_in(
            grid=-500, pv=500, soc=100,
            discharge_setting=0, charge_setting=0
        )
        assert result["new_net"] == -500.0      # controller wants to charge
        assert result["charge_target"] == 0.0   # blocked by max SOC
        assert result["discharge_target"] == 0.0  # no discharge triggered
        assert result["ramped_discharge"] == 0.0
        assert result["force_mode"] == "stop"


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

    def test_force_mode_charge_with_pv(self):
        """Charge target > 0 and PV > 0 → charge mode."""
        result = run_zero_feed_in(
            grid=-300, pv=500, soc=50,
            discharge_setting=0, charge_setting=0
        )
        assert result["force_mode"] == "charge"

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
            (-300, 500, "charge"),
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
        """Simulate PV surplus scenario: house=200W, PV=500W → charge 300W."""
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
        assert r["force_mode"] == "charge"
        cur_charge = r["charge_target"]

        # Next cycle: grid = 200 - 500 + 300 = 0 → within dead band
        grid = house_load - pv + cur_charge
        r = run_zero_feed_in(grid=grid, pv=pv, soc=soc,
                             discharge_setting=0, charge_setting=cur_charge)
        assert r["new_net"] == -300.0  # maintain current
        assert r["charge_target"] == 300.0
        assert r["force_mode"] == "charge"


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
    ) -> dict:
        """
        Simulate a cycle where pre-delay and post-delay readings differ.
        The pre-delay values trigger starting_discharge; post-delay values
        may cause the controller to re-evaluate.
        """
        entities_pre = {
            "sensor.grid_power": pre_grid,
            "sensor.pv_power": 0,
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
        ctx["tolerance"] = 10
        lower_bound = target_import - 10
        upper_bound = target_import + 10
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

        # Simulate post-delay re-read with DIFFERENT grid value
        ctx["grid_current"] = post_grid
        ctx["soc_current"] = soc_val
        ctx["pv_current"] = pv_val
        ctx["current_discharge_actual"] = cur_d
        ctx["current_charge_actual"] = cur_c

        current_net_actual = float(render_template(TPL_CURRENT_NET, ctx))
        ctx["current_net_actual"] = current_net_actual

        # Recalculate new_net with post-delay grid
        ctx["grid"] = post_grid
        new_net_final = float(render_template(TPL_NEW_NET, ctx))
        ctx["new_net_final"] = new_net_final

        # Recalculate targets with fresh values
        ctx["new_net"] = new_net_final
        discharge_target_final = float(render_template(TPL_DISCHARGE_TARGET, ctx))
        ctx["discharge_target_final"] = discharge_target_final
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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
