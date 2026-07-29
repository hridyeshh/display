#!/usr/bin/env python3
"""Send a command to the WiZ bulb on the local network.

WiZ bulbs take JSON over UDP on port 38899, addressed to the bulb directly, so
this needs no cloud account and no vendor library — the entire protocol used
here is one setPilot call and the reply it comes back with.

This lives on the Pi because it has to. The backend holds every secret and
drives every other part of the exchange, but Railway is not on this network and
cannot reach the bulb at all. So the server decides *what* should happen and
says so in its /voice/audio reply; this module is the only thing that can
actually do it.

The params in that reply are forwarded to the bulb untouched. That is safe only
because every key in them was written by the backend and none was copied out of
Claude's tool input — ranges, the scene table and the colour-mode exclusivity
are all settled there, on the side of the wire that can still refuse. What this
module checks is shape, not policy: that the envelope is ours and that no value
is of a type the bulb could not possibly mean.

Runnable on its own, which is also how to test the bulb before the voice loop is
anywhere near it:

    DESKY_BULB_IP=192.168.0.5 python3 light.py on
    DESKY_BULB_IP=192.168.0.5 python3 light.py on 40
    DESKY_BULB_IP=192.168.0.5 python3 light.py off
    DESKY_BULB_IP=192.168.0.5 python3 light.py scene 6
    DESKY_BULB_IP=192.168.0.5 python3 light.py rgb 255 0 128
    DESKY_BULB_IP=192.168.0.5 python3 light.py temp 2700
    python3 light.py --selftest

Scenes are numbers here on purpose: the name-to-id table belongs to the backend,
which is what Claude talks to, and a second copy on the Pi would be a second
thing to keep in step. For bench testing "does scene 6 work", the number is what
you wanted anyway.
"""

import json
import os
import socket
import sys

# WiZ's local control port, fixed by the firmware.
WIZ_PORT = 38899

# The bulb's address, reserved in the router's DHCP table so it survives a
# reboot. Out of code for the same reason as DESKY_BACKEND and the ALSA device
# names in wake_word.py: it belongs to this network, not to this program. Set it
# in the systemd unit on the Pi, the way CALENDAR_ICS_URL is.
#
# Empty means no bulb is configured, which is a clean skip rather than a crash —
# a Pi with no bulb on its network still answers questions.
BULB_IP = os.environ.get("DESKY_BULB_IP", "")

# How long to wait for the bulb to confirm.
#
# This timeout is the only reason we know anything at all: UDP sendto() reports
# success once the packet leaves, so a bulb that is unplugged, asleep, on
# another subnet, or simply at a different address than we think all look
# exactly like a bulb that obeyed. The reply is the evidence. 2s is generous for
# a LAN round trip (single-digit ms in practice) and short enough that the
# spoken confirmation does not lag noticeably behind the question.
REPLY_TIMEOUT_SEC = 2.0

# The setPilot arguments this end will forward, all integers. Anything outside
# this set is refused rather than passed on: the backend builds params from a
# fixed schema, so a key that is not here did not come from the path we think it
# did, and forwarding an unknown argument to a light is not a thing to do on
# faith.
#
# Ranges are deliberately absent. The backend clamps and owns the scene table;
# repeating its bounds here would give two places to disagree about what is
# valid, and the one that is wrong would be this one.
INT_PARAMS = ("dimming", "sceneId", "r", "g", "b", "temp")

# What Desky says when it cannot reach the bulb at all. Distinct from a bulb that
# answered and refused: one is a network or power problem, the other is a bad
# command, and they want different reactions from whoever is listening.
SPOKEN_UNREACHABLE = "I can't reach the light."


def set_pilot(params, ip=None, timeout=REPLY_TIMEOUT_SEC):
    """Send one setPilot and wait for the bulb to confirm it.

    Returns (ok, reason, spoken): a log line and a sentence to say out loud, the
    second because only this function knows *which* failure happened. Never
    raises — this runs on the voice loop's worker thread, where an exception
    would take down the thread that also plays the spoken reply, so a light that
    failed to dim would cost you the answer too.
    """
    ip = ip or BULB_IP
    if not ip:
        return False, "DESKY_BULB_IP is not set", "The light isn't set up yet."

    payload = json.dumps({"method": "setPilot", "params": params}).encode()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(payload, (ip, WIZ_PORT))
        raw, _ = sock.recvfrom(1024)
    except socket.timeout:
        return (False,
                f"no reply from {ip} in {timeout:g}s — bulb offline or wrong IP",
                SPOKEN_UNREACHABLE)
    except OSError as e:
        # Bad address, no route, network down: all arrive here, all worth quoting
        # as themselves because the fix differs for each.
        return False, f"udp send to {ip} failed: {e}", SPOKEN_UNREACHABLE
    finally:
        sock.close()

    # The bulb answers {"method":"setPilot","result":{"success":true}}. A reply
    # that parses but does not say success means the bulb rejected the params,
    # which is a different fix from an unreachable bulb and so a different line.
    try:
        reply = json.loads(raw.decode(errors="replace"))
    except ValueError:
        return (False, f"unparseable reply from {ip}: {raw[:80]!r}",
                "The light gave me a strange answer.")
    result = reply.get("result") if isinstance(reply, dict) else None
    if isinstance(result, dict) and result.get("success") is True:
        return True, "", ""
    return (False, f"bulb rejected {params}: {reply}",
            "The light wouldn't take that.")


def shape_error(params):
    """Return why these params cannot be forwarded, or "" if they can.

    Shape only — see INT_PARAMS. This is the last gate before hardware, not a
    second copy of the backend's validation.
    """
    if not isinstance(params, dict) or not params:
        return f"bad params: {params!r}"
    if not isinstance(params.get("state"), bool):
        return f"bad state: {params.get('state')!r}"
    for key, value in params.items():
        if key == "state":
            continue
        if key not in INT_PARAMS:
            return f"unexpected param {key!r}"
        # isinstance(True, int) is True in Python, so bools need excluding by
        # hand or {"dimming": true} sails through as brightness 1. JSON numbers
        # arrive as float when they carry a decimal point, which no setPilot
        # argument does, so int is the whole of what is acceptable.
        if isinstance(value, bool) or not isinstance(value, int):
            return f"bad {key}: {value!r}"
    return ""


def apply_action(action):
    """Carry out an action from the backend's /voice/audio reply.

    Returns (ok, reason, spoken). The envelope is
    {"action":"bulb","method":"setPilot","params":{...}} — self-describing, so it
    reads on its own in a log, and discriminated so a second kind of action can
    be added later without guessing at which one arrived.
    """
    if not isinstance(action, dict) or action.get("action") != "bulb":
        return False, f"unknown action: {action!r}", "I didn't understand that command."
    if action.get("method") != "setPilot":
        # Nothing else is in scope, and the alternative is forwarding an
        # arbitrary method name to a device on the LAN.
        return (False, f"unsupported method: {action.get('method')!r}",
                "I didn't understand that command.")

    params = action.get("params")
    bad = shape_error(params)
    if bad:
        return False, bad, "I didn't understand that command."

    return set_pilot(params)


def _cli_action(args):
    """Build the same envelope the backend sends, so the CLI exercises one path."""
    kind = args[0]
    params = {"state": kind != "off"}
    if kind == "on" and len(args) > 1:
        params["dimming"] = int(args[1])
    elif kind == "scene":
        params["sceneId"] = int(args[1])
    elif kind == "rgb":
        params["r"], params["g"], params["b"] = (int(a) for a in args[1:4])
    elif kind == "temp":
        params["temp"] = int(args[1])
    return {"action": "bulb", "method": "setPilot", "params": params}


def _selftest():
    """Runs with no bulb on the network.

    The branches worth checking are all in the envelope and shape handling, so
    set_pilot is stubbed and the assertions are about which payloads reach it —
    including that a refused one reaches it not at all.
    """
    global set_pilot

    sent = []

    def fake_set_pilot(params, ip=None, timeout=None):
        sent.append(params)
        return True, "", ""

    def envelope(params):
        return {"action": "bulb", "method": "setPilot", "params": params}

    real, set_pilot = set_pilot, fake_set_pilot
    try:
        # Every param the backend can build has to survive the trip untouched.
        for params in (
            {"state": False},
            {"state": True},                                    # bare on
            {"state": True, "dimming": 40},
            {"state": True, "sceneId": 6},
            {"state": True, "sceneId": 32, "dimming": 40},       # composes
            {"state": True, "r": 255, "g": 0, "b": 128},
            {"state": True, "r": 0, "g": 0, "b": 0},             # black is a colour
            {"state": True, "temp": 2700},
            {"state": True, "temp": 6500, "dimming": 100},
        ):
            ok, reason, spoken = apply_action(envelope(params))
            assert ok, f"{params} refused: {reason}"
            assert sent[-1] == params, f"{params} was altered to {sent[-1]}"
            assert not spoken, "a success should not have anything to say"

        before = len(sent)
        for bad in (
            None,
            "bulb",
            {},
            envelope(None),
            envelope({}),                                        # no state
            envelope({"dimming": 40}),                           # still no state
            envelope({"state": "true"}),                         # string, not bool
            envelope({"state": 1}),                              # int, not bool
            envelope({"state": True, "dimming": "40"}),           # string, not int
            envelope({"state": True, "dimming": True}),           # bool is an int here
            envelope({"state": True, "dimming": 40.5}),           # float, not int
            envelope({"state": True, "speed": 50}),               # not a param we forward
            {"action": "timer", "method": "setPilot", "params": {"state": True}},
            {"action": "bulb", "method": "setSystemConfig", "params": {"state": True}},
            {"action": "bulb", "params": {"state": True}},        # no method
        ):
            ok, reason, spoken = apply_action(bad)
            assert not ok, f"accepted {bad!r}"
            assert reason, f"refused {bad!r} without saying why in the log"
            assert spoken, f"refused {bad!r} without anything to say aloud"
        assert len(sent) == before, "a refused action still reached the bulb"
    finally:
        set_pilot = real

    # A missing IP has to fail rather than broadcast, and name the knob to turn.
    ok, reason, spoken = set_pilot({"state": True}, ip="")
    assert not ok and "DESKY_BULB_IP" in reason, reason
    assert spoken

    # The CLI has to build the same envelope the backend does, or bench-testing a
    # scene proves nothing about the path the voice loop takes.
    assert _cli_action(["off"])["params"] == {"state": False}
    assert _cli_action(["on"])["params"] == {"state": True}
    assert _cli_action(["on", "40"])["params"] == {"state": True, "dimming": 40}
    assert _cli_action(["scene", "6"])["params"] == {"state": True, "sceneId": 6}
    assert _cli_action(["rgb", "255", "0", "128"])["params"] == {
        "state": True, "r": 255, "g": 0, "b": 128}
    assert _cli_action(["temp", "2700"])["params"] == {"state": True, "temp": 2700}
    for args in (["off"], ["on", "40"], ["scene", "6"], ["rgb", "1", "2", "3"], ["temp", "3000"]):
        assert not shape_error(_cli_action(args)["params"]), args


USAGE = ("usage: light.py on [brightness] | off | scene <1-32> | "
         "rgb <r> <g> <b> | temp <kelvin> | --selftest")

if __name__ == "__main__":
    args = sys.argv[1:]
    if args == ["--selftest"]:
        _selftest()
        print("selftest ok")
        sys.exit(0)

    kinds = {"on": (1, 2), "off": (1, 1), "scene": (2, 2), "rgb": (4, 4), "temp": (2, 2)}
    if not args or args[0] not in kinds:
        sys.exit(USAGE)
    low, high = kinds[args[0]]
    if not low <= len(args) <= high:
        sys.exit(USAGE)

    try:
        action = _cli_action(args)
    except ValueError as e:
        sys.exit(f"{e}\n{USAGE}")

    ok, reason, spoken = apply_action(action)
    print("ok" if ok else f"failed: {reason}")
    sys.exit(0 if ok else 1)
