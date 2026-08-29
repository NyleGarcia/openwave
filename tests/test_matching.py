"""Which stream belongs to which source.

The rules here are the ones a regression would break silently: audio would
still play, just from the wrong row, or from two rows at once.
"""

import unittest

from wavexlr import sources
from wavexlr.mixer import claim_streams, stream_matches

from .support import stream


class StreamMatching(unittest.TestCase):
    def test_matches_on_application_name(self):
        src = {"match_app_names": ["Spotify"]}
        self.assertTrue(stream_matches(src, stream(app_name="Spotify")))

    def test_ignores_case_and_surrounding_space(self):
        src = {"match_app_names": ["spotify"]}
        self.assertTrue(stream_matches(src, stream(app_name="  SPOTIFY ")))

    def test_matches_on_process_binary(self):
        # Discord publishes "WEBRTC VoiceEngine" as its application name and
        # runs as "Discord"; only the binary identifies it.
        src = {"match_app_names": ["Discord"]}
        self.assertTrue(
            stream_matches(src, stream(app_name="WEBRTC VoiceEngine",
                                       binary="Discord"))
        )

    def test_rejects_substrings(self):
        # "Chrome" must not swallow every Chromium stream.
        src = {"match_app_names": ["Chrome"]}
        self.assertFalse(stream_matches(src, stream(app_name="Chromium")))

    def test_reads_the_superseded_singular_key(self):
        # Records written before multi-application sources existed.
        legacy = {"match_app_name": "Spotify"}
        self.assertEqual(sources.bindings(legacy), ["Spotify"])
        self.assertTrue(stream_matches(legacy, stream(app_name="Spotify")))

    def test_a_source_bound_to_nothing_matches_nothing(self):
        self.assertFalse(stream_matches({}, stream(app_name="Spotify")))
        self.assertFalse(
            stream_matches({"match_app_names": []}, stream(app_name="Spotify"))
        )


class Claiming(unittest.TestCase):
    def test_a_stream_has_exactly_one_owner(self):
        # Two sources naming the same application would otherwise both route
        # it into the same mix, summing to roughly +6 dB, and neither fader
        # would appear to work.
        srcs = {
            "a": {"match_app_names": ["Spotify"]},
            "b": {"match_app_names": ["spotify"]},
        }
        claims = claim_streams(srcs, {1: stream(app_name="Spotify")})
        self.assertEqual(sum(len(v) for v in claims.values()), 1)

    def test_ownership_is_stable_across_calls(self):
        # Ownership that flipped between polls would thrash the loopbacks.
        srcs = {
            "a": {"match_app_names": ["Spotify"]},
            "b": {"match_app_names": ["spotify"]},
        }
        streams = {1: stream(app_name="Spotify")}
        first = claim_streams(srcs, streams)
        for _ in range(5):
            self.assertEqual(claim_streams(srcs, streams), first)

    def test_a_named_source_beats_the_catch_all(self):
        srcs = {
            "system": {"match_app_names": ["gnome-shell"], "catch_all": True},
            "music": {"match_app_names": ["Spotify"]},
        }
        claims = claim_streams(srcs, {1: stream(app_name="Spotify")})
        self.assertEqual(claims["music"], {1})
        self.assertEqual(claims["system"], set())

    def test_the_catch_all_takes_what_nothing_else_named(self):
        srcs = {
            "system": {"match_app_names": ["gnome-shell"], "catch_all": True},
            "music": {"match_app_names": ["Spotify"]},
        }
        claims = claim_streams(srcs, {1: stream(app_name="SomeUnknownGame")})
        self.assertEqual(claims["system"], {1})

    def test_without_a_catch_all_an_unmatched_stream_is_unowned(self):
        srcs = {"music": {"match_app_names": ["Spotify"]}}
        claims = claim_streams(srcs, {1: stream(app_name="Nothing")})
        self.assertEqual(sum(len(v) for v in claims.values()), 0)

    def test_every_source_gets_an_entry(self):
        # Callers index the result directly; a missing key would be a KeyError
        # on the routing path.
        srcs = {"a": {"match_app_names": ["X"]}, "b": {}}
        claims = claim_streams(srcs, {})
        self.assertEqual(set(claims), {"a", "b"})


if __name__ == "__main__":
    unittest.main()
