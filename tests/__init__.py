"""Every test runs against a throwaway config, whether it asked to or not.

One test class built a bare mixer without temp_config(), and its set_cell
calls therefore rewrote the real ~/.config/openwave/mixes.json -- from an
empty state, so one write replaced the user's whole matrix with the test's
fixtures. It looked like the application losing settings at random, because
the wipe happened whenever the suite ran, and it left behind a plausible
55% Music cell that was actually test data.

temp_config() remains the right tool inside a test; this is the seatbelt for
the test that forgets it. Redirected here, at package import, before any
test module loads, so there is no ordering to get wrong.
"""

import atexit
import os
import tempfile

from wavexlr import mixer, mixes, scenes, sources

_SANDBOX = tempfile.TemporaryDirectory(prefix="openwave-tests-")
atexit.register(_SANDBOX.cleanup)

sources.CONFIG_PATH = os.path.join(_SANDBOX.name, "sources.json")
mixes.CONFIG_PATH = os.path.join(_SANDBOX.name, "mixdefs.json")
mixer.CONFIG_PATH = os.path.join(_SANDBOX.name, "mixes.json")
scenes.CONFIG_PATH = os.path.join(_SANDBOX.name, "scenes.json")
mixer.Mixer._TRACE_PATH = os.path.join(_SANDBOX.name, "write-trace.log")
