"""Diagnostics export: everything a bug report needs, in one text file.

    python3 -m wavexlr.diag [--full] [-o FILE]

Every section is collected in isolation: a source that is missing, hung or
broken produces a line saying so, never a traceback and never a hang — the
bundle a reporter attaches when something is wrong must survive everything
being wrong.

Privacy: the default bundle carries no config contents and no stream lists;
running application names are personal. --full includes them, and says so in
the header. Device serials stay — they are how hardware reports are matched.

The firmware serves vendor transfers to one process at a time, so the device
section reads through a fresh handle only when OpenWave is closed; run the
in-app export otherwise.
"""

import argparse
import glob
import json
import os
import subprocess
import sys
import time
import traceback

TIMEOUT = 3


def _run(*argv):
    """stdout of a command, or a one-line failure note."""
    try:
        r = subprocess.run(argv, capture_output=True, text=True,
                           timeout=TIMEOUT)
    except FileNotFoundError:
        return f"({argv[0]}: not found)"
    except subprocess.TimeoutExpired:
        return f"({argv[0]}: timed out after {TIMEOUT}s)"
    out = r.stdout.strip()
    if r.returncode != 0:
        err = (r.stderr or "").strip().splitlines()
        return f"({argv[0]}: exit {r.returncode}{': ' + err[0] if err else ''})"
    return out


def _hexdump(data):
    lines = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hexs = " ".join(f"{b:02x}" for b in chunk)
        text = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {i:04x}  {hexs:<47}  {text}")
    return "\n".join(lines)


# --- Collectors. Each returns a string; assemble() isolates failures. ---


def collect_versions():
    lines = [f"python: {sys.version.split()[0]} ({sys.executable})"]
    try:
        with open("/etc/os-release") as f:
            for line in f:
                if line.startswith("PRETTY_NAME="):
                    lines.append("distro: " + line.split("=", 1)[1].strip().strip('"'))
    except OSError:
        lines.append("distro: unknown (/etc/os-release unreadable)")
    lines.append("pipewire: " + _run("pipewire", "--version").splitlines()[-1])
    lines.append("wireplumber: " + _run("wireplumber", "--version").splitlines()[-1])
    return "\n".join(lines)


def collect_usb():
    """Which supported devices sysfs sees — no USB permissions needed."""
    from .profiles import PROFILES
    present = {}
    for entry in glob.glob("/sys/bus/usb/devices/*"):
        try:
            with open(os.path.join(entry, "idVendor")) as f:
                vid = f.read().strip()
            with open(os.path.join(entry, "idProduct")) as f:
                pid = f.read().strip()
        except OSError:
            continue
        present[(vid, pid)] = True
    lines = []
    for p in PROFILES:
        seen = (f"{p.vid:04x}", f"{p.pid:04x}") in present
        lines.append(f"{p.display_name} ({p.vid:04x}:{p.pid:04x}): "
                     + ("present" if seen else "absent"))
    return "\n".join(lines)


def describe_device(dev):
    """Profile, devinfo and a config hexdump for one open device."""
    p = dev.profile
    lines = [f"profile: {p.display_name} ({p.vid:04x}:{p.pid:04x})"
             + (f" at {dev.usbbus}" if dev.usbbus else ""),
             f"alsa card: {dev._card}"]
    try:
        info = dev.read_device_info()
        lines.append(f"firmware: {info['fw_version']}  "
                     f"api: {info['api_version']}  serial: {info['serial']}")
    except Exception as e:
        lines.append(f"devinfo: unreadable ({e})")
    try:
        lines.append(f"config ({p.config_len} bytes expected):")
        lines.append(_hexdump(dev.read_config()))
    except Exception as e:
        lines.append(f"config: unreadable ({e})")
    return lines


def collect_device():
    """Every supported device, each through a fresh handle in turn."""
    from .device import WaveDevice, scan
    units = scan()
    if not units:
        return "no supported device on the bus"
    lines = []
    for profile, bus, addr in units:
        dev = WaveDevice()
        try:
            dev.connect(profile, bus, addr)
        except RuntimeError as e:
            lines.append(f"{profile.display_name} at {bus:03d}/{addr:03d}: "
                         f"could not open ({e})")
            continue
        try:
            lines.extend(describe_device(dev))
        finally:
            dev.disconnect()
        lines.append("")
    if any("unreadable" in line or "could not open" in line for line in lines):
        lines.append("(a device was seen but reads failed: OpenWave is "
                     "probably running and holds the one handle the "
                     "firmware serves — use the in-app export, or quit "
                     "OpenWave including the tray icon)")
    return "\n".join(lines).rstrip()


def collect_udev():
    from . import setup
    lines = [f"udev rules complete: {setup.udev_installed()}"]
    for path in (setup.UDEV_PATH, setup.UDEV_PATH_OLD):
        lines.append(f"{path}: "
                     + ("present" if os.path.exists(path) else "absent"))
    return "\n".join(lines)


def collect_service():
    from . import service
    return "\n".join([
        f"backend: {service.backend_name}",
        f"installed: {service.is_installed()}",
        f"running: {service.is_running()}",
        f"failed: {service.is_failed()}",
    ])


def collect_journal():
    from . import service
    if service.backend_name != "systemd":
        return f"(journal only collected on systemd; backend is {service.backend_name})"
    return _run("journalctl", "--user", "-u", "openwave",
                "-n", "100", "--no-pager")


def collect_pipewire(full=False):
    out = _run("pw-dump")
    if out.startswith("("):
        return out
    try:
        objects = json.loads(out)
    except ValueError as e:
        return f"(pw-dump output unparseable: {e})"
    lines = []
    for obj in objects:
        props = (obj.get("info") or {}).get("props") or {}
        name = props.get("node.name", "")
        if not name:
            continue
        ours = name.startswith("openwave_") or "Elgato" in name \
            or "Wave" in props.get("node.description", "")
        if not (ours or full):
            continue
        state = (obj.get("info") or {}).get("state", "?")
        lines.append(f"{name}  [{state}]  {props.get('node.description', '')}")
    header = "all nodes:" if full else "openwave / Elgato nodes:"
    return header + "\n" + ("\n".join(lines) or "(none)")


def collect_configs(full=False):
    from . import mixer, mixes, sources
    lines = []
    paths = {
        "sources.json": sources.CONFIG_PATH,
        "mixdefs.json": mixes.CONFIG_PATH,
        "mixes.json": mixer.CONFIG_PATH,
        "ui-state.json": os.path.expanduser("~/.config/openwave/ui-state.json"),
    }
    for name, path in paths.items():
        if not os.path.exists(path):
            lines.append(f"{name}: absent")
            continue
        size = os.path.getsize(path)
        try:
            with open(path) as f:
                body = f.read()
            json.loads(body)
            state = "parses"
        except (OSError, ValueError) as e:
            body, state = None, f"BROKEN ({e})"
        lines.append(f"{name}: {size} bytes, {state}")
        if full and body is not None:
            lines.append(body.rstrip())
    if not full:
        lines.append("(contents withheld — app names are personal; --full includes them)")
    return "\n".join(lines)


SECTIONS = (
    ("Versions", collect_versions),
    ("USB devices", collect_usb),
    ("Device", collect_device),
    ("udev", collect_udev),
    ("Service", collect_service),
    ("Journal", collect_journal),
    ("PipeWire", collect_pipewire),
    ("Config files", collect_configs),
)


def assemble(full=False, sections=SECTIONS):
    """The whole bundle as a string. No collector failure escapes."""
    stamp = time.strftime("%Y-%m-%d %H:%M:%S %z")
    out = [f"OpenWave diagnostics — {stamp}"
           + ("  (--full: includes config contents and all node names)"
              if full else "")]
    for title, collect in sections:
        out.append(f"\n== {title} ==")
        try:
            if collect in (collect_pipewire, collect_configs):
                out.append(collect(full=full))
            else:
                out.append(collect())
        except Exception:
            last = traceback.format_exc().strip().splitlines()[-1]
            out.append(f"unavailable ({last})")
    return "\n".join(out) + "\n"


def default_path():
    return os.path.abspath(
        time.strftime("openwave-diag-%Y%m%d-%H%M%S.txt"))


def main():
    parser = argparse.ArgumentParser(prog="python3 -m wavexlr.diag")
    parser.add_argument("--full", action="store_true",
                        help="include config contents and all node names")
    parser.add_argument("-o", "--output", default=None,
                        help="write here instead of ./openwave-diag-<stamp>.txt")
    args = parser.parse_args()
    path = args.output or default_path()
    with open(path, "w") as f:
        f.write(assemble(full=args.full))
    print(path)


if __name__ == "__main__":
    main()
