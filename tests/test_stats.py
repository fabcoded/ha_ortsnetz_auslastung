"""Tests for the interval statistics, without Home Assistant."""

from custom_components.ortsnetz_auslastung.stats import IntervalStats


def test_value_counts_by_its_duration():
    stats = IntervalStats(0, 230.0)
    stats.report(240.0, 240)  # 230 V for four minutes, then 240 V for one minute
    assert stats.take(300) == (230.0, 240.0, 232.0)


def test_short_peak_is_kept():
    stats = IntervalStats(0, 230.0)
    stats.report(252.4, 100)
    stats.report(231.0, 101)
    minimum, maximum, mean = stats.take(300)
    assert (minimum, maximum) == (230.0, 252.4)
    assert minimum <= mean <= maximum


def test_unchanged_value_carries_over_into_the_next_interval():
    stats = IntervalStats(0, 230.0)
    stats.report(235.0, 200)
    stats.take(300)
    assert stats.take(600) == (235.0, 235.0, 235.0)


def test_gap_counts_neither_for_mean_nor_extremes():
    stats = IntervalStats(0, 230.0)
    stats.report(None, 60)
    stats.report(236.0, 120)  # 230 V for 60 s, gap of 60 s, 236 V for 180 s
    assert stats.take(300) == (230.0, 236.0, 234.5)


def test_nothing_without_enough_usable_time():
    assert IntervalStats(0, None).take(300) is None
    assert IntervalStats(0, 230.0).take(0) is None
    assert IntervalStats(0, 230.0).take(59) is None  # e.g. the send right after setup
    stats = IntervalStats(0, 230.0)
    stats.report(None, 140)  # usable for 140 of 300 s
    assert stats.take(300) is None


def test_rounded_mean_stays_between_min_and_max():
    for value in (230.0005, 229.9995, 49.9995, 50.0005):
        stats = IntervalStats(0, value)
        for second in range(1, 300, 7):
            stats.report(value, second)
        minimum, maximum, mean = stats.take(300)
        assert minimum <= mean <= maximum
