"""Exception hierarchy for the santec TSL driver."""


class TSLError(Exception):
    """Base exception for all santec TSL driver errors."""


class TSLConnectionError(TSLError):
    """Raised when the VISA resource cannot be opened or is lost mid-session."""


class TSLCommandError(TSLError):
    """Raised when the instrument reports a non-zero code via ``:SYSTem:ERRor?``."""


class TSLProtocolError(TSLError):
    """Raised when the instrument replies, but the reply cannot be parsed."""


class TSLValueError(TSLError, ValueError):
    """Raised when a requested setting falls outside the instrument's range."""
