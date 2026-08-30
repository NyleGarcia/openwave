"""The diagnostics bundle must survive everything being wrong.

It is what a reporter attaches when something is broken, so a collector
that is missing, hung or crashing must become a line in the bundle, never
an exception that prevents the bundle.
"""

import unittest

from wavexlr import diag


class Assemble(unittest.TestCase):
    def test_a_raising_collector_becomes_a_line(self):
        def boom():
            raise RuntimeError("kaput")
        text = diag.assemble(sections=(("Broken", boom),))
        self.assertIn("== Broken ==", text)
        self.assertIn("unavailable", text)
        self.assertIn("kaput", text)

    def test_other_sections_survive_a_broken_one(self):
        def boom():
            raise OSError("no")
        text = diag.assemble(sections=(("Bad", boom),
                                       ("Good", lambda: "fine")))
        self.assertIn("fine", text)

    def test_every_real_section_appears(self):
        text = diag.assemble()
        for title, _fn in diag.SECTIONS:
            self.assertIn(f"== {title} ==", text)

    def test_full_flag_is_announced_in_the_header(self):
        self.assertIn("--full", diag.assemble(full=True).splitlines()[0])
        self.assertNotIn("--full", diag.assemble(full=False).splitlines()[0])

    def test_default_withholds_config_contents(self):
        self.assertIn("contents withheld", diag.assemble(full=False))


class Run(unittest.TestCase):
    def test_a_missing_command_is_a_note(self):
        self.assertIn("not found", diag._run("no-such-command-here"))

    def test_a_failing_command_is_a_note(self):
        self.assertIn("exit", diag._run("false"))


if __name__ == "__main__":
    unittest.main()
