"""Shared helpers: keep every test off the user's real configuration."""

import contextlib
import os
import tempfile

from wavexlr import mixes, sources
from wavexlr import mixer as mixer_mod


@contextlib.contextmanager
def temp_config():
    """Point every JSON store at a throwaway directory.

    The stores address their files through module-level constants, so a test
    that forgot this would read and overwrite the real ~/.config/openwave.
    """
    with tempfile.TemporaryDirectory() as tmp:
        originals = (
            sources.CONFIG_PATH, mixes.CONFIG_PATH, mixer_mod.CONFIG_PATH,
        )
        sources.CONFIG_PATH = os.path.join(tmp, "sources.json")
        mixes.CONFIG_PATH = os.path.join(tmp, "mixdefs.json")
        mixer_mod.CONFIG_PATH = os.path.join(tmp, "mixes.json")
        try:
            yield tmp
        finally:
            (sources.CONFIG_PATH, mixes.CONFIG_PATH,
             mixer_mod.CONFIG_PATH) = originals


def stream(app_name="", binary="", node_name="", stream_id=1):
    """A stream record shaped like list_audio_streams() returns."""
    return {
        "id": stream_id, "app_name": app_name,
        "binary": binary, "node_name": node_name, "serial": 1000 + stream_id,
    }


def bare_mixer(**attrs):
    """A Mixer with no worker thread, for exercising pure logic.

    Mixer.__init__ starts a background thread and probes hardware; none of
    that is wanted here, and a leaked worker would outlive the test.
    """
    import threading
    mx = object.__new__(mixer_mod.Mixer)
    mx._lock = threading.Lock()
    mx._state = {}
    mx._sources = {}
    mx._mixes = {}
    mx._procs = {}
    mx._intakes = set()
    mx._live_captures = frozenset()
    mx.mic = None
    mx.hp = None
    mx._started = False
    # set_cell and friends enqueue their reconcile even with no worker
    # running. The queue is the seam: work lands in _pending and stays there,
    # so a test can call the real entry points and inspect the state they
    # persisted without a subprocess ever being spawned.
    mx._pending = {}
    mx._pending_lock = threading.Lock()
    mx._wake = threading.Event()
    for key, value in attrs.items():
        setattr(mx, key, value)
    return mx
