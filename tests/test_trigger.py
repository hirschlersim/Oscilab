"""
The trigger's logic: finding events (edge, hysteresis, window; coupling,
holdoff), and acquiring records (Run, Single, Stop, Auto, Force).
"""

import numpy as np
import pytest

from oscilab.trigger import TIME_PER_DIV, Acquisition, TriggerDetector, TriggerSettings


def events(settings: TriggerSettings, x, t=None, chunk: int | None = None) -> list[float]:
    """The events a detector finds in `x` (one sample a second unless `t`), fed whole or `chunk` at a time."""
    x = np.asarray(x, dtype=float)
    t = np.arange(len(x), dtype=float) if t is None else np.asarray(t, dtype=float)
    detector = TriggerDetector(settings)
    step = chunk or len(x)
    found = []
    for start in range(0, len(x), step):
        found += detector.feed(t[start : start + step], x[start : start + step])
    return found


# --- finding events ------------------------------------------------------------------------------

RAMP = [0, 1, 2, 1, 0, 1, 2]  # Crosses 1.5 up at t 1.5 and 5.5, down at 2.5


@pytest.mark.parametrize(
    ("slope", "expected"), [("rising", [1.5, 5.5]), ("falling", [2.5]), ("either", [1.5, 2.5, 5.5])]
)
def test_edges_are_found_on_their_slope(slope, expected):
    assert events(TriggerSettings(level=1.5, slope=slope), RAMP) == pytest.approx(expected)


def test_the_crossing_time_is_interpolated_between_samples():
    # From 0 to 4 in one second: 1 is crossed a quarter of the way
    assert events(TriggerSettings(level=1.0), [0, 4]) == pytest.approx([0.25])


def test_chunks_find_the_same_events_as_one_feed():
    x = np.sin(np.linspace(0, 20, 400))
    settings = TriggerSettings(level=0.3, slope="either")
    assert events(settings, x, chunk=7) == pytest.approx(events(settings, x))


def test_holdoff_skips_events_too_soon_after_the_last():
    assert events(TriggerSettings(level=1.5, holdoff=5.0), RAMP) == pytest.approx([1.5])
    assert events(TriggerSettings(level=1.5, holdoff=4.0), RAMP) == pytest.approx([1.5, 5.5])


def test_hysteresis_ignores_noise_around_the_level():
    rng = np.random.default_rng(1)
    square = np.repeat([0.0, 1.0, 0.0, 1.0], 50)  # Two real rising edges
    noisy = square + rng.normal(0, 0.05, square.size)
    noisy[40:60] = 0.5 + rng.normal(0, 0.05, 20)  # ...and a noisy stretch hovering at the level

    assert len(events(TriggerSettings(level=0.5), noisy)) > 2  # A plain edge fires on the noise too
    found = events(TriggerSettings(type="hysteresis", level=0.5, hysteresis=0.3), noisy)
    assert len(found) == 2


def test_hysteresis_on_a_falling_slope():
    assert events(
        TriggerSettings(type="hysteresis", slope="falling", level=1.0, hysteresis=0.5), [0, 2, 0, 1.2, 0.9, 2, 0]
    ) == pytest.approx([1.5, 5.5])


@pytest.mark.parametrize(("condition", "expected"), [("entering", [0.5, 4.5]), ("leaving", [2.5])])
def test_window_triggers_on_entering_or_leaving(condition, expected):
    x = [-2, 0, 0, 2, 2, 0]  # In between -1 and 1 at samples 1-2 and 5
    settings = TriggerSettings(type="window", low=-1.0, high=1.0, condition=condition)
    assert events(settings, x) == pytest.approx(expected)


def test_ac_coupling_takes_the_average_off():
    t = np.arange(0, 20, 0.001)
    x = 10 + np.sin(2 * np.pi * 10 * t)  # A 10 Hz sine sitting at 10
    assert events(TriggerSettings(level=0.0), x, t) == []  # DC: never gets down to 0
    found = np.array(events(TriggerSettings(level=0.0, coupling="ac"), x, t))
    late = found[found > 10]  # Once the average has settled: at its rising zero crossings
    # (A running average is a high-pass filter, as AC coupling is: it leads the phase a little)
    np.testing.assert_allclose(late, np.round(late * 10) / 10, atol=0.001)
    assert len(late) == pytest.approx(100, abs=1)


def test_samples_that_arent_numbers_are_skipped():
    assert events(TriggerSettings(level=1.5), [0, 1, np.nan, 2]) == pytest.approx([2.0])  # From 1 at t 1 to 2 at t 3


# --- the record -------------------------------------------------------------------------------------


def test_the_record_sits_at_its_reference_position():
    settings = TriggerSettings(time_per_div=0.01, reference=0.2)
    assert settings.span == pytest.approx(0.1)
    assert settings.window(5.0) == pytest.approx((4.98, 5.08))  # 20% before the trigger
    settings.delay = 0.05
    assert settings.window(5.0) == pytest.approx((5.03, 5.13))  # Around 50 ms after it


def test_time_per_div_steps_1_2_5():
    assert TIME_PER_DIV[:4] == pytest.approx((1e-5, 2e-5, 5e-5, 1e-4))
    assert TIME_PER_DIV[-1] == 1.0


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"time_per_div": 0}, "time_per_div"),
        ({"reference": 1.5}, "reference"),
        ({"holdoff": -1}, "holdoff"),
        ({"type": "window", "low": 1, "high": 0}, "low must be below"),
    ],
)
def test_impossible_settings_are_refused(changes, message):
    with pytest.raises(ValueError, match=message):
        Acquisition(TriggerSettings(**changes))


# --- acquiring --------------------------------------------------------------------------------------


class Source:
    """A buffer that grows, fed to an acquisition a tick at a time: a 1 Hz sine at 100 Hz."""

    def __init__(self):
        self.t = np.array([])

    def grow(self, seconds: float) -> tuple[np.ndarray, np.ndarray]:
        start = self.t.size
        self.t = np.arange(start + round(seconds * 100)) / 100
        return self.t, np.sin(2 * np.pi * self.t)


def settings(**changes) -> TriggerSettings:
    base = {"level": 0.0, "time_per_div": 0.01, "reference": 0.5, "auto": False}  # Records of 100 ms
    return TriggerSettings(**(base | changes))


def test_run_shows_each_record_once_it_is_complete():
    acquisition = Acquisition(settings())
    acquisition.run()
    source = Source()
    assert acquisition.tick(*source.grow(0.5)) is None  # Armed at 0.5 s: the rising crossing at 1 s is to come
    assert acquisition.state == "armed"
    assert acquisition.tick(*source.grow(0.52)) is None  # Triggered at 1.0 s, but the record runs to 1.05 s
    assert acquisition.state == "waiting"
    assert acquisition.tick(*source.grow(0.1)) == pytest.approx(1.0, abs=1e-3)
    assert (acquisition.state, acquisition.last) == ("armed", "trigger")


def test_the_newest_complete_record_is_shown():
    acquisition = Acquisition(settings())
    acquisition.run()
    source = Source()
    acquisition.tick(*source.grow(0.5))
    assert acquisition.tick(*source.grow(3.0)) == pytest.approx(3.0, abs=1e-3)  # Not 1 or 2: they're old news


def test_single_takes_one_record_then_stops():
    acquisition = Acquisition(settings())
    acquisition.single_shot()
    source = Source()
    acquisition.tick(*source.grow(0.5))
    assert acquisition.tick(*source.grow(1.0)) == pytest.approx(1.0, abs=1e-3)
    assert acquisition.state == "stopped"
    assert acquisition.tick(*source.grow(2.0)) is None


def test_stop_holds_and_ignores_what_happened_meanwhile():
    acquisition = Acquisition(settings())
    acquisition.run()
    source = Source()
    acquisition.tick(*source.grow(0.5))
    acquisition.stop()
    assert acquisition.tick(*source.grow(1.0)) is None  # The crossing at 1 s passes while stopped
    acquisition.run()
    assert acquisition.tick(*source.grow(0.01)) is None  # ...and doesn't count once it runs again
    assert acquisition.tick(*source.grow(1.0)) == pytest.approx(2.0, abs=1e-3)


def test_auto_takes_a_record_when_no_trigger_comes():
    acquisition = Acquisition(settings(level=5.0, auto=True))  # The sine never reaches 5
    acquisition.run()
    source = Source()
    acquisition.tick(*source.grow(0.1))
    assert acquisition.tick(*source.grow(0.1)) is None  # Not yet: Auto waits a quarter of a second
    event = acquisition.tick(*source.grow(0.2))
    assert event == pytest.approx(source.t[-1] - 0.05)  # A record ending at the newest sample
    assert acquisition.last == "auto"


def test_normal_waits_for_a_trigger_however_long():
    acquisition = Acquisition(settings(level=5.0, auto=False))
    acquisition.run()
    source = Source()
    for _ in range(10):
        assert acquisition.tick(*source.grow(1.0)) is None


def test_force_takes_a_record_right_away():
    acquisition = Acquisition(settings(level=5.0))
    source = Source()
    acquisition.force()  # Even while stopped
    assert acquisition.tick(*source.grow(1.0)) == pytest.approx(source.t[-1] - 0.05)
    assert (acquisition.last, acquisition.state) == ("forced", "stopped")


def test_immediate_takes_a_record_every_time():
    acquisition = Acquisition(settings(type="immediate"))
    acquisition.run()
    source = Source()
    for _ in range(3):
        assert acquisition.tick(*source.grow(0.1)) == pytest.approx(source.t[-1] - 0.05)
    assert acquisition.last == "immediate"


def test_an_event_too_early_for_the_buffer_is_passed_over():
    acquisition = Acquisition(settings(time_per_div=0.1, reference=0.9))  # 0.9 s before the trigger needed
    acquisition.run()
    t = np.arange(50, 200) / 100  # The buffer starts at 0.5 s
    acquisition.tick(t[:10], np.sin(2 * np.pi * t[:10]))
    assert acquisition.tick(t, np.sin(2 * np.pi * t)) is None  # The crossing at 1 s has no 0.9 s before it
