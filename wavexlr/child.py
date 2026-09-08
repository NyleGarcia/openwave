"""Spawn an audio helper that dies with us, without preexec_fn.

`PR_SET_PDEATHSIG` is the only thing that reliably reaps `pw-loopback` and
`pw-cat` when this process dies in a way that skips every Python cleanup path
— SIGKILL, a compositor cutting the client loose, a hard crash. Setting it from
`preexec_fn` is the obvious spelling and the wrong one: that callback runs in
the forked child between `fork()` and `exec()`, where only async-signal-safe
work is legal, and this is a threaded GTK process. A fork inherits exactly one
thread but all of the locks, so a malloc arena or an import lock held by
another thread at the instant of the fork is held forever in the child —
CPython itself warns that `preexec_fn` is unsafe in the presence of threads.

So run the prctl in a fresh interpreter instead. `spawn` starts this module,
which sets the flag in a process of its own and then `execvp`s the real helper
over itself: same pid, so the caller's `terminate`, `kill` and `wait` still
address the process it thinks they do, and no supervisor is left behind.

Shape ported from rikkichy/openwave, whose `child.py` established it.
"""

import errno
import os
import shutil
import subprocess
import sys

_PR_SET_PDEATHSIG = 1


def spawn(argv, **kwargs):
    """Popen `argv` with SIGTERM on our death. Keyword arguments pass through.

    Raises FileNotFoundError when the helper is not installed, as a direct
    Popen of it would. The interpreter this starts always exists, so without
    the check a missing pw-cat would spawn successfully and fail later inside
    the child — leaving callers that watch for it at the call site (the meter
    gives up on the row, calibration reports which node it could not record)
    to see an empty stream instead.
    """
    if shutil.which(argv[0]) is None:
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), argv[0])
    return subprocess.Popen(
        [sys.executable, "-m", "wavexlr.child", str(os.getpid()), *argv],
        **kwargs,
    )


def main():
    import ctypes
    import signal

    parent = int(sys.argv[1])
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(_PR_SET_PDEATHSIG, int(signal.SIGTERM), 0, 0, 0) != 0:
        raise OSError(ctypes.get_errno(), "cannot protect audio child lifetime")
    # The signal only fires for a death that happens from here on, so a parent
    # that died while we were still starting would leave this child orphaned
    # and running. Nothing to exec for: bail instead.
    if os.getppid() != parent:
        return 1
    os.execvp(sys.argv[2], sys.argv[2:])


if __name__ == "__main__":
    sys.exit(main())
