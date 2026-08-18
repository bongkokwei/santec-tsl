"""Python driver for the santec TSL-570 tunable semiconductor laser."""

import logging

from .driver import (
    SWEEP_SPEEDS_NM_PER_S,
    ModulationSource,
    SweepLog,
    SweepMode,
    SweepState,
    TriggerOutput,
    TSL570,
    WavelengthRange,
    nm_to_thz,
    thz_to_nm,
)
from .exceptions import (
    TSLCommandError,
    TSLConnectionError,
    TSLError,
    TSLProtocolError,
    TSLValueError,
)

# Library code only emits records; the application decides where they go.
logging.getLogger(__name__).addHandler(logging.NullHandler())

__all__ = [
    "TSL570",
    "SweepMode",
    "SweepState",
    "SweepLog",
    "TriggerOutput",
    "ModulationSource",
    "WavelengthRange",
    "SWEEP_SPEEDS_NM_PER_S",
    "nm_to_thz",
    "thz_to_nm",
    "TSLError",
    "TSLConnectionError",
    "TSLCommandError",
    "TSLProtocolError",
    "TSLValueError",
]
