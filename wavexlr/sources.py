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


def add(sources, source):
    sources[source["id"]] = source
    save(sources)
    return sources


def remove(sources, source_id):
    sources.pop(source_id, None)
    save(sources)
    return sources

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
