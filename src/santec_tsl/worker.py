"""Background VISA worker, so the GUI thread never blocks on instrument I/O.

The worker owns the :class:`~santec_tsl.driver.TSL570` instance and lives in its
own ``QThread``. The GUI talks to it exclusively through queued signal/slot
connections, and telemetry is pushed back to the GUI on a timer.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot

from .driver import TSL570, SweepMode, SweepState
from .exceptions import TSLError

log = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL_MS = 1000


@dataclass(frozen=True)
class Capabilities:
    """What the connected laser reports, discovered when the session opens."""

    resource: str
    identity: str
    firmware: str
    min_wavelength_nm: float
    max_wavelength_nm: float
    min_power_dbm: float
    max_power_dbm: float


@dataclass(frozen=True)
class SweepSettings:
    """The sweep configuration currently held by the instrument."""

    start_nm: float
    stop_nm: float
    speed_nm_per_s: float
    step_nm: float
    dwell_s: float
    cycles: int
    mode: SweepMode
    range_min_nm: float
    range_max_nm: float


@dataclass(frozen=True)
class Telemetry:
    """One poll of the laser. ``None`` means "not readable this time round"."""

    wavelength_nm: float | None = None
    frequency_thz: float | None = None
    power_dbm: float | None = None
    actual_power_dbm: float | None = None
    attenuation_db: float | None = None
    output: bool | None = None
    shutter_closed: bool | None = None
    power_control_auto: bool | None = None
    coherence_control: bool | None = None
    interlock: bool | None = None
    sweep_state: SweepState | None = None
    sweep_count: int | None = None


class LaserWorker(QObject):
    """Serialises every driver call onto a single background thread."""

    connected = pyqtSignal(object)  # Capabilities
    disconnected = pyqtSignal()
    telemetry = pyqtSignal(object)  # Telemetry
    sweep_settings = pyqtSignal(object)  # SweepSettings
    command_failed = pyqtSignal(str, str)  # title, message
    poll_failed = pyqtSignal(str)
    busy = pyqtSignal(bool)

    def __init__(self) -> None:
        super().__init__()
        self._tsl: TSL570 | None = None
        self._timer: QTimer | None = None
        self._interval_ms = DEFAULT_POLL_INTERVAL_MS

    # -- lifecycle ---------------------------------------------------------

    @pyqtSlot()
    def start(self) -> None:
        """Create the poll timer. Runs once, on the worker thread."""
        log.info("Worker started, poll interval %d ms", self._interval_ms)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self._interval_ms)
        self._timer.timeout.connect(self._poll)

    @pyqtSlot()
    def stop(self) -> None:
        """Close the session and tear down. Runs on the worker thread at shutdown."""
        log.info("Worker stopping")
        if self._timer is not None:
            self._timer.stop()
        self._close_session()

    @pyqtSlot(str)
    def open_device(self, resource: str) -> None:
        if self._tsl is not None:
            log.debug("open_device(%s) ignored, already connected", resource)
            return
        log.info("Connecting to %s", resource)
        self.busy.emit(True)
        try:
            tsl = TSL570(resource=resource)
            tsl.open()
            limits = tsl.wavelength_limits_nm()
            min_power_dbm, max_power_dbm = tsl.power_limits_dbm()
            caps = Capabilities(
                resource=resource,
                identity=tsl.identify(),
                firmware=tsl.firmware_version(),
                min_wavelength_nm=limits.min_nm,
                max_wavelength_nm=limits.max_nm,
                min_power_dbm=min_power_dbm,
                max_power_dbm=max_power_dbm,
            )
        except (TSLError, OSError, ValueError) as exc:
            log.error("Connection to %s failed: %s", resource, exc)
            self._tsl = None
            self.command_failed.emit("Connection failed", str(exc))
            self.busy.emit(False)
            return
        self._tsl = tsl
        log.info("Connected to %s: %s", resource, caps.identity)
        self.busy.emit(False)
        self.connected.emit(caps)
        self.refresh_sweep_settings()
        self._poll()

    @pyqtSlot()
    def close_device(self) -> None:
        log.info("Disconnecting")
        if self._timer is not None:
            self._timer.stop()
        self._close_session()
        self.disconnected.emit()

    def _close_session(self) -> None:
        if self._tsl is None:
            return
        try:
            self._tsl.close()
        except (TSLError, OSError) as exc:
            log.warning("Error while closing the session, ignoring: %s", exc)
        self._tsl = None

    # -- configuration -----------------------------------------------------

    @pyqtSlot(int)
    def set_poll_interval(self, interval_ms: int) -> None:
        log.info("Poll interval set to %d ms", interval_ms)
        self._interval_ms = interval_ms
        if self._timer is not None:
            self._timer.setInterval(interval_ms)

    # -- output commands ---------------------------------------------------

    @pyqtSlot(float)
    def set_wavelength(self, value_nm: float) -> None:
        log.info("Requesting wavelength %.4f nm", value_nm)
        self._run("Wavelength failed", lambda t: setattr(t, "wavelength_nm", value_nm))

    @pyqtSlot(float)
    def set_frequency(self, value_thz: float) -> None:
        log.info("Requesting frequency %.6f THz", value_thz)
        self._run("Frequency failed", lambda t: setattr(t, "frequency_thz", value_thz))

    @pyqtSlot(float)
    def set_power(self, value_dbm: float) -> None:
        log.info("Requesting power %.2f dBm", value_dbm)
        self._run("Power failed", lambda t: setattr(t, "power_dbm", value_dbm))

    @pyqtSlot(float)
    def set_attenuation(self, value_db: float) -> None:
        log.info("Requesting attenuation %.2f dB", value_db)
        self._run("Attenuation failed", lambda t: setattr(t, "attenuation_db", value_db))

    @pyqtSlot(bool)
    def set_power_control_auto(self, auto: bool) -> None:
        log.info("Requesting %s power control", "auto" if auto else "manual")
        self._run(
            "Power control failed", lambda t: setattr(t, "power_control_auto", auto)
        )

    @pyqtSlot(bool)
    def set_output(self, on: bool) -> None:
        log.info("Requesting output %s", "ON" if on else "OFF")
        self._run("Output control failed", lambda t: setattr(t, "output", on))

    @pyqtSlot(bool)
    def set_shutter(self, closed: bool) -> None:
        log.info("Requesting shutter %s", "closed" if closed else "open")
        self._run("Shutter control failed", lambda t: setattr(t, "shutter_closed", closed))

    @pyqtSlot(bool)
    def set_coherence_control(self, on: bool) -> None:
        log.info("Requesting coherence control %s", "ON" if on else "OFF")
        self._run(
            "Coherence control failed", lambda t: setattr(t, "coherence_control", on)
        )

    # -- sweep commands ----------------------------------------------------

    @pyqtSlot(object)
    def apply_sweep(self, settings: SweepSettings) -> None:
        """Push a whole sweep configuration, then read back what stuck."""
        log.info("Applying sweep settings: %s", settings)

        def action(tsl: TSL570) -> None:
            # Speed first: it constrains the configurable span.
            tsl.sweep_speed_nm_per_s = settings.speed_nm_per_s
            tsl.sweep_mode = settings.mode
            tsl.sweep_start_nm = settings.start_nm
            tsl.sweep_stop_nm = settings.stop_nm
            tsl.sweep_step_nm = settings.step_nm
            tsl.sweep_dwell_s = settings.dwell_s
            tsl.sweep_cycles = settings.cycles

        if self._run("Sweep setup failed", action):
            self.refresh_sweep_settings()

    @pyqtSlot(bool)
    def start_sweep(self, repeat: bool) -> None:
        log.info("Starting %s sweep", "repeat" if repeat else "single")
        self._run(
            "Sweep start failed",
            lambda t: t.start_repeat_sweep() if repeat else t.start_sweep(),
        )

    @pyqtSlot()
    def stop_sweep(self) -> None:
        log.info("Stopping sweep")
        self._run("Sweep stop failed", lambda t: t.stop_sweep())

    @pyqtSlot()
    def soft_trigger(self) -> None:
        log.info("Issuing a soft trigger")
        self._run("Soft trigger failed", lambda t: t.soft_trigger())

    @pyqtSlot()
    def refresh_sweep_settings(self) -> None:
        tsl = self._tsl
        if tsl is None:
            return
        try:
            span = tsl.sweep_range_limits_nm()
            settings = SweepSettings(
                start_nm=tsl.sweep_start_nm,
                stop_nm=tsl.sweep_stop_nm,
                speed_nm_per_s=tsl.sweep_speed_nm_per_s,
                step_nm=tsl.sweep_step_nm,
                dwell_s=tsl.sweep_dwell_s,
                cycles=tsl.sweep_cycles,
                mode=tsl.sweep_mode,
                range_min_nm=span.min_nm,
                range_max_nm=span.max_nm,
            )
        except (TSLError, OSError, ValueError) as exc:
            log.warning("Could not read the sweep settings: %s", exc)
            self.poll_failed.emit(str(exc))
            return
        log.debug("Sweep settings: %s", settings)
        self.sweep_settings.emit(settings)

    # -- plumbing ----------------------------------------------------------

    def _run(self, title: str, action) -> bool:
        """Run a driver call, reporting failures to the GUI instead of raising."""
        tsl = self._tsl
        if tsl is None:
            log.debug("%r skipped, not connected", title)
            return False
        if self._timer is not None:
            self._timer.stop()
        self.busy.emit(True)
        try:
            action(tsl)
            return True
        except (TSLError, OSError, ValueError) as exc:
            log.error("%s: %s", title, exc)
            self.command_failed.emit(title, str(exc))
            return False
        finally:
            self.busy.emit(False)
            self._poll()

    @pyqtSlot()
    def _poll(self) -> None:
        tsl = self._tsl
        if tsl is None:
            return
        if self._timer is not None:
            self._timer.stop()

        readings: dict[str, object] = {}
        errors: list[str] = []

        def read(name: str, fn) -> None:
            try:
                readings[name] = fn()
            except (TSLError, ValueError) as exc:
                log.debug("Reading %r failed: %s", name, exc)
                readings[name] = None
                errors.append(str(exc))

        try:
            read("wavelength_nm", lambda: tsl.wavelength_nm)
            read("frequency_thz", lambda: tsl.frequency_thz)
            read("power_dbm", lambda: tsl.power_dbm)
            read("actual_power_dbm", lambda: tsl.actual_power_dbm)
            read("attenuation_db", lambda: tsl.attenuation_db)
            read("output", lambda: tsl.output)
            read("shutter_closed", lambda: tsl.shutter_closed)
            read("power_control_auto", lambda: tsl.power_control_auto)
            read("coherence_control", lambda: tsl.coherence_control)
            read("interlock", lambda: tsl.interlock_tripped)
            read("sweep_state", lambda: tsl.sweep_state)
            read("sweep_count", lambda: tsl.sweep_count)
        except OSError as exc:  # the link went away underneath us
            log.error("Connection lost while polling: %s", exc)
            self.command_failed.emit("Connection lost", str(exc))
            self.close_device()
            return

        log.debug("Poll: %s", readings)
        self.telemetry.emit(Telemetry(**readings))  # type: ignore[arg-type]

        if errors:
            log.warning("Poll finished with %d error(s), first: %s", len(errors), errors[0])
            self.poll_failed.emit(errors[0])
        self._rearm()

    def _rearm(self) -> None:
        if self._tsl is not None and self._timer is not None:
            self._timer.start(self._interval_ms)
