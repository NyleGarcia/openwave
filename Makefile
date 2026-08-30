PREFIX ?= /usr/local
DESTDIR ?=
PYTHON ?= python3

BINDIR = $(DESTDIR)$(PREFIX)/bin
DATADIR = $(DESTDIR)$(PREFIX)/share
APPDIR = $(DATADIR)/openwave
DESKTOPDIR = $(DATADIR)/applications
DOCDIR = $(DATADIR)/doc/openwave
LICENSEDIR = $(DATADIR)/licenses/openwave

SITEPKG := $(shell $(PYTHON) -c "import site; print(site.getsitepackages()[0])")
PYPREFIX := $(shell $(PYTHON) -c "import sys; print(sys.prefix)")

.PHONY: install uninstall check-prefix

install: check-prefix
	install -dm755 $(DESTDIR)$(SITEPKG)/wavexlr
	install -m644 $(wildcard wavexlr/*.py) wavexlr/style.css $(DESTDIR)$(SITEPKG)/wavexlr/
	install -dm755 $(BINDIR)
	printf '#!/bin/sh\nexec %s -m wavexlr "$$@"\n' "$(PYTHON)" > $(BINDIR)/openwave
	chmod 755 $(BINDIR)/openwave
	printf '#!/bin/sh\nexec %s -m wavexlr.daemon "$$@"\n' "$(PYTHON)" > $(BINDIR)/openwave-daemon
	chmod 755 $(BINDIR)/openwave-daemon
	install -Dm644 wavexlr.desktop $(DESKTOPDIR)/openwave.desktop
	install -Dm644 openwave-autostart.desktop $(APPDIR)/openwave-autostart.desktop
	install -Dm644 wireplumber/51-openwave-wave-xlr.conf $(APPDIR)/wireplumber/51-openwave-wave-xlr.conf
	install -Dm644 pipewire/52-openwave-mixes.conf $(APPDIR)/pipewire/52-openwave-mixes.conf
	install -Dm644 README.md $(DOCDIR)/README.md
	install -Dm644 LICENSE $(LICENSEDIR)/LICENSE

uninstall:
	rm -rf $(DESTDIR)$(SITEPKG)/wavexlr
	rm -f $(BINDIR)/openwave
	rm -f $(BINDIR)/openwave-daemon
	rm -f $(DESKTOPDIR)/openwave.desktop
	rm -rf $(APPDIR)
	rm -rf $(DOCDIR)
	rm -rf $(LICENSEDIR)

# site-packages is chosen by the interpreter and is an absolute path: it does
# not move when PREFIX does. If PREFIX is not above it, wavexlr/ and
# share/openwave/ land under different prefixes and nothing above the installed
# module is PREFIX, so paths.py can only find the data through its fallback
# list. That still works, but the install is not self-describing -- warn, do
# not fail, because a staged DESTDIR tree or a store path may mean it.
check-prefix:
	@case '$(SITEPKG)' in \
	  '$(PREFIX)'/*) ;; \
	  *) printf '\033[1;33mwarning:\033[0m PREFIX=%s, but this interpreter installs modules to\n' '$(PREFIX)' >&2; \
	     printf '         %s (prefix %s).\n' '$(SITEPKG)' '$(PYPREFIX)' >&2; \
	     printf '         wavexlr/ and share/openwave/ will land under different prefixes;\n' >&2; \
	     printf '         paths.py finds the data only via its fallback list.\n' >&2; \
	     printf '         Use PREFIX=%s to keep the install self-consistent.\n' '$(PYPREFIX)' >&2 ;; \
	esac
