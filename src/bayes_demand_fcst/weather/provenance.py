"""Compact, stable provenance codes for hourly weather outputs."""

from enum import IntEnum


class WeatherProvenance(IntEnum):
    """Persisted ``uint8`` source/derivation codes for hourly weather values."""

    EXACT_ON_HOUR = 0
    ALGEBRAIC_PREFILL = 1
    LOCAL_TEMPORAL = 2
    SPATIOTEMPORAL_KRIG = 3
