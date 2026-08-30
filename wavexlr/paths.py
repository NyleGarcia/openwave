"""Locating files that were installed alongside this package.

Everything here is resolved relative to ``__file__`` rather than assuming a
particular prefix. The Makefile honours PREFIX, so hardcoding /usr and
/usr/local only works for two of the possible installs and silently fails for
the rest -- a store path, a --prefix=$HOME/.local install, a virtualenv, or a
staged DESTDIR tree. The failure mode is bad, too: setup.py reports "source not
found" and aborts the rest of first-run setup.

Layout the Makefile produces for a given PREFIX:

    <prefix>/bin/openwave
    <prefix>/bin/openwave-daemon
    <prefix>/<sitepackages>/wavexlr/*.py      <- __file__ is in here
    <prefix>/share/openwave/wireplumber/...
    <prefix>/share/openwave/pipewire/...

<sitepackages> is not a fixed depth (lib/python3.13/site-packages,
lib64/python3.13/site-packages, ...), so walk up until the expected
subdirectory appears instead of counting levels.

The walk alone is not enough, though, because that layout is a fiction: the
Makefile takes <sitepackages> from the interpreter, as an absolute path, so it
does not move when PREFIX does. Installing with the Makefile's own default
PREFIX=/usr/local against a distribution whose site-packages is
/usr/lib/python3.N/site-packages puts the module under /usr and its data under
/usr/local, and no ancestor of the module is ever /usr/local. The walk comes up
empty and first-run setup dies on a rule it did install, just not where it
looked. So try the usual prefixes too once the walk has failed.
"""

import os
import sys

_MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
_MAX_DEPTH = 8

# Tried only after the walk above, so an install that is self-consistent still
# resolves against its own prefix first: the running interpreter's prefix, then
# the two that the Makefile and install.sh actually default to.
_FALLBACK_PREFIXES = (
    sys.prefix,
    getattr(sys, "base_prefix", sys.prefix),
    "/usr",
    "/usr/local",
)


def _ancestors():
    """This package's directory and its parents, nearest first."""
    d = _MODULE_DIR
    for _ in range(_MAX_DEPTH):
        yield d
        parent = os.path.dirname(d)
        if parent == d:
            return
        d = parent


def _prefixes():
    """Every prefix worth looking under, nearest first, without repeats.

    The filesystem root is not one of them. Walking up always arrives there
    eventually, and on a merged-usr system /bin and /lib exist, so a root that
    was never anybody's PREFIX would answer for every lookup and the
    "prefers its own prefix" rule above would stop meaning anything.
    """
    root = os.path.abspath(os.sep)
    seen = set()
    for d in list(_ancestors()) + list(_FALLBACK_PREFIXES):
        if d and d != root and d not in seen:
            seen.add(d)
            yield d


def data_file(*parts):
    """Return an installed data file's path, or None if it is not present.

    Checked in order: a source checkout, where the data directories sit beside
    the package rather than under share/; then <prefix>/share/openwave for
    every plausible prefix above this module; then the fallback prefixes, which
    is what a PREFIX that does not contain site-packages needs.
    """
    rel = os.path.join(*parts)

    candidates = [os.path.join(os.path.dirname(_MODULE_DIR), rel)]
    candidates += [
        os.path.join(d, "share", "openwave", rel) for d in _prefixes()
    ]

    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None


def bin_file(name):
    """Return the path to one of our installed launchers, or None.

    Prefers the copy under this package's own prefix so a service unit keeps
    pointing at the install it was generated from, rather than whichever one
    happens to be first on PATH later. Falls back to the same prefixes as
    data_file, for the same reason: bin/ follows PREFIX and this module does
    not have to.
    """
    for d in _prefixes():
        candidate = os.path.join(d, "bin", name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    return None
