"""Set a wavelength and power, then read the built-in monitor back.

Usage:
    python examples/basic_control.py TCPIP0::192.168.1.161::5000::SOCKET
"""

import sys
import time

from santec_tsl import TSL570, nm_to_thz

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "TCPIP0::192.168.1.161::5000::SOCKET"

with TSL570(RESOURCE) as tsl:
    print(f"Identity : {tsl.identify()}")
    print(f"Firmware : {tsl.firmware_version()}")
    print(f"Product  : {tsl.product_code()}")

    limits = tsl.wavelength_limits_nm()
    print(f"Range    : {limits.min_nm:.2f} - {limits.max_nm:.2f} nm")
    if tsl.interlock_tripped:
        raise SystemExit("External interlock is engaged - output is blocked.")

    tsl.wavelength_nm = 1550.0
    tsl.power_dbm = 6.0
    tsl.power_control_auto = True
    tsl.output = True
    tsl.raise_on_error()

    time.sleep(1.0)  # let the wavelength settle before reading the monitor
    print(f"Set at   : {tsl.wavelength_nm:.4f} nm ({tsl.frequency_thz:.5f} THz)")
    print(f"Expected : {nm_to_thz(1550.0):.5f} THz")
    print(f"Monitor  : {tsl.actual_power_dbm:.2f} dBm")

    tsl.output = False
