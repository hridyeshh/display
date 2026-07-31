#!/usr/bin/env python3
"""Self-check for main.py's alarm state machine. Runs anywhere:

    python3 test_scheduled.py

Every other module here keeps its checks inline behind --selftest, and this one
would too if it could. It cannot: main.py imports spidev and lgpio at module
scope, neither of which exists off the Pi, so the two are stubbed into
sys.modules before the import and the real logic runs underneath.

What is worth checking is the deciding, not the drawing. The Pi fires alarms
from its own clock, which makes _alarm_due the one place where a wrong answer
means either an alarm that never goes off or one that goes off at the wrong
time — and neither is discovered until someone oversleeps.
"""

import sys
import time
import types
import tempfile
from pathlib import Path

# Stub the hardware before importing main. Only the names main.py touches at
# import time are needed; nothing here is called, because nothing under test
# draws anything.
for name in ("spidev", "lgpio"):
    if name not in sys.modules:
        sys.modules[name] = types.ModuleType(name)
sys.modules["spidev"].SpiDev = object
sys.path.insert(0, str(Path(__file__).resolve().parent))

import main  # noqa: E402


def alarm(id=1, hour=7, minute=0, next_at=0, enabled=True, ringing=False,
          label="WAKE UP", kind="alarm"):
    return {"id": id, "kind": kind, "hour": hour, "minute": minute, "days": 0,
            "label": label, "enabled": enabled, "next_at": next_at, "ringing": ringing}


def reminder(id=2, next_at=0, label="do laundry", **kw):
    return alarm(id=id, next_at=next_at, label=label, kind="reminder", **kw)


def reset(alarms=(), ringing_id=None, since=0.0):
    main.CONFIG["scheduled"] = list(alarms)
    main._ALARM_FIRED.clear()
    main.ALARM.update({"id": ringing_id, "time": "", "label": "", "since": since})
    main.REMINDER.update({"id": None, "text": "", "since": 0.0})


def test_due():
    now = time.time()

    reset([alarm(next_at=now + 60)])
    assert main._alarm_due(now) is None, "fired before it was due"

    reset([alarm(next_at=now)])
    assert main._alarm_due(now)["id"] == 1, "did not fire at the due second"

    # A Pi that boots a couple of minutes late should still wake you.
    reset([alarm(next_at=now - main.ALARM_GRACE + 1)])
    assert main._alarm_due(now) is not None, "refused to fire inside the grace window"

    # One that boots hours late should not. This is the boundary, so pin it
    # exactly rather than somewhere comfortably past it.
    reset([alarm(next_at=now - main.ALARM_GRACE)])
    assert main._alarm_due(now) is not None, "grace window is exclusive at the edge"
    reset([alarm(next_at=now - main.ALARM_GRACE - 1)])
    assert main._alarm_due(now) is None, "rang an alarm past the grace window"

    reset([alarm(next_at=now, enabled=False)])
    assert main._alarm_due(now) is None, "rang a disabled alarm"

    # Malformed rows must be skipped, not crash the loop that reads them: this
    # list arrives over the network.
    for bad in (None, "soon", float("nan")):
        reset([{"id": 9, "enabled": True, "next_at": bad}])
        try:
            main._alarm_due(now)
        except Exception as e:                      # noqa: BLE001
            raise AssertionError(f"next_at={bad!r} raised {e!r}") from e
    reset([alarm(next_at=0)])
    assert main._alarm_due(now) is None, "next_at 0 is 'unset', not 1970"


def test_fired_key_lets_a_repeating_alarm_ring_again():
    """The bug this keying exists to prevent.

    Keyed on id alone, a daily alarm rings once and then never again for the
    lifetime of the process — the server advances it to tomorrow, the id is
    already in the set, and tomorrow's ring is swallowed.
    """
    now = time.time()
    reset([alarm(next_at=now)])

    due = main._alarm_due(now)
    assert due is not None
    main._ALARM_FIRED.add((due["id"], int(due["next_at"])))
    assert main._alarm_due(now) is None, "same ring fired twice"

    # The server advances the alarm; same id, new instant.
    tomorrow = now + 86400
    main.CONFIG["scheduled"] = [alarm(next_at=tomorrow)]
    assert main._alarm_due(tomorrow) is not None, "re-armed alarm was suppressed by its own id"


def test_still_ringing():
    """How a dismiss from the phone gets back to the Pi."""
    reset([alarm(ringing=True)])
    assert main._alarm_still_ringing(1) is True

    reset([alarm(ringing=False)])
    assert main._alarm_still_ringing(1) is False, "kept ringing after the app dismissed it"

    # Deleted mid-ring reads the same as dismissed, which is what we want: an
    # alarm that no longer exists is not ringing.
    reset([])
    assert main._alarm_still_ringing(1) is False


def test_tick_stops_ringing():
    tmp = Path(tempfile.mkdtemp())
    main.ALARM_FILE = str(tmp / "desky_alarm")
    main.ALARM_CMD_FILE = str(tmp / "desky_alarm_cmd")

    posted = []
    main._alarm_post = posted.append          # no network in a self-check

    def ring():
        reset([alarm(ringing=True)], ringing_id=1, since=time.time())
        Path(main.ALARM_FILE).write_text("1 WAKE UP")
        posted.clear()

    def settle():
        # The POSTs go out on daemon threads; give them a moment to land.
        time.sleep(0.05)

    # Dismissed at the desk.
    ring()
    Path(main.ALARM_CMD_FILE).write_text("dismiss")
    main._alarm_tick()
    settle()
    assert main.ALARM["id"] is None, "encoder dismiss did not stop the ring"
    assert not Path(main.ALARM_FILE).exists(), "flag left behind, wake_word.py would keep ringing"
    assert posted == ["/scheduled/1/dismiss"], posted

    # Snoozed at the desk.
    ring()
    Path(main.ALARM_CMD_FILE).write_text("snooze")
    main._alarm_tick()
    settle()
    assert main.ALARM["id"] is None
    assert posted == ["/scheduled/1/snooze"], posted

    # A command we do not recognise is ignored rather than obeyed.
    ring()
    Path(main.ALARM_CMD_FILE).write_text("explode")
    main._alarm_tick()
    assert main.ALARM["id"] == 1, "acted on an unknown command"

    # Nobody came: the cap ends it. Until the desk encoder runs this is the path
    # that actually stops most rings, so it matters more than it looks.
    ring()
    main.ALARM["since"] = time.time() - main.ALARM_RING_CAP - 1
    main._alarm_tick()
    settle()
    assert main.ALARM["id"] is None, "rang past the cap"
    assert posted == ["/scheduled/1/dismiss"], posted

    # Dismissed from the phone: the server already knows, so nothing is posted.
    ring()
    main.CONFIG["scheduled"] = [alarm(ringing=False)]
    main._alarm_tick()
    settle()
    assert main.ALARM["id"] is None, "did not stop when the app dismissed it"
    assert posted == [], f"posted after an app dismiss: {posted}"


def test_tick_ignores_an_unset_clock():
    """A Pi that boots before NTP syncs reads 1970 and must not fire anything."""
    real_localtime = time.localtime
    time.localtime = lambda *a: real_localtime(0)
    try:
        reset([alarm(next_at=time.time())])
        main._alarm_tick()
        assert main.ALARM["id"] is None, "fired against an unsynced clock"
    finally:
        time.localtime = real_localtime


def test_reminder_fires_and_clears_itself():
    """A reminder is the same row taking the other branch.

    Nothing waits on it: no flag anyone polls for a dismiss, no cap, no ring. It
    shows, it is spoken elsewhere, and it goes away on its own clock.
    """
    tmp = Path(tempfile.mkdtemp())
    main.ALARM_FILE = str(tmp / "desky_alarm")
    main.ALARM_CMD_FILE = str(tmp / "desky_alarm_cmd")
    main.REMINDER_FILE = str(tmp / "desky_reminder")

    posted = []
    main._alarm_post = posted.append
    now = time.time()

    reset([reminder(next_at=now)])
    main._alarm_tick()
    time.sleep(0.05)

    assert main.REMINDER["id"] == 2, "reminder did not fire"
    assert main.REMINDER["text"] == "do laundry"
    assert main.ALARM["id"] is None, "a reminder must never start the ring path"
    assert Path(main.REMINDER_FILE).read_text() == "do laundry", "text not handed to wake_word"
    assert not Path(main.ALARM_FILE).exists(), "a reminder raised the alarm flag"
    assert posted == ["/scheduled/2/fired"], posted

    # Still inside its window: it stays put.
    main._alarm_tick()
    assert main.REMINDER["id"] == 2, "cleared before its time"

    # Past the window: gone, and the flag with it.
    main.REMINDER["since"] = time.time() - main.REMINDER_SHOW_SEC - 1
    main._alarm_tick()
    assert main.REMINDER["id"] is None, "reminder never cleared itself"
    assert not Path(main.REMINDER_FILE).exists(), "flag left behind after clearing"

    # And it must not fire a second time. days = 0 means once.
    reset([reminder(next_at=now)])
    main._ALARM_FIRED.add((2, int(now)))
    main._alarm_tick()
    assert main.REMINDER["id"] is None, "one-shot reminder fired twice"


def test_reminder_marked_fired_even_if_the_flag_cannot_be_written():
    """A full or read-only /dev/shm must not turn one reminder into a loop.

    _ALARM_FIRED is set before either branch runs, so a write that fails still
    counts as fired. Without that the same row comes back due on the next tick,
    a second later, forever.
    """
    reset([reminder(next_at=time.time())])
    main._alarm_post = lambda *_a, **_k: None
    main.REMINDER_FILE = "/nonexistent-dir/desky_reminder"
    try:
        main._alarm_tick()
    except Exception as e:                          # noqa: BLE001
        raise AssertionError(f"unwritable flag raised {e!r}") from e
    assert main._ALARM_FIRED, "fired set not marked, so this would fire again next tick"


def test_alarm_outranks_a_reminder():
    """A ringing alarm holds the floor; nothing fires underneath it."""
    tmp = Path(tempfile.mkdtemp())
    main.ALARM_FILE = str(tmp / "desky_alarm")
    main.ALARM_CMD_FILE = str(tmp / "desky_alarm_cmd")
    main.REMINDER_FILE = str(tmp / "desky_reminder")
    main._alarm_post = lambda *_a, **_k: None

    now = time.time()
    reset([alarm(ringing=True), reminder(next_at=now)], ringing_id=1, since=now)
    Path(main.ALARM_FILE).write_text("1 WAKE UP")

    main._alarm_tick()
    assert main.REMINDER["id"] is None, "a reminder fired while an alarm was ringing"
    assert main.ALARM["id"] == 1, "the alarm stopped for a reminder"


if __name__ == "__main__":
    test_due()
    test_fired_key_lets_a_repeating_alarm_ring_again()
    test_still_ringing()
    test_tick_stops_ringing()
    test_tick_ignores_an_unset_clock()
    test_reminder_fires_and_clears_itself()
    test_reminder_marked_fired_even_if_the_flag_cannot_be_written()
    test_alarm_outranks_a_reminder()
    print("scheduled selftest ok")
