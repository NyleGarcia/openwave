"""Mix definitions, persisted to ~/.config/openwave/mixdefs.json.

Mix *identity* only — name, icon, and the PipeWire sink that carries it.
Per-cell levels live separately in ~/.config/openwave/mixes.json, written by
Mixer; keeping the two apart means a slider move can never clobber a
definition, and a definition edit can never zero a level.

`sink` is stored explicitly rather than derived from `id` so that renaming a
mix never renames the PipeWire node an OBS or Discord capture is pointed at.
`description` is the node.description PipeWire publishes, which is distinct
from the name shown in our own UI for the same reason.
"""

import copy
import json
import os
import re
import uuid

CONFIG_PATH = os.path.expanduser("~/.config/openwave/mixdefs.json")

# Ids are interpolated unquoted into pw-loopback properties and into
# "."-separated cell keys, so anything outside this set silently corrupts one
# or the other. uuid4().hex satisfies it; a display name must never be an id.
_ID_RE = re.compile(r"^[a-z0-9_]+$")

DEFAULT_ICON = "audio-speakers-symbolic"


class Unreadable(Exception):
    """mixdefs.json exists but could not be parsed."""


# Insertion order is column order — sources.py already relies on dict order
# for row order, and json round-trips it. Do not add an "order" field.
DEFAULT_MIXES = {
    "personal": {
        "id": "personal",
        "name": "Personal Mix",
        "subtitle": "What you hear",
        "description": "OpenWave Personal Mix",
        "sink": "openwave_personal_mix",
        "icon_name": "audio-headphones-symbolic",
    },
    "chat": {
        "id": "chat",
        "name": "Chat Mix",
        "subtitle": "To voice apps (v0.3.0)",
        "description": "OpenWave Chat Mix",
        "sink": "openwave_chat_mix",
        "icon_name": "system-users-symbolic",
    },
    "record": {
        "id": "record",
        "name": "Record Mix",
        "subtitle": "To OBS / recording (v0.3.0)",
        "description": "OpenWave Record Mix",
        "sink": "openwave_record_mix",
        "icon_name": "media-record-symbolic",
    },
}


def _atomic_write(path, payload):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(payload, f, indent=2)
    os.replace(tmp, path)


def load():
    """Return the stored mixes, or None if the file does not exist.

    Raises Unreadable when the file is present but corrupt. Callers must not
    conflate that with "the user has no mixes": the consumer overwrites the
    generated PipeWire config, so treating a parse failure as an empty store
    would delete every sink.
    """
    if not os.path.exists(CONFIG_PATH):
        return None
    try:
        with open(CONFIG_PATH) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        raise Unreadable(str(exc)) from exc
    if not isinstance(data, dict):
        raise Unreadable("top-level value is not an object")
    return data


def load_seeded():
    """Load the store, creating it from DEFAULT_MIXES on first run.

    A corrupt file is preserved as mixdefs.json.corrupt and replaced with the
    defaults, so a bad write costs the user their customisation but never
    leaves the app with no mixes at all.
    """
    try:
        data = load()
    except Unreadable:
        try:
            os.replace(CONFIG_PATH, CONFIG_PATH + ".corrupt")
        except OSError:
            pass
        data = None
    if data is None:
        data = copy.deepcopy(DEFAULT_MIXES)
        save(data)
    return data


def save(mixes):
    _atomic_write(CONFIG_PATH, mixes)


def new_mix(*, name, subtitle="", icon_name=DEFAULT_ICON):
    """Return a fresh mix dict ready to insert into the mixes mapping."""
    mix_id = uuid.uuid4().hex[:12]
    if not _ID_RE.match(mix_id):  # defensive; uuid4().hex always matches
        raise ValueError(f"generated id is not safe to interpolate: {mix_id!r}")
    return {
        "id": mix_id,
        "name": name,
        "subtitle": subtitle,
        "description": f"OpenWave {name}",
        "sink": f"openwave_mix_{mix_id}",
        "icon_name": icon_name,
    }


def add(mixes, mix):
    mixes[mix["id"]] = mix
    save(mixes)
    return mixes


def remove(mixes, mix_id):
    mixes.pop(mix_id, None)
    save(mixes)
    return mixes


def update(mixes, mix_id, **fields):
    """Edit a mix in place, preserving its id and sink.

    id and sink are structural: cell keys in mixes.json are "<source>.<mix_id>",
    and other applications target the sink by name.
    """
    mix = mixes.get(mix_id)
    if mix is None:
        return mixes
    for key, value in fields.items():
        if key in ("id", "sink"):
            continue
        mix[key] = value
    save(mixes)
    return mixes
