"""Check that replies from the TSL line up with the commands sent.

Run this when a connection fails with an empty or shifted reply. It tries each
read-termination setting in turn and reports which one keeps the stream in step.

Usage:
    python examples/probe_link.py GPIB2::3::INSTR
"""

import sys

import pyvisa

RESOURCE = sys.argv[1] if len(sys.argv) > 1 else "GPIB2::3::INSTR"

# Three queries with recognisable replies. If the stream is off by one, the
# wavelength value lands in the IDN slot and the last query comes back empty.
PROBES = [
    ("*IDN?", "should start with SANTEC"),
    (":WAVelength?", "should be a number in metres, e.g. +1.55000000E-006"),
    (":POWer:STATe?", "should be +0 or +1"),
]

rm = pyvisa.ResourceManager()
print(f"VISA implementation: {rm.visalib}")
print(f"Resources visible  : {rm.list_resources()}\n")

for termination in ("\r", "\r\n", "\n", None):
    label = repr(termination) if termination else "None (read to EOI)"
    print(f"--- read_termination = {label}")
    try:
        inst = rm.open_resource(RESOURCE)
    except (pyvisa.VisaIOError, OSError, ValueError) as exc:
        print(f"    could not open: {exc}\n")
        continue

    inst.timeout = 3000
    inst.write_termination = "\r"
    inst.read_termination = termination
    try:
        inst.clear()
    except (pyvisa.VisaIOError, NotImplementedError) as exc:
        print(f"    no device clear: {exc}")

    try:
        for command, expected in PROBES:
            reply = inst.query(command).strip()
            print(f"    {command:20s} -> {reply!r:45s} ({expected})")
    except pyvisa.VisaIOError as exc:
        print(f"    failed: {exc}")
    finally:
        inst.close()
    print()

print("The setting where all three replies match their description is the one to use.")
