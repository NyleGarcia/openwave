"""Null-sink creation and the default-sink election.

Both are places where a wrong property is silent: the sink appears, audio
flows, and something subtly wrong happens somewhere else.
"""

import unittest

from wavexlr import setup


class NullSinkProperties(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self._run = setup.subprocess.run
        self._exists = setup._mix_sink_exists
        setup._mix_sink_exists = lambda name: False
        setup.subprocess.run = lambda cmd, **kw: self.calls.append(cmd) or _Ok()

    def tearDown(self):
        setup.subprocess.run = self._run
        setup._mix_sink_exists = self._exists

    def _args_for(self, **kwargs):
        setup.create_null_sink("openwave_test", "Test", **kwargs)
        self.assertTrue(self.calls, "pw-cli was never invoked")
        return self.calls[-1][-1]

    def test_it_lingers(self):
        # Without this the node dies the instant pw-cli exits, so the sink
        # never survives long enough to be used.
        self.assertIn("object.linger=true", self._args_for())

    def test_its_monitor_follows_the_sink_volume(self):
        # Otherwise the monitor is taken pre-volume and any level applied to
        # the sink has no effect on what a loopback reads from it.
        self.assertIn("monitor.channel-volumes=true", self._args_for())

    def test_priority_is_omitted_unless_asked_for(self):
        # A mix sink must stay eligible to be the system default.
        self.assertNotIn("priority.session", self._args_for())

    def test_an_intake_can_be_made_ineligible(self):
        # An intake winning the default-sink election sends every application
        # into one source row at that row's send level.
        self.assertIn("priority.session=0", self._args_for(priority=0))

    def test_the_description_is_quoted(self):
        setup.create_null_sink("openwave_test", 'Odd " name')
        args = self.calls[-1][-1]
        self.assertIn('node.description="Odd \\" name"', args)

    def test_the_property_list_is_balanced(self):
        args = self._args_for(priority=0)
        self.assertTrue(args.startswith("{ "), args[:20])
        self.assertTrue(args.endswith(" }"), args[-20:])


class _Ok:
    returncode = 0
    stdout = ""
    stderr = ""


if __name__ == "__main__":
    unittest.main()
