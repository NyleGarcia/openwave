"""User-defined matrix sources, persisted to ~/.config/openwave/sources.json.

Two kinds of source share this store:

* an *app* source binds to a PipeWire `application.name`, so any current or
  future audio stream from that application gets mixed through its row;
* a *device* source binds to the node.name of a hardware capture device — a
  headset microphone, a line input — which is a Source node rather than a
  stream and so is wired exactly like the Wave's own mic.

The `kind` field discriminates them. Records written before device sources
existed carry no `kind` at all, and kind() reads those as app sources, so this
file is never rewritten merely to add a discriminator.
"""

import copy
import json
import os
import uuid

CONFIG_PATH = os.path.expanduser("~/.config/openwave/sources.json")


def _atomic_write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def load():
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def load_seeded():
    """Load the store, creating it from DEFAULT_SOURCES on first run.

    An existing but empty file is respected: a user who has deleted every row
    means it, and reseeding would put them all back on the next launch.
    """
    if not os.path.exists(CONFIG_PATH):
        seeded = copy.deepcopy(DEFAULT_SOURCES)
        save(seeded)
        return seeded
    return load()


def save(sources):
    _atomic_write(CONFIG_PATH, sources)


KIND_APP = "app"
KIND_DEVICE = "device"

DEFAULT_APP_ICON = "applications-multimedia-symbolic"
DEFAULT_DEVICE_ICON = "audio-input-microphone-symbolic"


def kind(source):
    """The kind of a source record.

    Records predating device sources have no "kind" key; they are app sources,
    which is why this defaults rather than requiring load() to migrate. An
    older build reading a file we wrote simply ignores the extra key, so the
    store stays readable in both directions.
    """
    return (source or {}).get("kind") or KIND_APP


# Seeded on first run so the matrix opens with a usable set of rows rather
# than an empty grid. Every cell starts at zero, so a seeded row routes nothing
# and moves no stream until a fader is raised -- they are suggestions, not
# behaviour.
#
# Names are matched case-insensitively against a stream's application name,
# node name and process binary, so one entry covers a program whose reported
# name differs from its binary (Discord publishes "WEBRTC VoiceEngine" and runs
# as "Discord"; a Proton game reports its own name under a wine binary).
DEFAULT_SOURCES = {
    "system": {
        "id": "system", "name": "System",
        "subtitle": "Anything not matched by another row",
        "icon_name": "preferences-system-symbolic",
        # Takes anything no other row claims, so a program nobody has named
        # still gets a fader rather than slipping past the matrix. Listed names
        # still win, so this only ever catches the remainder.
        "catch_all": True,
        "match_app_names": [
            "gnome-shell", "GNOME Shell", "gsd-media-keys", "plasmashell",
            "libcanberra", "canberra-gtk-play", "speech-dispatcher",
            "xdg-desktop-portal", "notify-send",
        ],
    },
    "game": {
        "id": "game", "name": "Game", "icon_name": "applications-games-symbolic",
        "match_app_names": [
            "steam", "Steam", "steamwebhelper", "lutris", "heroic",
            "wine64-preloader", "wine-preloader", "wine", "gamescope",
            "RSI Launcher", "bottles", "Minecraft",
        ],
    },
    "music": {
        "id": "music", "name": "Music", "icon_name": "audio-x-generic-symbolic",
        "match_app_names": [
            "Spotify", "Tidal", "tidal-hifi", "Rhythmbox", "Lollypop",
            "Amberol", "Clementine", "Strawberry", "Audacious", "Elisa",
            "Deezer", "Feishin", "mpv", "VLC media player",
        ],
    },
    "browser": {
        "id": "browser", "name": "Browser", "icon_name": "web-browser-symbolic",
        "match_app_names": [
            "Firefox", "firefox", "LibreWolf", "Zen Browser", "zen",
            "Chromium", "Google Chrome", "chrome", "Brave", "brave",
            "Vivaldi", "Epiphany", "GNOME Web",
        ],
    },
    "voice": {
        "id": "voice", "name": "Voice", "icon_name": "system-users-symbolic",
        "match_app_names": [
            "Discord", "discord", "Vesktop", "vesktop", "WEBRTC VoiceEngine",
            "TeamSpeak", "ts3client", "Mumble", "Element", "Signal",
            "Telegram", "Zoom", "Slack",
        ],
    },
}


def bindings(source):
    """Every application name this source is bound to.

    Older records carry one `match_app_name` string. Newer ones carry a
    `match_app_names` list, so a single row can gather several applications --
    a Music row gathering two players, or a Games row gathering every game,
    each with one fader instead of a row apiece.
    """
    names = source.get("match_app_names")
    if isinstance(names, list):
        return [str(n).strip() for n in names if str(n).strip()]
    single = source.get("match_app_name")
    if isinstance(single, str) and single.strip():
        return [single.strip()]
    return []


def parse_bindings(text):
    """Split a comma-separated application list, discarding blanks."""
    return [part.strip() for part in str(text).split(",") if part.strip()]


def format_bindings(source):
    """The bindings as one comma-separated string, for an entry field."""
    return ", ".join(bindings(source))


def new_source(*, name, match_app_name, icon_name=DEFAULT_APP_ICON):
    """Return a fresh app source dict ready to insert into the sources mapping."""
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": KIND_APP,
        "name": name,
        "match_app_name": match_app_name,
        "icon_name": icon_name,
    }


def group(source):
    """The exclusivity group a source belongs to, or "" for none.

    Sources sharing a group are mutually exclusive: unmuting one mutes the
    others. Two microphones on one speaker -- a main and a backup -- want
    exactly one of them live, while a second speaker's microphone is in a
    different group (or none) and is unaffected.
    """
    value = (source or {}).get("group")
    return str(value).strip() if value else ""


def groups(sources):
    """Every group name in use, for offering as suggestions."""
    return sorted({group(s) for s in sources.values() if group(s)})


def is_protected(source):
    """True for a row the user should not be able to delete.

    An Elgato input is the device the application exists for; it is discovered
    automatically and removing it would only make it come back confusing.
    """
    return bool((source or {}).get("protected"))


def new_device_source(*, name, node_name, icon_name=DEFAULT_DEVICE_ICON):
    """Return a fresh capture-device source bound to a PipeWire source node.

    `node_name` is the node.name of a hardware Audio/Source. It is stored in
    preference to the node's numeric id, which PipeWire reassigns on every
    replug, and to its description, which is a display string: the ALSA node
    name encodes the card and profile and survives a power cycle. `name` is
    the user's label for the row and is free to differ, the same split
    mixes.py keeps between a mix's `name` and its `sink`.
    """
    return {
        "id": uuid.uuid4().hex[:12],
        "kind": KIND_DEVICE,
        "name": name,
        "node_name": node_name,
        "icon_name": icon_name,
    }


# Per-source DSP settings, stored on the source record because they are
# source identity like trim. Neutral values mean "this effect is off";
# fx_active() is the single definition of whether a chain is needed at all.
DEFAULT_FX = {
    "lowcut": 0,          # Hz: 0 (off), 80 or 120
    "gate": False,        # noise gate (LADSPA swh gate)
    "gate_thresh": -50.0,  # dB the gate opens at
    "comp": False,        # compressor (LADSPA swh sc4m)
    "comp_thresh": -18.0,  # dB compression starts at
    "comp_ratio": 3.0,    # 1:n above threshold
    "eq_low": 0.0,        # dB, low shelf @ 100 Hz
    "eq_mid": 0.0,        # dB, peaking @ 1 kHz
    "eq_high": 0.0,       # dB, high shelf @ 8 kHz
    "delay_ms": 0,        # alignment delay
    "mono": False,        # force centered mono
}


def fx(source):
    """A source's DSP settings, defaults filled in."""
    stored = (source or {}).get("fx") or {}
    return {**DEFAULT_FX, **stored}


def fx_active(source):
    """Whether any effect departs from neutral — the chain exists only then.

    Neutral settings spawn nothing: a pass-through filter node would cost a
    process and a resample for silence-shaped benefit.
    """
    f = fx(source)
    return bool(
        f["lowcut"]
        or f["gate"] or f["comp"]
        or f["eq_low"] or f["eq_mid"] or f["eq_high"]
        or f["delay_ms"]
        or f["mono"]
    )


def add(sources, source):
    sources[source["id"]] = source
    save(sources)
    return sources


def remove(sources, source_id):
    sources.pop(source_id, None)
    save(sources)
    return sources

def set_order(sources, order):
    """Rebuild the mapping in `order`, keeping anything the order omits.

    Insertion order is row order, so this is how a row is pinned to the top.
    """
    seen = [sid for sid in order if sid in sources]
    rest = [sid for sid in sources if sid not in seen]
    reordered = {sid: sources[sid] for sid in seen + rest}
    save(reordered)
    return reordered


def reorder(sources, source_id, delta):
    """Move a source `delta` places in the list, and persist the new order.

    Insertion order is row order, so reordering means rebuilding the mapping.
    Out-of-range moves are clamped rather than wrapping: a button at the end of
    the list should do nothing, not jump the row to the other end.
    """
    order = list(sources)
    if source_id not in order:
        return sources
    idx = order.index(source_id)
    new_idx = max(0, min(len(order) - 1, idx + delta))
    if new_idx == idx:
        return sources
    order.insert(new_idx, order.pop(idx))
    reordered = {sid: sources[sid] for sid in order}
    save(reordered)
    return reordered


def update(sources, source_id, **fields):
    """Edit a source in place, preserving its id.

    The id is structural: per-cell levels are keyed "<source_id>.<mix_id>" in
    ~/.config/openwave/mixes.json and in Mixer's in-memory state, so minting a
    new id (as new_source does) would silently orphan every level the user has
    set for this row. Editing must come through here, never through
    new_source().
    """
    source = sources.get(source_id)
    if source is None:
        return sources
    for key, value in fields.items():
        if key == "id":
            continue
        source[key] = value
    save(sources)
    return sources
