"""Driver for the santec TSL-570 tunable semiconductor laser.

The TSL-570 speaks two command sets: santec's own "Legacy" set (bare numbers in
nm/THz/dBm) and a "SCPI" set following the SCPI consortium conventions (SI base
units, exponential responses). This driver forces the SCPI set in
:meth:`TSL570.open` and works in SI units on the wire, while exposing
lab-friendly units (nm, THz, dBm) at the Python API. See the operation manual,
section 7.4.

Transport is PyVISA. The instrument is reachable over LAN (raw TCP socket),
GPIB or USB; the delimiter is CR on every interface, so terminations are set
explicitly rather than left to the VISA defaults.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum

import numpy as np
import pyvisa

from .exceptions import (
    TSLCommandError,
    TSLConnectionError,
    TSLError,
    TSLProtocolError,
    TSLValueError,
)

log = logging.getLogger(__name__)

C_M_PER_S = 299_792_458.0

#: The only sweep speeds the TSL-570 accepts (manual p. 81).
SWEEP_SPEEDS_NM_PER_S = (1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0)


class SweepMode(IntEnum):
    """Sweep mode codes for ``:WAVelength:SWEep:MODe`` (manual p. 81)."""

    STEP_ONE_WAY = 0
    CONTINUOUS_ONE_WAY = 1
    STEP_TWO_WAY = 2
    CONTINUOUS_TWO_WAY = 3


class SweepState(IntEnum):
    """Sweep status codes returned by ``:WAVelength:SWEep?`` (manual p. 86)."""

    STOPPED = 0
    RUNNING = 1
    STANDING_BY_TRIGGER = 3
    PREPARING = 4


class TriggerOutput(IntEnum):
    """Output trigger timing for ``:TRIGger:OUTPut`` (manual p. 91)."""

    NONE = 0
    STOP = 1
    START = 2
    STEP = 3


class ModulationSource(IntEnum):
    """Modulation source for ``:AM:SOURce`` (manual p. 88)."""

    COHERENCE_CONTROL = 0
    INTENSITY = 1
    FREQUENCY = 3


@dataclass(frozen=True)
class WavelengthRange:
    """A configurable wavelength window, in nm."""

    min_nm: float
    max_nm: float


@dataclass(frozen=True)
class SweepLog:
    """One sweep's logged data, read back from the instrument's memory."""

    wavelength_nm: np.ndarray
    power_dbm: np.ndarray


def nm_to_thz(wavelength_nm: float) -> float:
    """Convert a vacuum wavelength in nm to optical frequency in THz."""
    return C_M_PER_S / (wavelength_nm * 1e-9) / 1e12


def thz_to_nm(freq_thz: float) -> float:
    """Convert an optical frequency in THz to vacuum wavelength in nm."""
    return C_M_PER_S / (freq_thz * 1e12) * 1e9


class TSL570:
    """santec TSL-570 tunable laser over PyVISA.

    Args:
        resource: VISA resource string, e.g. ``TCPIP0::192.168.1.161::5000::SOCKET``
            for LAN, ``GPIB0::1::INSTR`` for GPIB, or ``ASRL8::INSTR`` for USB.
        timeout: I/O timeout in seconds. Sweeps run asynchronously, so this only
            has to cover the slowest single tuning move.

    Example:
        with TSL570("TCPIP0::192.168.1.161::5000::SOCKET") as tsl:
            tsl.wavelength_nm = 1550.0
            tsl.output = True
    """

    def __init__(self, resource: str, timeout: float = 5.0) -> None:
        self.resource = resource
        self.timeout = timeout
        self._rm: pyvisa.ResourceManager | None = None
        self._inst = None

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> None:
        """Open the VISA session and switch the instrument into SCPI mode."""
        if self._inst is not None:
            return
        try:
            self._rm = pyvisa.ResourceManager()
            self._inst = self._rm.open_resource(self.resource)
        except (pyvisa.VisaIOError, OSError, ValueError) as exc:
            self._rm = None
            raise TSLConnectionError(f"Could not open {self.resource!r}: {exc}") from exc

        self._inst.timeout = int(self.timeout * 1000)
        # Commands end with CR; the instrument accepts CR as a delimiter and VISA
        # asserts EOI on the last byte anyway (manual 7.1.3).
        self._inst.write_termination = "\r"
        # Replies are another matter. On GPIB/USB the instrument asserts EOI at
        # the end of a reply *and* appends its configured delimiter, which may be
        # CR+LF — stopping at the CR would leave the LF behind and shift every
        # later reply by one. Read to EOI instead and strip the delimiter here.
        # A raw TCP socket has no EOI, so there the CR is all we have.
        self._inst.read_termination = "\r" if self._is_socket else None

        # Drop anything a previous session left in the buffers (GPIB DCL).
        try:
            self._inst.clear()
        except (pyvisa.VisaIOError, NotImplementedError) as exc:
            log.debug("Device clear not available on %s: %s", self.resource, exc)

        try:
            self.write(":SYSTem:COMMunicate:CODe 1")  # 1 = SCPI command set
            log.info("Connected to %s: %s", self.resource, self.identify())
        except TSLError:
            self.close()
            raise

    def close(self) -> None:
        """Close the VISA session. Safe to call more than once."""
        if self._inst is not None:
            try:
                self._inst.close()
            except (pyvisa.VisaIOError, OSError) as exc:
                log.warning("Error while closing %s, ignoring: %s", self.resource, exc)
            self._inst = None
        if self._rm is not None:
            self._rm.close()
            self._rm = None

    def __enter__(self) -> "TSL570":
        self.open()
        return self

    def __exit__(self, *args) -> None:
        self.close()

    @property
    def is_open(self) -> bool:
        return self._inst is not None

    @property
    def _is_socket(self) -> bool:
        """True for raw TCP sockets, which carry no EOI to mark a reply's end."""
        return "SOCKET" in self.resource.upper()

    # -- raw I/O -----------------------------------------------------------

    def write(self, command: str) -> None:
        """Send a command that returns no response."""
        log.debug(">> %s", command)
        self._require_open().write(command)

    def query(self, command: str) -> str:
        """Send a query and return the stripped response string."""
        log.debug(">> %s", command)
        try:
            response = self._require_open().query(command).strip()
        except pyvisa.VisaIOError as exc:
            raise TSLConnectionError(f"No reply to {command!r}: {exc}") from exc
        log.debug("<< %s", response)
        return response

    def query_float(self, command: str) -> float:
        response = self.query(command)
        if not response:
            raise TSLProtocolError(
                f"{command!r} returned an empty reply. The reply stream is out of "
                "step with the commands — usually a delimiter mismatch, or a stale "
                "reply left in the buffer by an earlier session."
            )
        try:
            return float(response)
        except ValueError as exc:
            raise TSLProtocolError(
                f"{command!r} returned {response!r}, expected a number"
            ) from exc

    def query_int(self, command: str) -> int:
        return int(round(self.query_float(command)))

    def _require_open(self):
        if self._inst is None:
            raise TSLConnectionError("Not connected - call open() first.")
        return self._inst

    # -- IEEE-488.2 common commands ---------------------------------------

    def identify(self) -> str:
        """Return the ``*IDN?`` identification string."""
        return self.query("*IDN?")

    def reset(self) -> None:
        """Reset the instrument and abort any standby command."""
        self.write("*RST")

    def self_test(self) -> int:
        """Run the instrument self-test; 0 means it passed."""
        return self.query_int("*TST?")

    def clear_status(self) -> None:
        self.write("*CLS")

    def wait_for_operation(self) -> None:
        """Block until the pending operation completes (``*OPC?``)."""
        self.query("*OPC?")

    # -- wavelength / frequency -------------------------------------------

    @property
    def wavelength_nm(self) -> float:
        """Output wavelength in nm (0.1 pm resolution)."""
        return self.query_float(":WAVelength?") * 1e9

    @wavelength_nm.setter
    def wavelength_nm(self, value_nm: float) -> None:
        self.write(f":WAVelength {value_nm:.4f}nm")

    @property
    def frequency_thz(self) -> float:
        """Output optical frequency in THz (10 MHz resolution)."""
        return self.query_float(":FREQuency?") / 1e12

    @frequency_thz.setter
    def frequency_thz(self, value_thz: float) -> None:
        self.write(f":FREQuency {value_thz:.6f}THZ")

    def wavelength_limits_nm(self) -> WavelengthRange:
        """Minimum and maximum settable output wavelength, in nm."""
        return WavelengthRange(
            min_nm=self.query_float(":WAVelength? MINimum") * 1e9,
            max_nm=self.query_float(":WAVelength? MAXimum") * 1e9,
        )

    @property
    def fine_tuning(self) -> float:
        """Fine-tuning value, -100.00 to +100.00 (arbitrary units)."""
        return self.query_float(":WAVelength:FINe?")

    @fine_tuning.setter
    def fine_tuning(self, value: float) -> None:
        self._check_range("fine tuning", value, -100.0, 100.0)
        self.write(f":WAVelength:FINe {value:.2f}")

    def disable_fine_tuning(self) -> None:
        """Terminate the fine-tuning operation."""
        self.write(":WAVelength:FINetuning:DISable")

    @property
    def wavelength_offset_nm(self) -> float:
        """Constant offset added to the output wavelength, -0.1 to +0.1 nm."""
        return self.query_float(":WAVelength:OFFSet?") * 1e9

    @wavelength_offset_nm.setter
    def wavelength_offset_nm(self, value_nm: float) -> None:
        self._check_range("wavelength offset", value_nm, -0.1, 0.1)
        self.write(f":WAVelength:OFFSet {value_nm:.4f}nm")

    # -- optical output ----------------------------------------------------

    @property
    def output(self) -> bool:
        """Laser diode output state (``:POWer:STATe``)."""
        return bool(self.query_int(":POWer:STATe?"))

    @output.setter
    def output(self, on: bool) -> None:
        self.write(f":POWer:STATe {int(bool(on))}")

    @property
    def shutter_closed(self) -> bool:
        """Internal shutter state; ``True`` means closed, so output is blocked."""
        return bool(self.query_int(":POWer:SHUTter?"))

    @shutter_closed.setter
    def shutter_closed(self, closed: bool) -> None:
        self.write(f":POWer:SHUTter {int(bool(closed))}")

    @property
    def power_dbm(self) -> float:
        """Optical output power setpoint in dBm (-15 to +13 dBm)."""
        return self.query_float(":POWer?")

    @power_dbm.setter
    def power_dbm(self, value_dbm: float) -> None:
        self.write(f":POWer {value_dbm:.2f}DBM")

    @property
    def actual_power_dbm(self) -> float:
        """Power measured by the built-in monitor, in dBm."""
        return self.query_float(":POWer:ACTual?")

    def power_limits_dbm(self) -> tuple[float, float]:
        """Minimum and maximum configurable output power, in dBm."""
        return (
            self.query_float(":POWer? MINimum"),
            self.query_float(":POWer? MAXimum"),
        )

    @property
    def attenuation_db(self) -> float:
        """Internal attenuator value, 0 to 30 dB. Only honoured in manual mode."""
        return self.query_float(":POWer:ATTenuation?")

    @attenuation_db.setter
    def attenuation_db(self, value_db: float) -> None:
        self._check_range("attenuation", value_db, 0.0, 30.0)
        self.write(f":POWer:ATTenuation {value_db:.2f}")

    @property
    def power_control_auto(self) -> bool:
        """``True`` for automatic power control, ``False`` for manual attenuation."""
        return bool(self.query_int(":POWer:ATTenuation:AUTo?"))

    @power_control_auto.setter
    def power_control_auto(self, auto: bool) -> None:
        self.write(f":POWer:ATTenuation:AUTo {int(bool(auto))}")

    @property
    def coherence_control(self) -> bool:
        """Coherence control (linewidth broadening) state."""
        return bool(self.query_int(":COHCtrl?"))

    @coherence_control.setter
    def coherence_control(self, on: bool) -> None:
        self.write(f":COHCtrl {int(bool(on))}")

    @property
    def modulation(self) -> bool:
        """Modulation function state (``:AM:STATe``)."""
        return bool(self.query_int(":AM:STATe?"))

    @modulation.setter
    def modulation(self, on: bool) -> None:
        self.write(f":AM:STATe {int(bool(on))}")

    @property
    def modulation_source(self) -> ModulationSource:
        return ModulationSource(self.query_int(":AM:SOURce?"))

    @modulation_source.setter
    def modulation_source(self, source: ModulationSource) -> None:
        self.write(f":AM:SOURce {int(source)}")

    # -- sweep configuration ----------------------------------------------

    @property
    def sweep_start_nm(self) -> float:
        return self.query_float(":WAVelength:SWEep:STARt?") * 1e9

    @sweep_start_nm.setter
    def sweep_start_nm(self, value_nm: float) -> None:
        self.write(f":WAVelength:SWEep:STARt {value_nm:.4f}nm")

    @property
    def sweep_stop_nm(self) -> float:
        return self.query_float(":WAVelength:SWEep:STOP?") * 1e9

    @sweep_stop_nm.setter
    def sweep_stop_nm(self, value_nm: float) -> None:
        self.write(f":WAVelength:SWEep:STOP {value_nm:.4f}nm")

    def sweep_range_limits_nm(self) -> WavelengthRange:
        """Configurable sweep window at the *current* sweep speed, in nm.

        The usable span shrinks as the sweep speed rises, so re-read this after
        changing :attr:`sweep_speed_nm_per_s`.
        """
        return WavelengthRange(
            min_nm=self.query_float(":WAVelength:SWEep:RANGe:MINimum?") * 1e9,
            max_nm=self.query_float(":WAVelength:SWEep:RANGe:MAXimum?") * 1e9,
        )

    @property
    def sweep_mode(self) -> SweepMode:
        return SweepMode(self.query_int(":WAVelength:SWEep:MODe?"))

    @sweep_mode.setter
    def sweep_mode(self, mode: SweepMode) -> None:
        self.write(f":WAVelength:SWEep:MODe {int(mode)}")

    @property
    def sweep_speed_nm_per_s(self) -> float:
        return self.query_float(":WAVelength:SWEep:SPEed?")

    @sweep_speed_nm_per_s.setter
    def sweep_speed_nm_per_s(self, value: float) -> None:
        if value not in SWEEP_SPEEDS_NM_PER_S:
            raise TSLValueError(
                f"Sweep speed {value} nm/s is not selectable; "
                f"choose one of {SWEEP_SPEEDS_NM_PER_S}."
            )
        self.write(f":WAVelength:SWEep:SPEed {value:g}")

    @property
    def sweep_step_nm(self) -> float:
        """Step width for step sweep mode, in nm (0.1 pm resolution)."""
        return self.query_float(":WAVelength:SWEep:STEP?") * 1e9

    @sweep_step_nm.setter
    def sweep_step_nm(self, value_nm: float) -> None:
        self.write(f":WAVelength:SWEep:STEP {value_nm:.4f}nm")

    @property
    def sweep_dwell_s(self) -> float:
        """Wait between consecutive steps in step sweep mode, 0 to 999.9 s."""
        return self.query_float(":WAVelength:SWEep:DWELl?")

    @sweep_dwell_s.setter
    def sweep_dwell_s(self, value_s: float) -> None:
        self._check_range("dwell time", value_s, 0.0, 999.9)
        self.write(f":WAVelength:SWEep:DWELl {value_s:.1f}")

    @property
    def sweep_delay_s(self) -> float:
        """Wait between consecutive scans, 0 to 999.9 s."""
        return self.query_float(":WAVelength:SWEep:DELay?")

    @sweep_delay_s.setter
    def sweep_delay_s(self, value_s: float) -> None:
        self._check_range("sweep delay", value_s, 0.0, 999.9)
        self.write(f":WAVelength:SWEep:DELay {value_s:.1f}")

    @property
    def sweep_cycles(self) -> int:
        """Sweep repetition count, 0 to 999."""
        return self.query_int(":WAVelength:SWEep:CYCLes?")

    @sweep_cycles.setter
    def sweep_cycles(self, count: int) -> None:
        self._check_range("sweep cycles", count, 0, 999)
        self.write(f":WAVelength:SWEep:CYCLes {int(count)}")

    @property
    def sweep_count(self) -> int:
        """Number of sweeps completed so far."""
        return self.query_int(":WAVelength:SWEep:COUNt?")

    # -- sweep control -----------------------------------------------------

    @property
    def sweep_state(self) -> SweepState:
        return SweepState(self.query_int(":WAVelength:SWEep?"))

    def start_sweep(self) -> None:
        """Start a single scan."""
        self.write(":WAVelength:SWEep 1")

    def start_repeat_sweep(self) -> None:
        """Start a repeating scan, honouring :attr:`sweep_cycles`."""
        self.write(":WAVelength:SWEep:REPeat")

    def stop_sweep(self) -> None:
        self.write(":WAVelength:SWEep 0")

    # -- logged sweep data -------------------------------------------------

    @property
    def logged_points(self) -> int:
        """Number of logged data points held in the instrument, 0 to 500,000."""
        return self.query_int(":READout:POINts?")

    def read_log(self) -> SweepLog:
        """Read back the wavelength and power arrays logged during the last sweep.

        In SCPI mode the wavelength array is IEEE-754 float64 in metres and the
        power array is float32 in dBm, both little-endian (manual p. 87).
        """
        inst = self._require_open()
        # The payload is raw binary, so a 0x0D byte inside it would look like the
        # CR delimiter. The length header tells PyVISA how much to read, so drop
        # the termination character for the duration of the transfer.
        saved_termination = inst.read_termination
        inst.read_termination = None
        try:
            wavelength_m = inst.query_binary_values(
                ":READout:DATa?", datatype="d", is_big_endian=False, container=np.array
            )
            power_dbm = inst.query_binary_values(
                ":READout:DATa:POWer?",
                datatype="f",
                is_big_endian=False,
                container=np.array,
            )
        finally:
            inst.read_termination = saved_termination
        return SweepLog(wavelength_nm=wavelength_m * 1e9, power_dbm=power_dbm)

    # -- triggers ----------------------------------------------------------

    @property
    def trigger_input_external(self) -> bool:
        return bool(self.query_int(":TRIGger:INPut:EXTernal?"))

    @trigger_input_external.setter
    def trigger_input_external(self, enabled: bool) -> None:
        self.write(f":TRIGger:INPut:EXTernal {int(bool(enabled))}")

    @property
    def trigger_input_standby(self) -> bool:
        """``True`` when the instrument is waiting for a trigger to start a sweep."""
        return bool(self.query_int(":TRIGger:INPut:STANdby?"))

    @trigger_input_standby.setter
    def trigger_input_standby(self, standby: bool) -> None:
        self.write(f":TRIGger:INPut:STANdby {int(bool(standby))}")

    def soft_trigger(self) -> None:
        """Issue a software trigger, running a sweep held in standby."""
        self.write(":TRIGger:INPut:SOFTtrigger")

    @property
    def trigger_output(self) -> TriggerOutput:
        return TriggerOutput(self.query_int(":TRIGger:OUTPut?"))

    @trigger_output.setter
    def trigger_output(self, timing: TriggerOutput) -> None:
        self.write(f":TRIGger:OUTPut {int(timing)}")

    @property
    def trigger_output_step_nm(self) -> float:
        """Wavelength interval between output trigger pulses, in nm."""
        return self.query_float(":TRIGger:OUTPut:STEP?") * 1e9

    @trigger_output_step_nm.setter
    def trigger_output_step_nm(self, value_nm: float) -> None:
        self.write(f":TRIGger:OUTPut:STEP {value_nm:.4f}nm")

    @property
    def trigger_through(self) -> bool:
        """Pass the input trigger through to the output trigger port."""
        return bool(self.query_int(":TRIGger:THRough?"))

    @trigger_through.setter
    def trigger_through(self, on: bool) -> None:
        self.write(f":TRIGger:THRough {int(bool(on))}")

    # -- system ------------------------------------------------------------

    @property
    def interlock_tripped(self) -> bool:
        """``True`` when the external interlock is engaged and output is blocked."""
        return bool(self.query_int(":SYSTem:LOCK?"))

    def error(self) -> tuple[int, str]:
        """Pop one entry off the error queue as ``(code, message)``."""
        response = self.query(":SYSTem:ERRor?")
        code, _, message = response.partition(",")
        try:
            return int(float(code)), message.strip().strip('"')
        except ValueError as exc:
            raise TSLProtocolError(f":SYSTem:ERRor? returned {response!r}") from exc

    def raise_on_error(self) -> None:
        """Raise :class:`TSLCommandError` if the instrument has queued an error."""
        code, message = self.error()
        if code != 0:
            raise TSLCommandError(f"Instrument error {code}: {message or 'unknown'}")

    def alert(self) -> str:
        """Return the current alert information (see manual 7.4.6)."""
        return self.query(":SYSTem:ALERt?")

    def firmware_version(self) -> str:
        return self.query(":SYSTem:VERSion?")

    def product_code(self) -> str:
        return self.query(":SYSTem:CODe?")

    @property
    def display_brightness(self) -> int:
        """Front-panel brightness, 0 to 100 %."""
        return self.query_int(":DISPlay:BRIGhtness?")

    @display_brightness.setter
    def display_brightness(self, percent: int) -> None:
        self._check_range("display brightness", percent, 0, 100)
        self.write(f":DISPlay:BRIGhtness {int(percent)}")

    def shutdown(self) -> None:
        """Shut the instrument down. The VISA session dies with it."""
        self.write(":SPECial:SHUTdown")

    def reboot(self) -> None:
        """Restart the instrument. The VISA session dies with it."""
        self.write(":SPECial:REBoot")

    # -- helpers -----------------------------------------------------------

    @staticmethod
    def _check_range(name: str, value: float, low: float, high: float) -> None:
        if not low <= value <= high:
            raise TSLValueError(f"{name} {value} is outside {low} to {high}.")
