"""Minimum, maximum and time-weighted mean of one sensor over one send interval."""

from __future__ import annotations

# Statistics over a few seconds (the send right after setup) or mostly gaps would pose as a five-minute summary.
MIN_INTERVAL_S = 60.0
MIN_COVERAGE = 0.5


class IntervalStats:
    """Collect the values a sensor reported during one send interval.

    Home Assistant reports changes only, so a value counts from the moment it was reported until the next report.
    A value that stays for four minutes therefore weighs four minutes in the mean. Unusable values (None) open a
    gap that counts neither for the mean nor for minimum and maximum.
    """

    def __init__(self, at: float, value: float | None = None) -> None:
        self._start(at, value)

    def _start(self, at: float, value: float | None) -> None:
        """Begin an interval at `at` with the value that holds from then on."""
        self._value: float | None = None
        self._started = self._since = at
        self._minimum: float | None = None
        self._maximum: float | None = None
        self._weighted = 0.0
        self._covered = 0.0
        self.report(value, at)

    def report(self, value: float | None, at: float) -> None:
        """Take a new value (None: unusable) reported at timestamp `at`."""
        self._close(at)
        self._value, self._since = value, max(at, self._since)
        if value is not None:
            self._minimum = value if self._minimum is None else min(self._minimum, value)
            self._maximum = value if self._maximum is None else max(self._maximum, value)

    def take(self, at: float) -> tuple[float, float, float] | None:
        """Minimum, maximum and mean up to `at`, or None if the interval was too short or mostly gaps; then start
        the next interval with the current value."""
        self._close(at)
        result = None
        length = at - self._started
        usable = length >= MIN_INTERVAL_S and self._covered >= MIN_COVERAGE * length
        if usable and self._minimum is not None and self._maximum is not None:
            minimum, maximum = round(self._minimum, 3), round(self._maximum, 3)
            # Rounding must not break min <= avg <= max, which the API checks.
            mean = min(max(round(self._weighted / self._covered, 3), minimum), maximum)
            result = (minimum, maximum, mean)
        self._start(at, self._value)
        return result

    def _close(self, at: float) -> None:
        """Add the current value for the time it was held."""
        if self._value is not None and at > self._since:
            self._weighted += self._value * (at - self._since)
            self._covered += at - self._since
            self._since = at
