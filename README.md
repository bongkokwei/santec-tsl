# santec-tsl

Python driver and PyQt6 control panel for the **santec TSL-570** tunable
semiconductor laser, built from the TSL-570 operation manual (a copy lives in
[`manual/`](manual/)).

The driver talks to the instrument over PyVISA — LAN, GPIB or USB — and forces
the instrument's **SCPI** command set on connect, so responses come back in SI
units regardless of what the front panel was left on. The Python API works in
lab units: nm, THz, dBm, seconds.

## Install

```bash
pip install -e ".[dev,gui]"
```

## Driver

```python
from santec_tsl import TSL570, SweepMode, SweepState

with TSL570("TCPIP0::192.168.1.161::5000::SOCKET") as tsl:
    tsl.wavelength_nm = 1550.0
    tsl.power_dbm = 6.0
    tsl.output = True
    print(tsl.actual_power_dbm)
```

Resource strings: `TCPIP0::<ip>::<port>::SOCKET` for LAN (the port is set on the
instrument's Communication screen), `GPIB0::<address>::INSTR` for GPIB, or
`ASRL<n>::INSTR` for USB once santec's FTDI driver is installed.

### What's covered

| Area | API |
|---|---|
| Wavelength / frequency | `wavelength_nm`, `frequency_thz`, `wavelength_limits_nm()`, `fine_tuning`, `wavelength_offset_nm` |
| Output | `output`, `shutter_closed`, `power_dbm`, `actual_power_dbm`, `power_limits_dbm()`, `attenuation_db`, `power_control_auto`, `coherence_control` |
| Modulation | `modulation`, `modulation_source` |
| Sweep setup | `sweep_start_nm`, `sweep_stop_nm`, `sweep_speed_nm_per_s`, `sweep_step_nm`, `sweep_dwell_s`, `sweep_delay_s`, `sweep_cycles`, `sweep_mode`, `sweep_range_limits_nm()` |
| Sweep control | `start_sweep()`, `start_repeat_sweep()`, `stop_sweep()`, `sweep_state`, `sweep_count` |
| Logged data | `logged_points`, `read_log()` → numpy arrays in nm and dBm |
| Triggers | `trigger_input_external`, `trigger_input_standby`, `soft_trigger()`, `trigger_output`, `trigger_output_step_nm`, `trigger_through` |
| System | `interlock_tripped`, `error()`, `raise_on_error()`, `alert()`, `firmware_version()`, `product_code()`, `display_brightness` |

Two gotchas worth knowing, both from the manual:

- **Set the sweep speed before the sweep span.** The configurable span shrinks
  as the speed rises, so `sweep_range_limits_nm()` is only valid for the speed
  currently loaded.
- **Only eight sweep speeds exist** (1, 2, 5, 10, 20, 50, 100, 200 nm/s).
  Anything else raises `TSLValueError` rather than being silently rounded.

## GUI

```bash
santec-tsl-gui
```

Or `python -m santec_tsl.gui`. Set `TSL_LOG_LEVEL=DEBUG` to see every command on
the wire.

The panel keeps all VISA I/O on a background `QThread`, so it never freezes on a
slow reply. It shows live wavelength, frequency, power setpoint and monitored
power; badges for output, interlock and sweep state; and a sweep section that
mirrors whatever the instrument currently holds. Enabling the laser output asks
for confirmation first.

## Tests

```bash
pytest
```

Everything is mocked at the PyVISA boundary, so the suite runs with no hardware.
The bench smoke test is tagged and skipped by default:

```bash
pytest -m hw
```

Edit the resource string in `tests/test_driver.py::test_real_connection` to
match your instrument before running it.
