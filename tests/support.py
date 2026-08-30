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
    mx._volumes_restored = True
    # The default seam delegates to the module-level functions at call time,
    # so a test that patches mixer_mod._pactl_set_sink_volume still
    # intercepts. Pass _pw=FakePipeWire() instead to assert on graph calls.
    mx._pw = mixer_mod.SubprocessPipeWire()
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


class FakeProc:
    """A loopback process that never was: records its lifecycle."""

    def __init__(self, argv):
        self.argv = argv
        self.terminated = False
        self.killed = False
        self._returncode = None

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return self._returncode if self._returncode is not None else 0

    def terminate(self):
        self.terminated = True
        self._returncode = 0

    def kill(self):
        self.killed = True
        self._returncode = -9

    def dies(self):
        """Simulate an out-of-band death, PipeWire restarting under it."""
        self._returncode = 1


class FakePipeWire:
    """A PipeWire graph made of dicts, recording every call in order.

    Configure what exists (node ids, ports, streams, sink volumes); read
    back `calls` to assert what the mixer decided to do about it. Nothing
    here spawns a process or needs a sound card.
    """

    def __init__(self):
        self.calls = []
        self.node_ids = {}       # node_name -> id
        self.port_map = {}       # (flag, node_name) -> [ports]
        self.streams = []
        self.volumes = {}        # sink_name -> (volume, muted)
        self.default = "default_sink"
        self.spawned = []        # FakeProc, in spawn order
        self.spawn_fails = False

    def short_list(self, kind):
        self.calls.append(("short_list", kind))
        return []

    def sink_volumes(self):
        self.calls.append(("sink_volumes",))
        return dict(self.volumes)

    def set_sink_volume(self, name, volume):
        self.calls.append(("set_sink_volume", name, round(volume, 3)))

    def set_sink_mute(self, name, muted):
        self.calls.append(("set_sink_mute", name, muted))

    def move_stream(self, serial, sink_name):
        self.calls.append(("move_stream", serial, sink_name))

    def node_id(self, name, retries=20):
        self.calls.append(("node_id", name))
        return self.node_ids.get(name)

    def wpctl(self, *args):
        self.calls.append(("wpctl",) + args)

    def ports(self, direction_flag, node_name):
        return self.port_map.get((direction_flag, node_name), [])

    def link(self, src_port, dst_port):
        self.calls.append(("link", src_port, dst_port))
        return True

    def audio_streams(self):
        return list(self.streams)

    def default_sink(self):
        return self.default

    def set_default_sink(self, name):
        self.calls.append(("set_default_sink", name))

    def spawn_loopback(self, argv, detach):
        self.calls.append(("spawn", argv, detach))
        if self.spawn_fails:
            return None
        proc = FakeProc(argv)
        self.spawned.append(proc)
        return proc

    def sweep_stale_loopbacks(self):
        self.calls.append(("sweep",))

    def find_wave(self):
        self.calls.append(("find_wave",))
        return (None, None)
