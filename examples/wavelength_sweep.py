"""Run a continuous sweep on the TSL-570 and plot the logged trace.

Usage:
    python examples/wavelength_sweep.py TCPIP0::192.168.1.161::5000::SOCKET
"""

import logging
import sys
import time

import matplotlib.pyplot as plt

from santec_tsl import TSL570, SweepMode, SweepState, TriggerOutput

logging.basicConfig(level=logging.INFO, format="%(levelname)-7s %(name)s: %(message)s")

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "TCPIP0::192.168.1.161::5000::SOCKET"

with TSL570(RESOURCE) as tsl:
    print(tsl.identify())
    if tsl.interlock_tripped:
        raise SystemExit("External interlock is engaged - output is blocked.")

    # Speed first: it constrains the configurable sweep span.
    tsl.sweep_speed_nm_per_s = 20.0
    tsl.sweep_mode = SweepMode.CONTINUOUS_ONE_WAY
    span = tsl.sweep_range_limits_nm()
    print(f"Configurable span at 20 nm/s: {span.min_nm:.3f} - {span.max_nm:.3f} nm")

    tsl.sweep_start_nm = max(1520.0, span.min_nm)
    tsl.sweep_stop_nm = min(1580.0, span.max_nm)
    tsl.sweep_cycles = 1

    # Log one point every 10 pm so the trace lines up with an external detector.
    tsl.trigger_output = TriggerOutput.STEP
    tsl.trigger_output_step_nm = 0.01

    tsl.power_dbm = 6.0
    tsl.output = True
    tsl.raise_on_error()

    tsl.start_sweep()
    while tsl.sweep_state is not SweepState.STOPPED:
        time.sleep(0.2)

    logged = tsl.read_log()
    tsl.output = False

print(f"Logged {logged.wavelength_nm.size} points")

fig, ax = plt.subplots(figsize=(10, 5))
ax.plot(logged.wavelength_nm, logged.power_dbm, lw=1.0)
ax.set_xlabel("Wavelength (nm)")
ax.set_ylabel("Power (dBm)")
fig.savefig("sweep.pdf", bbox_inches="tight", dpi=300)
plt.show()
