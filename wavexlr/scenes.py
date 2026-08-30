"""The scene store: named snapshots of the matrix, recalled as one gesture.

A scene holds levels for the matrix that exists — source trims and mutes,
cell sends and mutes, per-mix outputs and master volumes, and optionally
hardware state keyed by device profile. It deliberately does not hold mix or
source *definitions*: applying a scene never creates or deletes a row or a
column, so a scene can never restructure the matrix under the user.

Not named "profiles": that word is taken by the per-device protocol
profiles in profiles.py, and a store that could be confused with USB
constants would be worse than a second noun. The UI may still say what it
likes.

Store shape (~/.config/openwave/scenes.json):

    {"scenes": {"<id>": {"name": ..., "sources": ..., "cells": ...,
                          "outputs": ..., "volumes": ..., "hardware": ...}}}

Same durability rules as the other stores: whole-file rewrite on save, and
a corrupt file is preserved as scenes.json.corrupt and replaced with an
empty store — a bad write costs the scenes, never the app.
"""

import json
import os
import re

CONFIG_PATH = os.path.expanduser("~/.config/openwave/scenes.json")


class Unreadable(Exception):
    pass


def _load_raw():
    with open(CONFIG_PATH) as f:
        data = json.load(f)
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), dict):
        raise Unreadable("top-level shape is not {'scenes': {...}}")
    return data["scenes"]


def load():
    """Every stored scene, {} on first run; a corrupt file is set aside."""
    if not os.path.exists(CONFIG_PATH):
        return {}
    try:
        return _load_raw()
    except (OSError, ValueError, Unreadable):
        try:
            os.replace(CONFIG_PATH, CONFIG_PATH + ".corrupt")
        except OSError:
            pass
        return {}


def save(scenes):
    os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump({"scenes": scenes}, f, indent=2)
    os.replace(tmp, CONFIG_PATH)


def scene_id(name):
    """A stable id from a human name: lowercase, dashes, nothing else."""
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "scene"


def hardware_key(profile_key, serial):
    """How a device is addressed inside a scene's hardware section.

    Keyed by serial, not by model: two Docks on one desk are different
    devices with different gains, and a scene keyed by model alone could
    only ever describe one of them.
    """
    return f"{profile_key}:{serial}" if serial else profile_key


def pick_hardware_entry(hardware, profile_key, serial):
    """The scene entry that should apply to this device, or None.

    Exact serial first; then a bare profile key (scenes saved before serial
    keying); then any entry for the same model — a replaced unit should
    still pick up the scene its predecessor was saved with, rather than
    silently getting nothing.
    """
    if not hardware:
        return None
    if serial:
        exact = hardware.get(f"{profile_key}:{serial}")
        if exact is not None:
            return exact
    if profile_key in hardware:
        return hardware[profile_key]
    for key, entry in hardware.items():
        if key.split(":", 1)[0] == profile_key:
            return entry
    return None


def put(name, payload):
    """Store a scene under its name's id, replacing an existing one."""
    scenes = load()
    sid = scene_id(name)
    scenes[sid] = dict(payload, name=name)
    save(scenes)
    return sid


def remove(sid):
    scenes = load()
    if scenes.pop(sid, None) is not None:
        save(scenes)
        return True
    return False
