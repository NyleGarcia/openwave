"""Elgato Wave USB device backend.

Uses raw libusb control transfers with wIndex=0x3303 to bypass the Linux
kernel's interface routing. The kernel sees interface 3 (unclaimed) and
lets the transfer through, while the firmware only checks the 0x33 prefix.
No driver detach needed — audio is never interrupted.

Per-model constants (USB IDs, config offsets, capabilities) live in
profiles.py; connect() picks the first supported device found.
"""

import ctypes
import glob
import os
import ctypes.util
import re
import struct
import subprocess
import threading

from .profiles import PROFILES

BREQUEST_READ = 0x85
BREQUEST_WRITE = 0x05

RT_CLASS_IN = 0xA1
RT_CLASS_OUT = 0x21

# --- Raw libusb setup ---
_lib_path = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0"
_lib = ctypes.CDLL(_lib_path)

_lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
_lib.libusb_init.restype = ctypes.c_int
_lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
_lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
_lib.libusb_close.argtypes = [ctypes.c_void_p]
_lib.libusb_close.restype = None
_lib.libusb_control_transfer.argtypes = [
    ctypes.c_void_p, ctypes.c_uint8, ctypes.c_uint8,
    ctypes.c_uint16, ctypes.c_uint16,
    ctypes.POINTER(ctypes.c_ubyte), ctypes.c_uint16, ctypes.c_uint,
]
_lib.libusb_control_transfer.restype = ctypes.c_int

_lib.libusb_get_device_list.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p))]
_lib.libusb_get_device_list.restype = ctypes.c_ssize_t
_lib.libusb_free_device_list.argtypes = [
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
_lib.libusb_free_device_list.restype = None
_lib.libusb_get_bus_number.argtypes = [ctypes.c_void_p]
_lib.libusb_get_bus_number.restype = ctypes.c_uint8
_lib.libusb_get_device_address.argtypes = [ctypes.c_void_p]
_lib.libusb_get_device_address.restype = ctypes.c_uint8
_lib.libusb_open.argtypes = [ctypes.c_void_p,
                             ctypes.POINTER(ctypes.c_void_p)]
_lib.libusb_open.restype = ctypes.c_int


class _DeviceDescriptor(ctypes.Structure):
    _fields_ = [
        ("bLength", ctypes.c_uint8),
        ("bDescriptorType", ctypes.c_uint8),
        ("bcdUSB", ctypes.c_uint16),
        ("bDeviceClass", ctypes.c_uint8),
        ("bDeviceSubClass", ctypes.c_uint8),
        ("bDeviceProtocol", ctypes.c_uint8),
        ("bMaxPacketSize0", ctypes.c_uint8),
        ("idVendor", ctypes.c_uint16),
        ("idProduct", ctypes.c_uint16),
        ("bcdDevice", ctypes.c_uint16),
        ("iManufacturer", ctypes.c_uint8),
        ("iProduct", ctypes.c_uint8),
        ("iSerialNumber", ctypes.c_uint8),
        ("bNumConfigurations", ctypes.c_uint8),
    ]


_lib.libusb_get_device_descriptor.argtypes = [
    ctypes.c_void_p, ctypes.POINTER(_DeviceDescriptor)]
_lib.libusb_get_device_descriptor.restype = ctypes.c_int

_ctx = ctypes.c_void_p()
_lib.libusb_init(ctypes.byref(_ctx))


def _each_usb_device(visit):
    """Call visit(vid, pid, bus, addr, dev_ptr) for every device on the bus.

    The device list is freed before returning, so visit must open (ref) a
    device it wants to keep, not stash the pointer.
    """
    devs = ctypes.POINTER(ctypes.c_void_p)()
    count = _lib.libusb_get_device_list(_ctx, ctypes.byref(devs))
    if count < 0:
        return
    try:
        desc = _DeviceDescriptor()
        for i in range(count):
            dev = devs[i]
            if _lib.libusb_get_device_descriptor(dev, ctypes.byref(desc)) != 0:
                continue
            visit(desc.idVendor, desc.idProduct,
                  _lib.libusb_get_bus_number(dev),
                  _lib.libusb_get_device_address(dev), dev)
    finally:
        _lib.libusb_free_device_list(devs, 1)


def scan():
    """Every supported Wave on the bus: [(profile, bus, addr)], bus order.

    connect() opens only the first device of a vid:pid, which made a second
    identical model invisible; this is how a caller sees them all.
    """
    by_id = {(p.vid, p.pid): p for p in PROFILES}
    found = []

    def visit(vid, pid, bus, addr, _dev):
        profile = by_id.get((vid, pid))
        if profile is not None:
            found.append((profile, bus, addr))

    _each_usb_device(visit)
    return sorted(found, key=lambda e: (e[1], e[2]))


def _find_card(matches, vid=None, pid=None, usbbus=None):
    """ALSA card number for a device.

    Matched on /proc/asound/card*/usbid, which is the device's vid:pid, rather
    than on names. Name matching was ambiguous the moment two Elgato devices
    were connected: every profile's match list ends in "Elgato", so all three
    resolved to whichever Elgato card came first, and OpenWave would read one
    device over USB while driving the other's ALSA controls.

    usbbus ("bus/device") disambiguates two of the SAME model, where vid:pid
    alone cannot.
    """
    if vid is not None and pid is not None:
        want = f"{vid:04x}:{pid:04x}"
        for path in sorted(glob.glob("/proc/asound/card*/usbid")):
            try:
                with open(path) as f:
                    if f.read().strip().lower() != want:
                        continue
                if usbbus is not None:
                    bus_path = os.path.join(os.path.dirname(path), "usbbus")
                    try:
                        with open(bus_path) as f:
                            if f.read().strip() != usbbus:
                                continue
                    except OSError:
                        pass
            except OSError:
                continue
            digits = "".join(c for c in os.path.basename(os.path.dirname(path))
                             if c.isdigit())
            if digits:
                return digits

        # A vid:pid was given and /proc/asound was readable, so "no match"
        # means the device is not present -- not that we should guess. Falling
        # through to the name match here is what made an absent Wave:3 resolve
        # to a connected Dock, because every match list ends in "Elgato".
        if glob.glob("/proc/asound/card*/usbid"):
            return None

    # Name matching only when /proc/asound is unreadable at all.
    try:
        r = subprocess.run(["aplay", "-l"], capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            if any(m in line for m in matches):
                return line.split(":")[0].split()[-1]
    except Exception:
        pass
    return None


def _amixer(card, *args):
    """Run amixer and return stdout."""
    try:
        r = subprocess.run(
            ["amixer", "-c", card, *args],
            capture_output=True, text=True, timeout=3,
        )
        return r.stdout
    except Exception:
        return ""


def _alsa_get(card):
    """Read ALSA mute and HP volume."""
    state = {}
    out = _amixer(card, "cget", f"numid={_numid(card, 'mute')}")
    state["mute"] = ": values=off" in out
    # HP volume — raw ALSA value 0-120
    out = _amixer(card, "cget", f"numid={_numid(card, 'hp_vol')}")
    for line in out.splitlines():
        if ": values=" in line:
            try:
                state["hp_vol"] = int(line.split("=")[-1])
            except ValueError:
                pass
    return state


def present_units():
    """{(vid, pid, "bus/addr")} for every supported Wave on the bus.

    Sysfs only — no USB permissions, no enumeration — cheap enough for a
    periodic tick. The bus/addr string matches WaveDevice.usbbus, so the
    caller can diff this against what it holds open and notice a unit
    appearing or vanishing while others stay connected.
    """
    wanted = {(f"{p.vid:04x}", f"{p.pid:04x}") for p in PROFILES}
    base = "/sys/bus/usb/devices"
    units = set()
    try:
        entries = os.listdir(base)
    except OSError:
        return units
    for entry in entries:
        try:
            with open(os.path.join(base, entry, "idVendor")) as f:
                vid = f.read().strip()
            with open(os.path.join(base, entry, "idProduct")) as f:
                pid = f.read().strip()
            if (vid, pid) not in wanted:
                continue
            with open(os.path.join(base, entry, "busnum")) as f:
                bus = int(f.read().strip())
            with open(os.path.join(base, entry, "devnum")) as f:
                addr = int(f.read().strip())
        except (OSError, ValueError):
            continue
        units.add((vid, pid, f"{bus:03d}/{addr:03d}"))
    return units


def wave_present():
    """True when any supported Wave is on the USB bus. Sysfs only -- no USB
    permissions, no enumeration, cheap enough for a 2 s reconnect tick."""
    from .profiles import PROFILES
    wanted = {(f"{p.vid:04x}", f"{p.pid:04x}") for p in PROFILES}
    base = "/sys/bus/usb/devices"
    try:
        entries = os.listdir(base)
    except OSError:
        return False
    for entry in entries:
        try:
            with open(os.path.join(base, entry, "idVendor")) as f:
                vid = f.read().strip()
            with open(os.path.join(base, entry, "idProduct")) as f:
                pid = f.read().strip()
        except OSError:
            continue
        if (vid, pid) in wanted:
            return True
    return False


# ALSA control name suffix -> role. The numids 4/5/6 hold on the hardware in
# hand but are not promised across firmware revisions or models; the control
# NAMES vary only in their product-string prefix ("PCM Playback Volume",
# "Mic Capture Switch" on the XLR Dock), so the suffix is the stable handle.
# Ported from CryoByte33/openwave and verified against a live 0fd9:00a6 Dock,
# where discovery resolves to exactly the numbers below.
_ALSA_ROLE_SUFFIX = {
    "Capture Switch": "mute",
    "Capture Volume": "gain",
    "Playback Volume": "hp_vol",
}
_ALSA_ROLE_FALLBACK = {"mute": 5, "gain": 6, "hp_vol": 4}
_ALSA_NUMIDS = {}  # card -> {role: numid}, cached like the maxima below


def _discover_numids(card):
    """{role: numid} scanned from `amixer contents`, by control-name suffix.

    One pass also feeds the max cache, so discovery costs no extra calls.
    Anything not found falls back to the historical hardcoded numid, so a
    device this has never seen behaves exactly as before.
    """
    if card in _ALSA_NUMIDS:
        return _ALSA_NUMIDS[card]
    found = {}
    cur_id = cur_name = None
    for line in _amixer(card, "contents").splitlines():
        stripped = line.strip()
        m = re.match(r"numid=(\d+),iface=(\w+),name='(.*)'", stripped)
        if m:
            cur_id, iface, cur_name = int(m.group(1)), m.group(2), m.group(3)
            if iface != "MIXER":
                cur_id = cur_name = None
            continue
        if cur_id is not None and stripped.startswith("; type="):
            role = next((r for suffix, r in _ALSA_ROLE_SUFFIX.items()
                         if cur_name.endswith(suffix)), None)
            if role and role not in found:
                found[role] = cur_id
                m = re.search(r",max=(-?\d+)", stripped)
                if m:
                    _ALSA_CTL_MAX[(card, cur_id)] = int(m.group(1))
    _ALSA_NUMIDS[card] = found
    return found


def _numid(card, role):
    """The numid carrying a role on this card, discovered or historical."""
    return _discover_numids(card).get(role, _ALSA_ROLE_FALLBACK[role])


# Control ranges differ per device and per kernel driver, so they are read
# from the driver rather than assumed. Cached: they cannot change for a card.
_ALSA_CTL_MAX = {}


def _alsa_ctl_max(card, numid, fallback):
    """The highest value a control accepts, per the driver."""
    key = (card, numid)
    if key not in _ALSA_CTL_MAX:
        match = re.search(r",max=(-?\d+)", _amixer(card, "cget", f"numid={numid}"))
        _ALSA_CTL_MAX[key] = int(match.group(1)) if match else fallback
    return _ALSA_CTL_MAX[key]


def _alsa_set_mute(card, muted):
    _amixer(card, "cset", f"numid={_numid(card, 'mute')}",
            "off" if muted else "on")


def _alsa_set_hp_vol(card, value):
    """Set ALSA HP volume, clamped to the control's real range."""
    numid = _numid(card, "hp_vol")
    top = _alsa_ctl_max(card, numid, 120)
    _amixer(card, "cset", f"numid={numid}", str(max(0, min(top, value))))


def _alsa_set_gain(card, value):
    """Set ALSA mic gain, clamped to the control's real range."""
    numid = _numid(card, "gain")
    top = _alsa_ctl_max(card, numid, 150)
    _amixer(card, "cset", f"numid={numid}", str(max(0, min(top, value))))


def _fw_gain_to_alsa(fw_gain_raw, scale):
    """Map firmware gain (raw / scale dB) to ALSA steps of 0.5 dB.

    The upper clamp belongs to the setter, which knows the control's real
    range. Clamping here to a constant silently halved any gain above 40 dB
    on a device whose control goes higher.
    """
    return max(0, round((fw_gain_raw / scale) / 0.5))


def _fw_hp_to_alsa(fw_hp_raw, scale):
    """Map firmware HP to ALSA (0-120).

    Firmware: raw / scale dB (XLR: int16 Q8.8, Wave:3: int8 whole-dB).
    ALSA driver caps lower at 0 → -60 dB; anything below saturates.
    ALSA step = 0.5 dB, so dB = (value - 120) * 0.5 → value = dB / 0.5 + 120.
    """
    db = fw_hp_raw / scale
    return max(0, min(120, round(db / 0.5 + 120)))


def _alsa_hp_to_fw(alsa_hp, scale):
    """Map ALSA HP (0-120) to firmware HP raw."""
    db = (alsa_hp - 120) * 0.5  # 0→-60, 120→0
    db = max(-128.0, min(0.0, db))  # firmware range
    return int(db * scale)


class WaveDevice:
    def __init__(self):
        self._handle = None
        self._lock = threading.Lock()
        self._card = None
        self._last_fw = None  # last known firmware state for change detection
        self.profile = None
        self.usbbus = None    # "bus/addr" when opened via scan()
        self.info = {}        # devinfo cache: fw/api/serial, filled by caller
        self._alsa_tick = 0   # decimates the amixer reads inside get_all()

    @property
    def connected(self):
        return self._handle is not None

    def connect(self, profile=None, bus=None, addr=None):
        """Open a Wave. With no arguments: the first supported device found.

        With (profile, bus, addr) from scan(): that specific unit — which is
        what lets two devices, even of the same model, each get their own
        handle. bus/addr also pin the ALSA card via /proc/asound usbbus, so
        two of one model cannot end up sharing a card either.
        """
        if profile is not None and bus is not None:
            handle = self._open_at(profile, bus, addr)
            if handle:
                self._handle = handle
                self.profile = profile
                self.usbbus = f"{bus:03d}/{addr:03d}"
                self._card = _find_card(
                    profile.card_match, vid=profile.vid, pid=profile.pid,
                    usbbus=self.usbbus,
                )
                return
            raise RuntimeError(
                f"Could not open {profile.display_name} at {bus:03d}/{addr:03d}")

        for prof in PROFILES:
            handle = _lib.libusb_open_device_with_vid_pid(
                _ctx, prof.vid, prof.pid)
            if handle:
                self._handle = handle
                self.profile = prof
                self._card = _find_card(
                    prof.card_match, vid=prof.vid, pid=prof.pid,
                )
                return
        raise RuntimeError("No supported Elgato Wave device found")

    @staticmethod
    def _open_at(profile, bus, addr):
        """A handle for the unit at (bus, addr), or None."""
        handle = ctypes.c_void_p()

        def visit(vid, pid, dbus, daddr, dev):
            if handle.value:
                return
            if (vid, pid) == (profile.vid, profile.pid) \
                    and (dbus, daddr) == (bus, addr):
                opened = ctypes.c_void_p()
                if _lib.libusb_open(dev, ctypes.byref(opened)) == 0:
                    handle.value = opened.value

        _each_usb_device(visit)
        return handle.value and handle

    def disconnect(self):
        # Under the transfer lock: closing a handle another thread is mid-
        # control-transfer on is a use-after-free inside libusb. The poll
        # worker and the device watch both touch devices concurrently now,
        # so the close must wait its turn like any other USB operation.
        with self._lock:
            if self._handle:
                _lib.libusb_close(self._handle)
                self._handle = None
        self._card = None
        self._last_fw = None
        self.usbbus = None

    def _ctrl_read(self, wValue, length):
        """USB control read — no detach needed."""
        buf = (ctypes.c_ubyte * length)()
        with self._lock:
            # Checked INSIDE the lock: a multi-transfer operation releases it
            # between transfers, and a disconnect (unplug handling) can slot
            # in there. libusb does not NULL-check the handle — passing the
            # cleared one was a hard SEGV, not an error return.
            if self._handle is None:
                raise RuntimeError("device disconnected")
            ret = _lib.libusb_control_transfer(
                self._handle, RT_CLASS_IN, BREQUEST_READ, wValue, self.profile.windex,
                buf, length, 1000,
            )
        if ret < 0:
            raise RuntimeError(f"USB read failed (err {ret})")
        return bytearray(buf[:ret])

    def _ctrl_write(self, wValue, data):
        """USB control write — no detach needed."""
        data = bytes(data)
        buf = (ctypes.c_ubyte * len(data))(*data)
        with self._lock:
            if self._handle is None:
                raise RuntimeError("device disconnected")
            ret = _lib.libusb_control_transfer(
                self._handle, RT_CLASS_OUT, BREQUEST_WRITE, wValue, self.profile.windex,
                buf, len(data), 1000,
            )
        if ret < 0:
            raise RuntimeError(f"USB write failed (err {ret})")

    def read_config(self):
        return self._ctrl_read(self.profile.wvalue_config, self.profile.config_len)

    def write_config(self, config):
        self._ctrl_write(self.profile.wvalue_config, config)

    def read_meters(self):
        data = self._ctrl_read(self.profile.wvalue_meter, self.profile.meter_len)
        left = struct.unpack_from('<I', data, 0)[0]
        right = struct.unpack_from('<I', data, 4)[0]
        return left, right

    def read_device_info(self):
        """Read and parse the device info block."""
        p = self.profile
        data = self._ctrl_read(p.wvalue_devinfo, p.devinfo_len)
        serial = bytes(data[p.devinfo_serial[0]:p.devinfo_serial[1]]).decode(
            'ascii', errors='replace').rstrip('\x00')
        return {
            "api_version": f"{data[p.devinfo_api[0]]}.{data[p.devinfo_api[1]]}",
            "fw_version": f"{data[p.devinfo_fw[0]]}.{data[p.devinfo_fw[1]]}.{data[p.devinfo_fw[2]]}",
            "serial": serial,
        }

    # --- High-level getters ---

    def get_gain_raw(self):
        return struct.unpack_from('<H', self.read_config(), self.profile.off_gain)[0]

    def get_mute(self):
        return bool(self.read_config()[self.profile.off_mute])

    def get_hp_volume_db(self):
        p = self.profile
        raw = struct.unpack_from(p.hp_fmt, self.read_config(), p.off_hp_vol)[0]
        return raw / p.hp_scale

    def get_phantom(self):
        """48 V phantom power state, or None on a device without it."""
        if self.profile.off_phantom is None:
            return None
        return bool(self.read_config()[self.profile.off_phantom])

    def get_low_impedance(self):
        if self.profile.off_low_z is None:
            return None
        return bool(self.read_config()[self.profile.off_low_z])

    def get_volume_select(self):
        if self.profile.off_vol_select is None:
            return None
        val = self.read_config()[self.profile.off_vol_select]
        return self.profile.vol_select_map.get(val, "gain")

    def get_monitor_mix(self):
        if self.profile.off_monitor_mix is None:
            return None
        return struct.unpack_from('<H', self.read_config(), self.profile.off_monitor_mix)[0]

    def get_all(self):
        p = self.profile
        config = self.read_config()
        fw_gain = struct.unpack_from('<H', config, p.off_gain)[0]
        fw_hp = struct.unpack_from(p.hp_fmt, config, p.off_hp_vol)[0]
        fw_mute = bool(config[p.off_mute])

        fw_now = {"mute": fw_mute, "gain": fw_gain, "hp": fw_hp}

        # Sync firmware ↔ ALSA
        if self._card:
            # The firmware→ALSA direction below costs nothing while nothing
            # changed, but reading ALSA back is two amixer subprocesses per
            # call — at 10 Hz across two devices that was forty forks a
            # second for values that almost never move. Read every 5th poll
            # (0.5 s): pavucontrol moving the mic is still picked up
            # promptly, and the physical controls keep their 10 Hz path.
            self._alsa_tick = (self._alsa_tick + 1) % 5
            read_alsa = self._alsa_tick == 0 or self._last_fw is None
            alsa = _alsa_get(self._card) if read_alsa else {}
            dirty = False  # whether we need to write config back

            if self._last_fw is not None:
                # --- Mute ---
                if p.sync_alsa_mute:
                    if fw_mute != self._last_fw["mute"]:
                        _alsa_set_mute(self._card, fw_mute)
                    elif alsa.get("mute") is not None and alsa["mute"] != fw_mute:
                        config[p.off_mute] = 0x01 if alsa["mute"] else 0x00
                        fw_mute = alsa["mute"]
                        dirty = True

                # --- HP volume ---
                if p.sync_alsa_hp:
                    if fw_hp != self._last_fw["hp"]:
                        _alsa_set_hp_vol(self._card, _fw_hp_to_alsa(fw_hp, p.hp_scale))
                    elif "hp_vol" in alsa and alsa["hp_vol"] != _fw_hp_to_alsa(self._last_fw["hp"], p.hp_scale):
                        fw_hp = _alsa_hp_to_fw(alsa["hp_vol"], p.hp_scale)
                        struct.pack_into(p.hp_fmt, config, p.off_hp_vol, fw_hp)
                        dirty = True

                # --- Gain (push only: ALSA writes mirror back into firmware) ---
                if p.sync_alsa_gain:
                    if fw_gain != self._last_fw["gain"]:
                        _alsa_set_gain(self._card, _fw_gain_to_alsa(fw_gain, p.gain_scale))

            else:
                # First poll — sync firmware state to ALSA
                if p.sync_alsa_mute:
                    _alsa_set_mute(self._card, fw_mute)
                if p.sync_alsa_hp:
                    _alsa_set_hp_vol(self._card, _fw_hp_to_alsa(fw_hp, p.hp_scale))
                if p.sync_alsa_gain:
                    _alsa_set_gain(self._card, _fw_gain_to_alsa(fw_gain, p.gain_scale))

            if dirty:
                self.write_config(config)

            self._last_fw = {"mute": fw_mute, "gain": fw_gain, "hp": fw_hp}
        else:
            self._last_fw = fw_now

        state = {
            "gain_raw": fw_gain,
            "mute": fw_mute,
            "hp_volume_db": fw_hp / p.hp_scale,
        }
        if p.off_vol_select is not None:
            state["volume_select"] = p.vol_select_map.get(config[p.off_vol_select], "gain")
        if p.off_low_z is not None:
            state["low_impedance"] = bool(config[p.off_low_z])
        if p.off_phantom is not None:
            state["phantom"] = bool(config[p.off_phantom])
        if p.off_monitor_mix is not None:
            state["monitor_mix"] = struct.unpack_from('<H', config, p.off_monitor_mix)[0]
        return state

    # --- High-level setters (read-modify-write) ---

    def set_gain_raw(self, value):
        value = max(0, min(0xFFFF, value))
        config = self.read_config()
        struct.pack_into('<H', config, self.profile.off_gain, value)
        self.write_config(config)
        if self._last_fw:
            self._last_fw["gain"] = value
        if self._card and self.profile.sync_alsa_gain:
            _alsa_set_gain(self._card, _fw_gain_to_alsa(value, self.profile.gain_scale))

    def set_mute(self, muted):
        config = self.read_config()
        config[self.profile.off_mute] = 0x01 if muted else 0x00
        self.write_config(config)
        if self._last_fw:
            self._last_fw["mute"] = muted
        if self._card and self.profile.sync_alsa_mute:
            _alsa_set_mute(self._card, muted)

    def set_hp_volume_db(self, db):
        p = self.profile
        db = max(-128.0, min(0.0, db))
        raw = int(db * p.hp_scale)
        config = self.read_config()
        struct.pack_into(p.hp_fmt, config, p.off_hp_vol, raw)
        self.write_config(config)
        if self._last_fw:
            self._last_fw["hp"] = raw
        if self._card and p.sync_alsa_hp:
            _alsa_set_hp_vol(self._card, _fw_hp_to_alsa(raw, p.hp_scale))

    def set_phantom(self, enabled):
        """Switch 48 V phantom power.

        The only way to reach this on an XLR Dock, which has no controls at
        all: on a Wave XLR the dial toggles it, on the dock nothing does.
        """
        if self.profile.off_phantom is None:
            return
        config = self.read_config()
        config[self.profile.off_phantom] = 0x01 if enabled else 0x00
        self.write_config(config)

    def set_low_impedance(self, enabled):
        if self.profile.off_low_z is None:
            return
        config = self.read_config()
        config[self.profile.off_low_z] = 0x01 if enabled else 0x00
        self.write_config(config)

    def set_monitor_mix(self, value):
        p = self.profile
        if p.off_monitor_mix is None:
            return
        value = max(0, min(p.mix_max, int(value)))
        config = self.read_config()
        struct.pack_into('<H', config, p.off_monitor_mix, value)
        self.write_config(config)


WaveXLR = WaveDevice
