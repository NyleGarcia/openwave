"""Recovering a capture device that enumerated but never started producing.

An Elgato Wave replugged while the system is running comes back on the USB
bus, gets its ALSA card, gets a PipeWire node, and reports itself unmuted at
full gain with phantom power on -- and delivers no audio frames at all. Not
quiet frames: none. Every layer says the device is healthy, so nothing
notices, and the microphone is simply dead until the card is opened again.

The distinction that makes this detectable is between *silence* and *no
data*. A live analogue input always delivers a noise floor; a stalled one
delivers nothing, so a meter reading it blocks forever on its first read.
That is the signal used here -- no bytes at all while the node exists -- and
it is why a level threshold would be the wrong test: a muted microphone in a
quiet room is legitimately near zero and must not be "recovered".

The remedy is to make ALSA close and reopen the device, which cycling the
card's profile does. Restarting the capture keepalive does not: it exists to
prevent the race, and cannot clear one that has already happened.
"""

import logging
import re
import subprocess

# How long a live node may deliver nothing before it counts as stalled. Long
# enough to survive a device changing profile or a meter restarting, short
# enough that nobody finishes a sentence into a dead microphone.
STALL_SECONDS = 8.0
# A failed recovery must not become a loop: cycling a card is disruptive, and
# a device that is genuinely broken should be left alone to be noticed.
COOLDOWN_SECONDS = 60.0
MAX_ATTEMPTS = 2

log = logging.getLogger(__name__)


def card_name_for(node_name):
    """The ALSA card behind a capture node, or None.

    alsa_input.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9-00.mono-fallback
      -> alsa_card.usb-Elgato_Systems_Elgato_XLR_Dock_A8A9-00

    The trailing component is the profile, not part of the device, and the
    card name is the device stem with the card prefix.
    """
    if not node_name or not node_name.startswith(("alsa_input.", "alsa_output.")):
        return None
    stem = node_name.split(".", 1)[1]
    if "." in stem:
        stem = stem.rsplit(".", 1)[0]
    return f"alsa_card.{stem}" if stem else None


def _pactl(*args, timeout=5):
    try:
        result = subprocess.run(
            ["pactl", *args], capture_output=True, text=True, timeout=timeout,
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return result.stdout if result.returncode == 0 else None


def active_profile(card_name):
    """The card's current profile name, or None if the card is unknown."""
    out = _pactl("list", "cards")
    if not out:
        return None
    wanted = f"Name: {card_name}"
    inside = False
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("Name: alsa_card."):
            inside = stripped == wanted
        elif inside and stripped.startswith("Active Profile:"):
            return stripped.split(":", 1)[1].strip()
    return None


def cycle_card(card_name):
    """Force ALSA to close and reopen a card. Returns True if it was cycled.

    Through `off` rather than straight back to the same profile: setting a
    card to the profile it already has is a no-op, and the point is the close
    and reopen, not the profile itself. The profile is restored afterwards
    because it is the user's -- OpenWave deliberately puts a Wave into an
    input-only profile, and coming back on a different one would silently
    change what the device is.
    """
    profile = active_profile(card_name)
    if not profile or profile == "off":
        return False
    if _pactl("set-card-profile", card_name, "off") is None:
        return False
    restored = _pactl("set-card-profile", card_name, profile)
    if restored is None:
        log.error("left %s off: could not restore profile %s",
                  card_name, profile)
        return False
    log.info("recovered %s by cycling profile %s", card_name, profile)
    return True


class StallWatch:
    """Decides when a capture node has stalled, and rate-limits the remedy.

    Kept separate from the acting on it so the decision can be tested without
    a sound card: every input is a number or a bool.
    """

    def __init__(self, stall_seconds=STALL_SECONDS,
                 cooldown_seconds=COOLDOWN_SECONDS,
                 max_attempts=MAX_ATTEMPTS):
        self.stall_seconds = stall_seconds
        self.cooldown_seconds = cooldown_seconds
        self.max_attempts = max_attempts
        self._attempts = {}      # node_name -> count
        self._last_attempt = {}  # node_name -> monotonic time

    def forget(self, node_name):
        """A node that went away starts clean when it comes back.

        Attempts are counted per appearance, not for the life of the process:
        unplugging and replugging is exactly how the stall arises, so it must
        not exhaust the budget from the previous time.
        """
        self._attempts.pop(node_name, None)
        self._last_attempt.pop(node_name, None)

    def should_recover(self, node_name, node_present, silent_for, now):
        """True when this node is stalled and may be acted on right now."""
        if not node_present or node_name is None:
            # Absent is not stalled. Cycling a card for a device that has
            # been unplugged would fight the person who unplugged it.
            return False
        if silent_for is None or silent_for < self.stall_seconds:
            return False
        if self._attempts.get(node_name, 0) >= self.max_attempts:
            return False
        last = self._last_attempt.get(node_name)
        if last is not None and now - last < self.cooldown_seconds:
            return False
        return True

    def record_attempt(self, node_name, now):
        self._attempts[node_name] = self._attempts.get(node_name, 0) + 1
        self._last_attempt[node_name] = now

    def record_recovered(self, node_name):
        """Audio came back, so the budget is spent on the next stall only."""
        self.forget(node_name)
