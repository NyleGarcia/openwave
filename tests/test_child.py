"""Audio helpers get parent-death protection without preexec_fn.

The three spawn sites — pw-loopback, the meter's pw-cat and calibration's —
used to set PR_SET_PDEATHSIG from a `preexec_fn`, which runs in the forked
child between fork() and exec(). Only async-signal-safe work is legal there
and this is a threaded GTK process: a lock another thread held at the instant
of the fork is held forever in the child. These pin the replacement, which
runs the prctl in an interpreter of its own and then execs over itself.
"""

import os
import signal
import subprocess
import sys
import time
import unittest
from unittest import mock

from wavexlr import child


class TestSpawnShape(unittest.TestCase):
    def test_wraps_argv_with_our_pid(self):
        """The helper needs our pid to detect a parent that died before prctl."""
        with mock.patch.object(child.subprocess, "Popen") as popen:
            child.spawn(["sh", "-c", "true"])
        argv, = popen.call_args[0]
        self.assertEqual(
            argv,
            [sys.executable, "-m", "wavexlr.child", str(os.getpid()),
             "sh", "-c", "true"],
        )

    def test_runs_the_requested_command(self):
        proc = child.spawn(["echo", "routed"], stdout=subprocess.PIPE)
        out, _ = proc.communicate(timeout=10)
        self.assertEqual(out.strip(), b"routed")
        self.assertEqual(proc.returncode, 0)

    def test_pid_is_the_helper_itself(self):
        """execvp replaces the interpreter, so terminate() reaches the helper.

        If a supervisor stayed in between, the caller's terminate() would kill
        the wrapper and leave pw-cat holding the node open — exactly the stall
        health.py then reports.
        """
        proc = child.spawn(["sh", "-c", "echo $$; sleep 30"], stdout=subprocess.PIPE)
        try:
            reported = int(proc.stdout.readline().strip())
            self.assertEqual(reported, proc.pid)
        finally:
            proc.terminate()
            proc.wait(timeout=10)
            proc.stdout.close()

    def test_missing_helper_raises_at_the_call_site(self):
        """A direct Popen raised here; the wrapper must not defer it.

        sys.executable always exists, so an unchecked wrapper would spawn
        happily and fail inside the child. The meter's `except FileNotFoundError`
        would never fire and the row would meter an empty stream instead of
        giving up.
        """
        with self.assertRaises(FileNotFoundError):
            child.spawn(["pw-cat-that-is-not-installed", "--record"])

    def test_keyword_arguments_reach_popen(self):
        proc = child.spawn(["sh", "-c", "echo to-err >&2"], stderr=subprocess.PIPE)
        _, err = proc.communicate(timeout=10)
        self.assertEqual(err.strip(), b"to-err")


class TestParentDeath(unittest.TestCase):
    def test_helper_dies_with_its_parent(self):
        """A parent killed outright still takes its audio children with it."""
        script = (
            "import subprocess, sys, time\n"
            "from wavexlr import child\n"
            "p = child.spawn(['sleep', '60'])\n"
            "print(p.pid, flush=True)\n"
            "time.sleep(60)\n"
        )
        parent = subprocess.Popen(
            [sys.executable, "-c", script],
            stdout=subprocess.PIPE,
            cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        )
        try:
            grandchild = int(parent.stdout.readline().strip())
            # SIGKILL: no Python cleanup path runs, so only PDEATHSIG can act.
            parent.kill()
            parent.wait(timeout=10)
            deadline = time.monotonic() + 10
            while time.monotonic() < deadline:
                try:
                    os.kill(grandchild, 0)
                except OSError:
                    return
                time.sleep(0.05)
            os.kill(grandchild, signal.SIGKILL)
            self.fail("audio helper outlived a SIGKILLed parent")
        finally:
            parent.stdout.close()
            if parent.poll() is None:
                parent.kill()
                parent.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
