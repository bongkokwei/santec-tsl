"""PyQt6 control panel for a santec TSL-570 tunable laser.

All VISA I/O happens on a background thread (see :mod:`santec_tsl.worker`), so
readings update by themselves and the window never freezes on a slow reply.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime

import pyvisa
from PyQt6.QtCore import Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QStatusBar,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .driver import SWEEP_SPEEDS_NM_PER_S, SweepMode, SweepState
from .worker import Capabilities, LaserWorker, SweepSettings, Telemetry

log = logging.getLogger(__name__)

POLL_INTERVALS = [("0.5 s", 500), ("1 s", 1000), ("2 s", 2000), ("5 s", 5000)]

#: Trailing entry in the resource dropdown. LAN sockets are never enumerated by
#: ``list_resources()``, so there has to be a way to type one in.
MANUAL_ENTRY = "Enter an address..."
EXAMPLE_SOCKET = "TCPIP0::192.168.1.161::5000::SOCKET"

SWEEP_MODE_LABELS = [
    ("Step, one way", SweepMode.STEP_ONE_WAY),
    ("Continuous, one way", SweepMode.CONTINUOUS_ONE_WAY),
    ("Step, two way", SweepMode.STEP_TWO_WAY),
    ("Continuous, two way", SweepMode.CONTINUOUS_TWO_WAY),
]

SWEEP_STATE_TEXT = {
    SweepState.STOPPED: ("Sweep idle", "idle"),
    SweepState.RUNNING: ("Sweep running", "ok"),
    SweepState.STANDING_BY_TRIGGER: ("Awaiting trigger", "warn"),
    SweepState.PREPARING: ("Sweep preparing", "warn"),
}

STYLESHEET = """
QWidget {
    background: #f4f6f9;
    color: #1e2532;
    font-family: "Segoe UI", "Helvetica Neue", "DejaVu Sans", sans-serif;
    font-size: 13px;
}
QGroupBox {
    background: #ffffff;
    border: 1px solid #dbe1ea;
    border-radius: 8px;
    margin-top: 14px;
    padding: 14px;
    font-weight: 600;
}
QGroupBox::title {
    subcontrol-origin: margin;
    left: 12px;
    padding: 0 4px;
    color: #6b7686;
    font-size: 11px;
    letter-spacing: 1px;
}
QLabel { background: transparent; }
QLabel#fieldLabel { color: #6b7686; }
QLabel#headline { font-size: 18px; font-weight: 600; }
QLabel#tileTitle { color: #6b7686; font-size: 11px; letter-spacing: 1px; }
QLabel#tileValue { font-size: 22px; font-weight: 600; color: #1e2532; }
QLabel#tileUnit { color: #8b95a5; font-size: 12px; }
QFrame#tile {
    background: #f8fafc;
    border: 1px solid #e3e8ef;
    border-radius: 8px;
}
QFrame#pill {
    background: #eef1f5;
    border: 1px solid #dbe1ea;
    border-radius: 13px;
}
QLabel#pillText { font-weight: 600; color: #6b7686; }

QPushButton {
    background: #ffffff;
    border: 1px solid #c8d0dc;
    border-radius: 6px;
    padding: 7px 16px;
    min-height: 20px;
    font-weight: 500;
}
QPushButton:hover { background: #eef2f7; }
QPushButton:pressed { background: #e2e8f0; }
QPushButton:disabled { color: #a6aebb; background: #f2f4f7; border-color: #e0e5ec; }

QPushButton#primary {
    background: #2563eb;
    border: 1px solid #1d4ed8;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primary:hover { background: #1d4ed8; }
QPushButton#primary:pressed { background: #1a43bf; }
QPushButton#primary:disabled { background: #b7c6e8; border-color: #b7c6e8; color: #eef2ff; }

QPushButton#danger {
    background: #dc2626;
    border: 1px solid #b91c1c;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#danger:hover { background: #b91c1c; }
QPushButton#danger:disabled { background: #edb6b6; border-color: #edb6b6; color: #fff5f5; }

QPushButton#segment { padding: 7px 22px; }
QPushButton#segment:checked {
    background: #1e2532;
    border-color: #1e2532;
    color: #ffffff;
    font-weight: 600;
}

QComboBox, QSpinBox, QDoubleSpinBox {
    background: #ffffff;
    border: 1px solid #c8d0dc;
    border-radius: 6px;
    padding: 6px 8px;
    min-height: 20px;
}
/* Keep the text clear of the drop-down chevron. */
QComboBox { padding-right: 22px; }
QComboBox QAbstractItemView {
    background: #ffffff;
    border: 1px solid #c8d0dc;
    border-radius: 6px;
    padding: 4px;
    outline: none;
    selection-background-color: #eef2f7;
    selection-color: #1e2532;
}
QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus { border-color: #2563eb; }
QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background: #f2f4f7;
    color: #a6aebb;
}
/* The drop-down button is deliberately left unstyled. Any rule here makes Qt
   draw the subcontrol from the sheet, and with no image to put in it the arrow
   disappears. Fusion (set in main()) draws a flat chevron that suits the panel;
   the native Windows style would draw a themed button, which does not. */
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    width: 16px;
    border: none;
    background: transparent;
}
QCheckBox { spacing: 8px; }

QTabWidget::pane {
    background: #ffffff;
    border: 1px solid #dbe1ea;
    border-radius: 8px;
    top: -1px;
}
QTabBar::tab {
    background: #eef1f5;
    border: 1px solid #dbe1ea;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 7px 16px;
    margin-right: 4px;
    color: #6b7686;
}
QTabBar::tab:selected { background: #ffffff; color: #1e2532; font-weight: 600; }
QTabBar::tab:disabled { color: #a6aebb; }

/* The front-panel-style wavelength dial. Dark, so the lit digit reads as the
   cursor the way it does on the instrument's own touchscreen. */
QFrame#dial { background: #12161d; border: 1px solid #2a3240; border-radius: 8px; }
QFrame#dial:focus { border-color: #2563eb; }
QLabel#dialDigit {
    color: #e8edf5;
    font-family: "DejaVu Sans Mono", "Consolas", "Courier New", monospace;
    font-size: 46px;
    font-weight: 600;
    padding: 6px 1px;
}
/* The selected place, drawn inverted. Repolished on every move -- Qt only
   re-evaluates a property selector when the widget's style is refreshed. */
QLabel#dialDigit[selected="true"] { background: #e8edf5; color: #12161d; border-radius: 4px; }
QLabel#dialDigit[stale="true"] { color: #59637a; }
QLabel#dialUnit { color: #8b95a5; font-size: 16px; }
QLabel#dialRange { color: #8b95a5; font-size: 12px; }
QLabel#dialHint { color: #59637a; font-size: 11px; }

QStatusBar { background: #eef1f5; color: #6b7686; }
QStatusBar::item { border: none; }
"""

STATUS_COLOURS = {
    "ok": ("#dcfce7", "#15803d"),
    "warn": ("#fef3c7", "#b45309"),
    "error": ("#fee2e2", "#b91c1c"),
    "idle": ("#eef1f5", "#6b7686"),
}


class Tile(QFrame):
    """A single large read-only telemetry value."""

    def __init__(self, title: str, unit: str = "") -> None:
        super().__init__()
        self.setObjectName("tile")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(2)

        heading = QLabel(title.upper())
        heading.setObjectName("tileTitle")
        layout.addWidget(heading)

        value_row = QHBoxLayout()
        value_row.setSpacing(4)
        self.value_label = QLabel("--")
        self.value_label.setObjectName("tileValue")
        self.value_label.setFont(QFont("DejaVu Sans Mono", 15, QFont.Weight.DemiBold))
        value_row.addWidget(self.value_label)
        if unit:
            unit_label = QLabel(unit)
            unit_label.setObjectName("tileUnit")
            unit_label.setAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom
            )
            value_row.addWidget(unit_label)
        value_row.addStretch()
        layout.addLayout(value_row)

    def set_value(self, text: str) -> None:
        self.value_label.setText(text)


class StatusPill(QFrame):
    """Rounded badge used for the connection, output, interlock and sweep flags."""

    def __init__(self, text: str = "") -> None:
        super().__init__()
        self.setObjectName("pill")
        self.setFixedHeight(26)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        self.label = QLabel(text)
        self.label.setObjectName("pillText")
        layout.addWidget(self.label)

    def set_state(self, text: str, state: str) -> None:
        background, foreground = STATUS_COLOURS[state]
        self.label.setText(text)
        self.setStyleSheet(
            f"QFrame#pill {{ background: {background}; border: 1px solid {background}; }}"
            f"QLabel#pillText {{ color: {foreground}; }}"
        )


class WavelengthDial(QFrame):
    """A front-panel-style wavelength readout you tune one decimal place at a time.

    The instrument's own touchscreen puts a cursor on a single digit and steps
    that place; this is the same idea with a mouse. Left/right moves the cursor,
    the wheel (or up/down) steps the selected place by one, and every change is
    sent straight to the laser -- there is no Apply button by design.
    """

    value_changed = pyqtSignal(float)

    #: Power of ten each column carries. ``None`` is the decimal point, which
    #: is drawn but never selectable.
    PLACES = (3, 2, 1, 0, None, -1, -2, -3, -4)
    DEFAULT_INDEX = 6  # the 0.01 nm column

    #: Commands are coalesced to this rate, so spinning the wheel hard leaves
    #: one queued write rather than fifty.
    SEND_INTERVAL_MS = 150

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("dial")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._value = 1550.0
        self._low = 1480.0
        self._high = 1640.0
        self._index = self.DEFAULT_INDEX
        self._live = False
        self._pending: float | None = None

        self._send_timer = QTimer(self)
        self._send_timer.setSingleShot(True)
        self._send_timer.setInterval(self.SEND_INTERVAL_MS)
        self._send_timer.timeout.connect(self._flush)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 12, 16, 10)
        outer.setSpacing(4)

        top = QHBoxLayout()
        self.range_label = QLabel("")
        self.range_label.setObjectName("dialRange")
        top.addWidget(self.range_label)
        top.addStretch()
        unit = QLabel("nm")
        unit.setObjectName("dialUnit")
        top.addWidget(unit)
        outer.addLayout(top)

        row = QHBoxLayout()
        row.setSpacing(0)
        row.addStretch()
        self._digits: list[QLabel] = []
        for place in self.PLACES:
            label = QLabel(".")
            label.setObjectName("dialDigit")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            if place is not None:
                label.setMinimumWidth(34)
            self._digits.append(label)
            row.addWidget(label)
        row.addStretch()
        outer.addLayout(row)

        hint = QLabel(
            "\u2190 \u2192 pick the digit   \u00b7   wheel or \u2191 \u2193 to tune   "
            "\u00b7   sent to the laser as you go"
        )
        hint.setObjectName("dialHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        outer.addWidget(hint)

        self.setEnabled(False)
        self.set_limits(self._low, self._high)
        self._redraw()

    # -- state -------------------------------------------------------------

    def set_limits(self, low: float, high: float) -> None:
        self._low, self._high = low, high
        self.range_label.setText(f"{low:.4f} To {high:.4f}")
        self.set_value(self._value)

    def set_live(self, live: bool) -> None:
        """Grey the digits out while there is nothing to tune."""
        if self._live == live:
            return
        self._live = live
        self.setEnabled(live)
        self._redraw()

    def set_value(self, value: float) -> None:
        """Set the displayed value without emitting -- used for telemetry."""
        self._value = min(max(value, self._low), self._high)
        self._redraw()

    def sync_value(self, value: float | None) -> None:
        """Mirror the instrument, unless the user is the one driving."""
        if value is None or self.hasFocus() or self._send_timer.isActive():
            return
        self.set_value(value)

    def value(self) -> float:
        return self._value

    # -- editing -----------------------------------------------------------

    def select_index(self, index: int) -> None:
        if 0 <= index < len(self.PLACES) and self.PLACES[index] is not None:
            self._index = index
            self._redraw()

    def move_cursor(self, delta: int) -> None:
        index = self._index + delta
        while 0 <= index < len(self.PLACES) and self.PLACES[index] is None:
            index += delta  # step over the decimal point
        self.select_index(index)

    def step(self, direction: int) -> None:
        """Add or subtract one unit of the selected place."""
        if not self._live or direction == 0:
            return
        place = self.PLACES[self._index]
        stepped = round(self._value + direction * 10.0**place, 4)
        clamped = min(max(stepped, self._low), self._high)
        if clamped == self._value:
            return
        self._value = clamped
        self._redraw()
        self._queue(clamped)

    def _queue(self, value: float) -> None:
        """Send now if the line is quiet, otherwise fold into the next tick."""
        if self._send_timer.isActive():
            self._pending = value
            return
        self.value_changed.emit(value)
        self._send_timer.start()

    def _flush(self) -> None:
        if self._pending is None:
            return
        value, self._pending = self._pending, None
        self.value_changed.emit(value)
        self._send_timer.start()

    # -- events ------------------------------------------------------------

    def wheelEvent(self, event) -> None:
        notches = event.angleDelta().y()
        if notches:
            self.step(1 if notches > 0 else -1)
            event.accept()
        else:
            super().wheelEvent(event)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key.Key_Left:
            self.move_cursor(-1)
        elif key == Qt.Key.Key_Right:
            self.move_cursor(1)
        elif key == Qt.Key.Key_Up:
            self.step(1)
        elif key == Qt.Key.Key_Down:
            self.step(-1)
        else:
            super().keyPressEvent(event)
            return
        event.accept()

    def mousePressEvent(self, event) -> None:
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        hit = self.childAt(event.position().toPoint())
        if hit in self._digits:
            self.select_index(self._digits.index(hit))
        event.accept()

    def focusInEvent(self, event) -> None:
        super().focusInEvent(event)
        self._redraw()

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self._redraw()

    # -- painting ----------------------------------------------------------

    def _redraw(self) -> None:
        # Nine columns wide, so 1554.0000 lines up with 999.9999 and 1640.0000.
        text = f"{self._value:9.4f}" if self._live else "    --.----"[-9:]
        for i, label in enumerate(self._digits):
            label.setText(text[i])
            self._set_flag(label, "selected", self._live and i == self._index)
            self._set_flag(label, "stale", not self._live)

    @staticmethod
    def _set_flag(label: QLabel, name: str, on: bool) -> None:
        value = "true" if on else "false"
        if label.property(name) == value:
            return
        label.setProperty(name, value)
        label.style().unpolish(label)
        label.style().polish(label)


class MainWindow(QMainWindow):
    request_open = pyqtSignal(str)
    request_close = pyqtSignal()
    request_wavelength = pyqtSignal(float)
    request_frequency = pyqtSignal(float)
    request_power = pyqtSignal(float)
    request_attenuation = pyqtSignal(float)
    request_power_auto = pyqtSignal(bool)
    request_output = pyqtSignal(bool)
    request_shutter = pyqtSignal(bool)
    request_coherence = pyqtSignal(bool)
    request_sweep_apply = pyqtSignal(object)
    request_sweep_start = pyqtSignal(bool)
    request_sweep_stop = pyqtSignal()
    request_interval = pyqtSignal(int)
    request_stop = pyqtSignal()

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("santec TSL-570 Control")
        self.setMinimumWidth(940)
        self.setStyleSheet(STYLESHEET)

        self.connected = False
        self.output_on = False
        self.sweeping = False
        self.busy = False
        self._manual_resources: list[str] = []

        log.info("Opening the control panel")
        self._build_ui()
        self._start_worker()
        self.refresh_resources()
        self._apply_enabled_state()

    # -- construction ------------------------------------------------------

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(18, 14, 18, 10)
        root.setSpacing(14)

        root.addLayout(self._build_header())
        root.addWidget(self._build_connection_group())

        columns = QHBoxLayout()
        columns.setSpacing(14)
        columns.addWidget(self._build_control_group(), 1)
        columns.addWidget(self._build_readings_group(), 1)
        root.addLayout(columns)

        root.addWidget(self._build_sweep_group())
        root.addStretch()

        self.setStatusBar(QStatusBar())
        self.status_message = QLabel("Ready")
        self.updated_label = QLabel("")
        self.statusBar().addWidget(self.status_message, 1)
        self.statusBar().addPermanentWidget(self.updated_label)

    def _build_header(self) -> QHBoxLayout:
        header = QHBoxLayout()
        title = QLabel("santec TSL-570")
        title.setObjectName("headline")
        header.addWidget(title)
        header.addStretch()
        self.connection_pill = StatusPill()
        self.connection_pill.set_state("Disconnected", "idle")
        header.addWidget(self.connection_pill)
        return header

    def _build_connection_group(self) -> QGroupBox:
        group = QGroupBox("CONNECTION")
        row = QHBoxLayout(group)
        row.setSpacing(10)

        label = QLabel("VISA resource")
        label.setObjectName("fieldLabel")
        row.addWidget(label)

        self.resource_combo = QComboBox()
        self.resource_combo.setMinimumWidth(340)
        self.resource_combo.activated.connect(self._on_resource_activated)
        row.addWidget(self.resource_combo, 1)

        self.rescan_button = QPushButton("Rescan")
        self.rescan_button.setFixedWidth(90)
        self.rescan_button.clicked.connect(self.refresh_resources)
        row.addWidget(self.rescan_button)

        self.connect_button = QPushButton("Connect")
        self.connect_button.setObjectName("primary")
        self.connect_button.setFixedWidth(130)
        self.connect_button.clicked.connect(self.toggle_connection)
        row.addWidget(self.connect_button)
        return group

    def _build_control_group(self) -> QGroupBox:
        group = QGroupBox("OUTPUT CONTROL")
        outer = QVBoxLayout(group)
        outer.setContentsMargins(0, 6, 0, 0)

        self.control_tabs = QTabWidget()
        self.control_tabs.addTab(self._build_setpoint_tab(), "Set values")
        self.control_tabs.addTab(self._build_tune_tab(), "Tune wavelength")
        outer.addWidget(self.control_tabs)
        return group

    def _build_tune_tab(self) -> QWidget:
        """Digit-at-a-time wavelength tuning, after the instrument's own panel."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        self.wavelength_dial = WavelengthDial()
        self.wavelength_dial.value_changed.connect(self.request_wavelength)
        layout.addWidget(self.wavelength_dial)
        layout.addStretch()
        return page

    def _build_setpoint_tab(self) -> QWidget:
        page = QWidget()
        layout = QGridLayout(page)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(10)
        layout.setColumnStretch(1, 1)

        self.wavelength_spin = self._add_apply_row(
            layout, 0, "Wavelength (nm)", 1000.0, 2000.0, 4, 0.001,
            lambda: self.request_wavelength.emit(self.wavelength_spin.value()),
        )
        self.frequency_spin = self._add_apply_row(
            layout, 1, "Frequency (THz)", 150.0, 300.0, 6, 0.001,
            lambda: self.request_frequency.emit(self.frequency_spin.value()),
        )
        self.power_spin = self._add_apply_row(
            layout, 2, "Power (dBm)", -15.0, 13.0, 2, 0.1,
            lambda: self.request_power.emit(self.power_spin.value()),
        )
        self.attenuation_spin = self._add_apply_row(
            layout, 3, "Attenuation (dB)", 0.0, 30.0, 2, 0.1,
            lambda: self.request_attenuation.emit(self.attenuation_spin.value()),
        )

        self.limits_label = QLabel("")
        self.limits_label.setObjectName("fieldLabel")
        # Spans both columns: the line is long, and the tab inset costs width.
        layout.addWidget(self.limits_label, 4, 0, 1, 2)

        toggles = QHBoxLayout()
        toggles.setSpacing(14)
        self.auto_power_check = QCheckBox("Auto power control")
        self.auto_power_check.clicked.connect(self.request_power_auto)
        toggles.addWidget(self.auto_power_check)
        self.coherence_check = QCheckBox("Coherence control")
        self.coherence_check.clicked.connect(self.request_coherence)
        toggles.addWidget(self.coherence_check)
        toggles.addStretch()
        layout.addLayout(toggles, 5, 0, 1, 2)

        self.output_button = QPushButton("Enable Output")
        self.output_button.setObjectName("primary")
        self.output_button.setMinimumHeight(42)
        self.output_button.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed
        )
        self.output_button.clicked.connect(self.toggle_output)
        layout.addWidget(self.output_button, 6, 0, 1, 2)

        self.shutter_button = QPushButton("Close shutter")
        self.shutter_button.clicked.connect(self.toggle_shutter)
        layout.addWidget(self.shutter_button, 7, 0, 1, 2)
        layout.setRowStretch(8, 1)
        return page

    def _add_apply_row(
        self, layout, row, label_text, low, high, decimals, step, on_apply
    ) -> QDoubleSpinBox:
        """One "label / spinbox / Apply" line in the output-control grid."""
        label = QLabel(label_text)
        label.setObjectName("fieldLabel")
        layout.addWidget(label, row, 0)

        line = QHBoxLayout()
        line.setSpacing(8)
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setKeyboardTracking(False)
        spin.setMinimumWidth(150)
        line.addWidget(spin)
        button = QPushButton("Apply")
        button.setFixedWidth(90)
        button.clicked.connect(on_apply)
        line.addWidget(button)
        line.addStretch()
        layout.addLayout(line, row, 1)

        self._apply_widgets = getattr(self, "_apply_widgets", [])
        self._apply_widgets.extend([spin, button])
        return spin

    def _build_readings_group(self) -> QGroupBox:
        group = QGroupBox("LIVE READINGS")
        layout = QVBoxLayout(group)
        layout.setSpacing(10)

        badges = QHBoxLayout()
        badges.setSpacing(8)
        self.output_pill = StatusPill()
        self.output_pill.set_state("Output --", "idle")
        self.interlock_pill = StatusPill()
        self.interlock_pill.set_state("Interlock --", "idle")
        self.sweep_pill = StatusPill()
        self.sweep_pill.set_state("Sweep --", "idle")
        badges.addWidget(self.output_pill)
        badges.addWidget(self.interlock_pill)
        badges.addWidget(self.sweep_pill)
        badges.addStretch()
        layout.addLayout(badges)

        tiles = QGridLayout()
        tiles.setSpacing(10)
        self.wavelength_tile = Tile("Wavelength", "nm")
        self.frequency_tile = Tile("Frequency", "THz")
        self.setpoint_tile = Tile("Power setpoint", "dBm")
        self.monitor_tile = Tile("Monitored power", "dBm")
        tiles.addWidget(self.wavelength_tile, 0, 0)
        tiles.addWidget(self.frequency_tile, 0, 1)
        tiles.addWidget(self.setpoint_tile, 1, 0)
        tiles.addWidget(self.monitor_tile, 1, 1)
        layout.addLayout(tiles)

        interval_row = QHBoxLayout()
        interval_label = QLabel("Update every")
        interval_label.setObjectName("fieldLabel")
        interval_row.addWidget(interval_label)
        self.interval_combo = QComboBox()
        for text, value in POLL_INTERVALS:
            self.interval_combo.addItem(text, value)
        self.interval_combo.setCurrentIndex(1)
        self.interval_combo.setFixedWidth(90)
        self.interval_combo.currentIndexChanged.connect(
            lambda _index: self.request_interval.emit(self.interval_combo.currentData())
        )
        interval_row.addWidget(self.interval_combo)
        interval_row.addStretch()
        self.sweep_count_label = QLabel("")
        self.sweep_count_label.setObjectName("fieldLabel")
        interval_row.addWidget(self.sweep_count_label)
        layout.addLayout(interval_row)
        layout.addStretch()
        return group

    def _build_sweep_group(self) -> QGroupBox:
        group = QGroupBox("WAVELENGTH SWEEP")
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(12)
        layout.setVerticalSpacing(10)

        self.sweep_start_spin = self._add_field(
            layout, 0, 0, "Start (nm)", 1000.0, 2000.0, 4, 0.001
        )
        self.sweep_stop_spin = self._add_field(
            layout, 0, 2, "Stop (nm)", 1000.0, 2000.0, 4, 0.001
        )

        speed_label = QLabel("Speed (nm/s)")
        speed_label.setObjectName("fieldLabel")
        layout.addWidget(speed_label, 0, 4)
        self.sweep_speed_combo = QComboBox()
        for speed in SWEEP_SPEEDS_NM_PER_S:
            self.sweep_speed_combo.addItem(f"{speed:g}", speed)
        layout.addWidget(self.sweep_speed_combo, 0, 5)

        self.sweep_step_spin = self._add_field(
            layout, 1, 0, "Step (nm)", 0.0001, 200.0, 4, 0.001
        )
        self.sweep_dwell_spin = self._add_field(
            layout, 1, 2, "Dwell (s)", 0.0, 999.9, 1, 0.1
        )

        cycles_label = QLabel("Cycles")
        cycles_label.setObjectName("fieldLabel")
        layout.addWidget(cycles_label, 1, 4)
        self.sweep_cycles_spin = QSpinBox()
        self.sweep_cycles_spin.setRange(0, 999)
        layout.addWidget(self.sweep_cycles_spin, 1, 5)

        mode_label = QLabel("Mode")
        mode_label.setObjectName("fieldLabel")
        layout.addWidget(mode_label, 2, 0)
        self.sweep_mode_combo = QComboBox()
        for text, mode in SWEEP_MODE_LABELS:
            self.sweep_mode_combo.addItem(text, mode)
        layout.addWidget(self.sweep_mode_combo, 2, 1)

        self.sweep_range_label = QLabel("")
        self.sweep_range_label.setObjectName("fieldLabel")
        layout.addWidget(self.sweep_range_label, 2, 2, 1, 2)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.sweep_apply_button = QPushButton("Apply settings")
        self.sweep_apply_button.clicked.connect(self.apply_sweep)
        buttons.addWidget(self.sweep_apply_button)
        self.sweep_single_button = QPushButton("Single sweep")
        self.sweep_single_button.setObjectName("primary")
        self.sweep_single_button.clicked.connect(lambda: self.request_sweep_start.emit(False))
        buttons.addWidget(self.sweep_single_button)
        self.sweep_repeat_button = QPushButton("Repeat sweep")
        self.sweep_repeat_button.clicked.connect(lambda: self.request_sweep_start.emit(True))
        buttons.addWidget(self.sweep_repeat_button)
        self.sweep_stop_button = QPushButton("Stop")
        self.sweep_stop_button.setObjectName("danger")
        self.sweep_stop_button.clicked.connect(self.request_sweep_stop)
        buttons.addWidget(self.sweep_stop_button)
        buttons.addStretch()
        layout.addLayout(buttons, 3, 0, 1, 6)
        layout.setColumnStretch(6, 1)
        return group

    def _add_field(
        self, layout, row, column, label_text, low, high, decimals, step
    ) -> QDoubleSpinBox:
        label = QLabel(label_text)
        label.setObjectName("fieldLabel")
        layout.addWidget(label, row, column)
        spin = QDoubleSpinBox()
        spin.setRange(low, high)
        spin.setDecimals(decimals)
        spin.setSingleStep(step)
        spin.setKeyboardTracking(False)
        layout.addWidget(spin, row, column + 1)
        return spin

    def _start_worker(self) -> None:
        self.thread = QThread(self)
        self.worker = LaserWorker()
        self.worker.moveToThread(self.thread)

        self.thread.started.connect(self.worker.start)
        self.request_open.connect(self.worker.open_device)
        self.request_close.connect(self.worker.close_device)
        self.request_wavelength.connect(self.worker.set_wavelength)
        self.request_frequency.connect(self.worker.set_frequency)
        self.request_power.connect(self.worker.set_power)
        self.request_attenuation.connect(self.worker.set_attenuation)
        self.request_power_auto.connect(self.worker.set_power_control_auto)
        self.request_output.connect(self.worker.set_output)
        self.request_shutter.connect(self.worker.set_shutter)
        self.request_coherence.connect(self.worker.set_coherence_control)
        self.request_sweep_apply.connect(self.worker.apply_sweep)
        self.request_sweep_start.connect(self.worker.start_sweep)
        self.request_sweep_stop.connect(self.worker.stop_sweep)
        self.request_interval.connect(self.worker.set_poll_interval)
        self.request_stop.connect(self.worker.stop)

        self.worker.connected.connect(self.on_connected)
        self.worker.disconnected.connect(self.on_disconnected)
        self.worker.telemetry.connect(self.on_telemetry)
        self.worker.sweep_settings.connect(self.on_sweep_settings)
        self.worker.command_failed.connect(self.on_command_failed)
        self.worker.poll_failed.connect(self.on_poll_failed)
        self.worker.busy.connect(self.on_busy)

        log.debug("Starting the worker thread")
        self.thread.start()

    # -- actions -----------------------------------------------------------

    def refresh_resources(self) -> None:
        """Repopulate the dropdown, keeping any hand-typed addresses."""
        previous = self.selected_resource()
        try:
            resources = sorted(pyvisa.ResourceManager().list_resources())
        except (pyvisa.VisaIOError, OSError, ValueError) as exc:
            log.warning("Could not enumerate VISA resources: %s", exc)
            self.status_message.setText(f"VISA scan failed: {exc}")
            resources = []

        self.resource_combo.blockSignals(True)
        self.resource_combo.clear()
        for resource in resources:
            self.resource_combo.addItem(resource, resource)
        for resource in self._manual_resources:
            if resource not in resources:
                self.resource_combo.addItem(resource, resource)
        self.resource_combo.addItem(MANUAL_ENTRY, None)
        index = self.resource_combo.findData(previous) if previous else -1
        self.resource_combo.setCurrentIndex(max(index, 0))
        self.resource_combo.blockSignals(False)

        log.info("VISA scan found %d resource(s)", len(resources))
        if resources:
            self.status_message.setText(f"{len(resources)} VISA resource(s) found")
        else:
            self.status_message.setText(
                f"No VISA resources found - pick '{MANUAL_ENTRY}' to type an address"
            )

    def selected_resource(self) -> str | None:
        """The chosen resource string, or ``None`` on the manual-entry row."""
        return self.resource_combo.currentData()

    def _on_resource_activated(self, index: int) -> None:
        """Prompt for an address when the manual-entry row is picked."""
        if self.resource_combo.itemData(index) is not None:
            return
        text, accepted = QInputDialog.getText(
            self,
            "VISA resource",
            "Address (a LAN socket, or any VISA resource string):",
            text=EXAMPLE_SOCKET,
        )
        resource = text.strip()
        if not accepted or not resource:
            # Fall back to the first real entry rather than leaving the row selected.
            self.resource_combo.setCurrentIndex(0 if self.resource_combo.count() > 1 else -1)
            return
        log.info("Resource %s entered by hand", resource)
        if resource not in self._manual_resources:
            self._manual_resources.append(resource)
        existing = self.resource_combo.findData(resource)
        if existing < 0:
            existing = self.resource_combo.count() - 1  # just above the manual row
            self.resource_combo.insertItem(existing, resource, resource)
        self.resource_combo.setCurrentIndex(existing)

    def toggle_connection(self) -> None:
        if self.connected:
            log.info("Disconnect requested")
            self.request_close.emit()
            return
        resource = self.selected_resource()
        if not resource:
            log.warning("Connect requested with no resource selected")
            QMessageBox.warning(
                self, "No resource", "Choose a VISA resource from the list first."
            )
            return
        log.info("Connect requested for %s", resource)
        self.connection_pill.set_state("Connecting...", "warn")
        self.request_interval.emit(self.interval_combo.currentData())
        self.request_open.emit(resource)

    def toggle_output(self) -> None:
        turning_on = not self.output_on
        if turning_on:
            confirm = QMessageBox.question(
                self,
                "Enable laser output",
                "Enable the TSL-570 optical output?\n\n"
                "Make sure the fibre is connected and everyone is wearing "
                "appropriate eye protection.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if confirm != QMessageBox.StandardButton.Yes:
                log.info("Output enable cancelled at the confirmation dialog")
                return
        log.info("Output %s requested from the panel", "ON" if turning_on else "OFF")
        self.request_output.emit(turning_on)

    def toggle_shutter(self) -> None:
        closing = self.shutter_button.text().startswith("Close")
        log.info("Shutter %s requested", "close" if closing else "open")
        self.request_shutter.emit(closing)

    def apply_sweep(self) -> None:
        settings = SweepSettings(
            start_nm=self.sweep_start_spin.value(),
            stop_nm=self.sweep_stop_spin.value(),
            speed_nm_per_s=self.sweep_speed_combo.currentData(),
            step_nm=self.sweep_step_spin.value(),
            dwell_s=self.sweep_dwell_spin.value(),
            cycles=self.sweep_cycles_spin.value(),
            mode=self.sweep_mode_combo.currentData(),
            range_min_nm=0.0,  # filled in by the instrument on read-back
            range_max_nm=0.0,
        )
        log.info("Applying sweep settings from the panel: %s", settings)
        self.request_sweep_apply.emit(settings)

    # -- worker callbacks --------------------------------------------------

    def on_connected(self, caps: Capabilities) -> None:
        log.info("Panel connected to %s (%s)", caps.resource, caps.identity)
        self.connected = True
        self.connect_button.setText("Disconnect")
        self._set_button_style(self.connect_button, "")
        self.connection_pill.set_state(f"Connected - {caps.resource}", "ok")

        for spin in (self.wavelength_spin, self.sweep_start_spin, self.sweep_stop_spin):
            spin.setRange(caps.min_wavelength_nm, caps.max_wavelength_nm)
        self.wavelength_dial.set_limits(
            caps.min_wavelength_nm, caps.max_wavelength_nm
        )
        self.power_spin.setRange(caps.min_power_dbm, caps.max_power_dbm)
        self.limits_label.setText(
            f"Allowed {caps.min_wavelength_nm:.2f} - {caps.max_wavelength_nm:.2f} nm, "
            f"{caps.min_power_dbm:.2f} - {caps.max_power_dbm:.2f} dBm"
        )
        self.status_message.setText(f"{caps.identity} - firmware {caps.firmware}")
        self._apply_enabled_state()

    def on_disconnected(self) -> None:
        log.info("Panel disconnected")
        self.connected = False
        self.output_on = False
        self.sweeping = False
        self.connect_button.setText("Connect")
        self._set_button_style(self.connect_button, "primary")
        self.connection_pill.set_state("Disconnected", "idle")
        self.output_pill.set_state("Output --", "idle")
        self.interlock_pill.set_state("Interlock --", "idle")
        self.sweep_pill.set_state("Sweep --", "idle")
        self.output_button.setText("Enable Output")
        self._set_button_style(self.output_button, "primary")
        self.shutter_button.setText("Close shutter")
        for tile in (
            self.wavelength_tile,
            self.frequency_tile,
            self.setpoint_tile,
            self.monitor_tile,
        ):
            tile.set_value("--")
        self.limits_label.setText("")
        self.sweep_range_label.setText("")
        self.sweep_count_label.setText("")
        self.updated_label.setText("")
        self.status_message.setText("Disconnected")
        self._apply_enabled_state()

    def on_telemetry(self, data: Telemetry) -> None:
        log.debug("Telemetry received: %s", data)
        self.wavelength_tile.set_value(_fmt(data.wavelength_nm, 4))
        self.frequency_tile.set_value(_fmt(data.frequency_thz, 5))
        self.setpoint_tile.set_value(_fmt(data.power_dbm))
        self.monitor_tile.set_value(_fmt(data.actual_power_dbm))

        # Mirror the instrument's own state into the editors the user isn't in.
        self._sync_spin(self.wavelength_spin, data.wavelength_nm)
        self.wavelength_dial.sync_value(data.wavelength_nm)
        self._sync_spin(self.frequency_spin, data.frequency_thz)
        self._sync_spin(self.power_spin, data.power_dbm)
        self._sync_spin(self.attenuation_spin, data.attenuation_db)
        self._sync_check(self.auto_power_check, data.power_control_auto)
        self._sync_check(self.coherence_check, data.coherence_control)
        self.attenuation_spin.setEnabled(
            self.connected and not self.busy and data.power_control_auto is False
        )

        self.output_on = bool(data.output)
        self.output_button.setText("Disable Output" if self.output_on else "Enable Output")
        self._set_button_style(self.output_button, "danger" if self.output_on else "primary")
        if data.output is None:
            self.output_pill.set_state("Output --", "idle")
        elif self.output_on:
            self.output_pill.set_state("Output ON", "ok")
        else:
            self.output_pill.set_state("Output OFF", "idle")

        if data.shutter_closed is not None:
            self.shutter_button.setText(
                "Open shutter" if data.shutter_closed else "Close shutter"
            )

        if data.interlock is None:
            self.interlock_pill.set_state("Interlock --", "idle")
        elif data.interlock:
            self.interlock_pill.set_state("Interlock TRIPPED", "error")
        else:
            self.interlock_pill.set_state("Interlock clear", "ok")

        if data.sweep_state is None:
            self.sweep_pill.set_state("Sweep --", "idle")
            self.sweeping = False
        else:
            text, state = SWEEP_STATE_TEXT[data.sweep_state]
            self.sweep_pill.set_state(text, state)
            self.sweeping = data.sweep_state != SweepState.STOPPED
        if data.sweep_count is not None:
            self.sweep_count_label.setText(f"{data.sweep_count} sweep(s) done")

        self.updated_label.setText(f"Updated {datetime.now():%H:%M:%S}")

    def on_sweep_settings(self, settings: SweepSettings) -> None:
        log.debug("Sweep settings received: %s", settings)
        for spin in (self.sweep_start_spin, self.sweep_stop_spin):
            spin.setRange(settings.range_min_nm, settings.range_max_nm)
        self._sync_spin(self.sweep_start_spin, settings.start_nm)
        self._sync_spin(self.sweep_stop_spin, settings.stop_nm)
        self._sync_spin(self.sweep_step_spin, settings.step_nm)
        self._sync_spin(self.sweep_dwell_spin, settings.dwell_s)
        self.sweep_cycles_spin.blockSignals(True)
        self.sweep_cycles_spin.setValue(settings.cycles)
        self.sweep_cycles_spin.blockSignals(False)
        self._sync_combo(self.sweep_speed_combo, settings.speed_nm_per_s)
        self._sync_combo(self.sweep_mode_combo, settings.mode)
        self.sweep_range_label.setText(
            f"Configurable at {settings.speed_nm_per_s:g} nm/s: "
            f"{settings.range_min_nm:.3f} - {settings.range_max_nm:.3f} nm"
        )

    def on_command_failed(self, title: str, message: str) -> None:
        log.error("%s: %s", title, message)
        if not self.connected:
            self.connection_pill.set_state("Disconnected", "idle")
        self.status_message.setText(f"{title}: {message}")
        QMessageBox.critical(self, title, message)

    def on_poll_failed(self, message: str) -> None:
        log.warning("Read warning: %s", message)
        self.status_message.setText(f"Read warning: {message}")

    def on_busy(self, busy: bool) -> None:
        log.debug("Busy: %s", busy)
        self.busy = busy
        self._apply_enabled_state()

    # -- helpers -----------------------------------------------------------

    def _apply_enabled_state(self) -> None:
        live = self.connected and not self.busy
        for widget in (
            *self._apply_widgets,
            self.auto_power_check,
            self.coherence_check,
            self.output_button,
            self.shutter_button,
            self.sweep_start_spin,
            self.sweep_stop_spin,
            self.sweep_speed_combo,
            self.sweep_step_spin,
            self.sweep_dwell_spin,
            self.sweep_cycles_spin,
            self.sweep_mode_combo,
            self.sweep_apply_button,
            self.sweep_single_button,
            self.sweep_repeat_button,
            self.sweep_stop_button,
        ):
            widget.setEnabled(live)
        self.wavelength_dial.set_live(live)
        self.connect_button.setEnabled(not self.busy)
        self.resource_combo.setEnabled(not self.connected and not self.busy)
        self.rescan_button.setEnabled(not self.connected and not self.busy)

    @staticmethod
    def _sync_spin(spin, value: float | None) -> None:
        """Update a spinbox from telemetry without stealing an in-progress edit."""
        if value is None or spin.hasFocus():
            return
        spin.blockSignals(True)
        spin.setValue(value)
        spin.blockSignals(False)

    @staticmethod
    def _sync_check(check, value: bool | None) -> None:
        if value is None:
            return
        check.blockSignals(True)
        check.setChecked(value)
        check.blockSignals(False)

    @staticmethod
    def _sync_combo(combo, value) -> None:
        index = combo.findData(value)
        if index < 0:
            return
        combo.blockSignals(True)
        combo.setCurrentIndex(index)
        combo.blockSignals(False)

    def _set_button_style(self, button: QPushButton, name: str) -> None:
        """Swap a button between the default/primary/danger looks."""
        if button.objectName() == name:
            return
        button.setObjectName(name)
        button.style().unpolish(button)
        button.style().polish(button)

    def closeEvent(self, event) -> None:
        log.info("Closing the control panel")
        self.request_stop.emit()
        self.thread.quit()
        self.thread.wait(3000)
        event.accept()


def _fmt(value: float | None, decimals: int = 2) -> str:
    return "--" if value is None else f"{value:.{decimals}f}"


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("TSL_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    app = QApplication(sys.argv)
    # Fusion instead of the platform style: the panel is fully custom-styled,
    # and native styles draw their own themed combo buttons and check indicators
    # straight through the sheet. Fusion composes with it, on any OS.
    app.setStyle("Fusion")
    app.setApplicationName("santec TSL-570 Control")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
