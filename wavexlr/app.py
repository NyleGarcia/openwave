"""OpenWave — GTK4 + Adwaita control application for Elgato Wave devices."""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, GObject, Gio, Gdk
import json
import logging
import os
import sys
import threading

from .device import WaveDevice
from .meter import MeterMonitor
from .mixer import (
    Mixer, list_output_sinks, default_sink_name, OUTPUT_AUTO, OUTPUT_NONE,
    claim_streams, stream_matches,
)
from .mixdialog import MixDialog
from .mixmatrix import MixMatrix
from .sourcedialog import AddSourceDialog
from . import paths, setup, service, sources as sources_module, mixes as mixes_module

logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

KNOB_LABELS = {"gain": "Gain", "hp": "Headphones", "mix": "Monitor Mix"}


def _slider_row(scale):
    """Put a Gtk.Scale inside a PreferencesGroup card.

    The scales were appended to the sidebar box rather than added to their
    group, so they rendered below the whole card -- visually detached from the
    row whose value they set, and ambiguous about which control they belonged
    to.
    """
    row = Adw.PreferencesRow(activatable=False, selectable=False)
    scale.set_margin_start(12)
    scale.set_margin_end(12)
    scale.set_margin_top(2)
    scale.set_margin_bottom(6)
    row.set_child(scale)
    return row


class WaveXLRWindow(Adw.ApplicationWindow):
    def __init__(self, **kwargs):
        # The old 1100x620 could not show its own content: the matrix alone
        # needs 260 for the source column plus 228 per mix, and the sidebar
        # another ~340, so three mixes overflowed the width by ~180px and the
        # height ran out at the sixth row. Sized for three mixes and eight rows
        # with headroom, then overridden by whatever size was last used.
        super().__init__(**kwargs, title="OpenWave",
                         default_width=1360, default_height=800)
        # Kept modest so the window still fits a small screen; the matrix
        # scrolls rather than being clipped.
        self.set_size_request(820, 480)
        self._restore_window_size()
        self.dev = WaveDevice()
        self._gain_max = 0x5000
        self._updating_ui = False
        self._last_state = None
        self._poll_id = None
        self._stream_poll_id = None
        self._device_poll_countdown = self._DEVICE_POLL_EVERY
        self._gain_timeout = None
        self._hp_timeout = None
        self._mix_timeout = None
        # Debounce slider events to coalesce a flurry of value-changed signals
        # during a drag into one set_cell. {(source_id, mix_id): timeout_id}.
        self._cell_debounce_ids = {}
        # One-shot re-read of the routing after a mix output change settles.
        self._output_refresh_id = None
        self._sources = sources_module.load_seeded()
        self._mixes = mixes_module.load_seeded()

        self._build_ui()
        self._restore_gain_lock()
        self._update_service_status()
        self.mixer = Mixer()
        self.mixer.set_mixes(self._mixes)
        self.mixer.set_sources(self._sources)
        self.mixer.start()
        # Re-evaluate now that mixer.hp is known: whether the capture fix is
        # needed at all depends on the card exposing a playback side, and the
        # first call above ran before the Mixer existed.
        self._update_service_status()
        # The capture snapshot is seeded by _do_start on the worker. Priming
        # it here would put a 5-second-timeout pw-dump on the GTK thread during
        # window construction; capture_device_present is fail-open, so an
        # unseeded snapshot draws rows live rather than dead in the meantime.
        self._refresh_outputs()
        self.meter = MeterMonitor()
        self._meter_targets = {}
        self._wire_matrix_cells()
        self._refresh_mix_emptiness()
        self._start_meters()
        self._start_stream_poll()
        self._try_connect()

    # Remembered across sessions: the right size depends on how many mixes and
    # sources the user keeps, which only they know.
    _UI_STATE = os.path.expanduser("~/.config/openwave/ui-state.json")

    def _on_gain_lock_toggled(self, btn):
        locked = btn.get_active()
        self.gain_scale.set_sensitive(not locked)
        btn.set_icon_name(
            "changes-prevent-symbolic" if locked else "changes-allow-symbolic"
        )
        btn.set_tooltip_text("Gain locked \u2014 click to unlock" if locked
                             else "Lock gain")
        self._save_ui_state()

    def _restore_gain_lock(self):
        state = self._load_ui_state()
        if state.get("gain_locked"):
            self.gain_lock.set_active(True)   # toggled fires and applies it

    def _load_ui_state(self):
        try:
            with open(self._UI_STATE) as f:
                state = json.load(f)
        except (OSError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _restore_window_size(self):
        state = self._load_ui_state()
        width, height = state.get("width"), state.get("height")
        if isinstance(width, int) and isinstance(height, int) \
                and width >= 820 and height >= 480:
            self.set_default_size(width, height)
        if state.get("maximized"):
            self.maximize()

    def _save_ui_state(self):
        """Store window geometry and the gain lock.

        Never fatal: a window that cannot record its state should still close,
        and a lock toggle that cannot persist should still take effect now.
        """
        try:
            os.makedirs(os.path.dirname(self._UI_STATE), exist_ok=True)
            state = {
                "width": self.get_width(),
                "height": self.get_height(),
                "maximized": self.is_maximized(),
                "gain_locked": bool(
                    getattr(self, "gain_lock", None) and self.gain_lock.get_active()
                ),
            }
            if state["maximized"]:
                # get_width/height report the maximized size; keep the last
                # restored size so unmaximizing does not snap to full screen.
                previous = self._load_ui_state()
                state["width"] = previous.get("width", state["width"])
                state["height"] = previous.get("height", state["height"])
            tmp = self._UI_STATE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp, self._UI_STATE)
        except OSError:
            pass

    def _build_ui(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(box)

        # Header bar
        header = Adw.HeaderBar()
        self.status_label = Gtk.Label(label="Disconnected")
        self.status_label.add_css_class("dim-label")
        header.set_title_widget(self.status_label)

        # Audio-service status. Packed at the start and hidden while healthy,
        # so it costs nothing until it has something to say -- it used to be a
        # whole PreferencesGroup carrying one row.
        self.service_btn = Gtk.MenuButton(
            icon_name="dialog-warning-symbolic", visible=False,
        )
        self.service_btn.add_css_class("flat")
        service_pop = Gtk.Popover()
        service_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=8,
            margin_top=12, margin_bottom=12, margin_start=12, margin_end=12,
        )
        self.service_label = Gtk.Label(label="", xalign=0, wrap=True, max_width_chars=34)
        service_box.append(self.service_label)
        self.uninstall_btn = Gtk.Button(label="Uninstall capture fix")
        self.uninstall_btn.connect("clicked", self._on_uninstall_clicked)
        service_box.append(self.uninstall_btn)
        service_pop.set_child(service_box)
        self.service_btn.set_popover(service_pop)
        header.pack_start(self.service_btn)

        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Reconnect")
        refresh_btn.connect("clicked", lambda _: self._try_connect())
        header.pack_end(refresh_btn)

        # Sidebar toggle (placed at the end so it sits next to the close button)
        self.sidebar_toggle = Gtk.ToggleButton(
            icon_name="sidebar-show-symbolic",
            tooltip_text="Toggle device panel",
            # Closed by default: the matrix is the thing you came for, and the
            # device controls are set once and then left alone.
            active=False,
        )
        header.pack_end(self.sidebar_toggle)
        box.append(header)

        # --- Split view: matrix (content) | device controls (sidebar) ---------
        self.split = Adw.OverlaySplitView(
            sidebar_position=Gtk.PackType.END,
            min_sidebar_width=320,
            max_sidebar_width=420,
            sidebar_width_fraction=0.30,
            vexpand=True,
        )
        box.append(self.split)

        self.sidebar_toggle.bind_property(
            "active", self.split, "show-sidebar",
            GObject.BindingFlags.BIDIRECTIONAL | GObject.BindingFlags.SYNC_CREATE,
        )

        # Auto-collapse the sidebar into an overlay on narrow windows.
        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 900sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        # --- Content: mix matrix ---------------------------------------------
        self.matrix = MixMatrix()
        self.split.set_content(self.matrix)

        for mix_id, mix in self._mixes.items():
            self.matrix.add_mix(
                mix_id,
                title=mix.get("name", mix_id),
                subtitle=mix.get("subtitle", ""),
                icon_name=mix.get("icon_name", mixes_module.DEFAULT_ICON),
            )

        self.mic_source = self.matrix.add_source(
            "mic", name="Microphone",
            icon_name="audio-input-microphone-symbolic",
            has_level=True,
            is_capture=True,
        )
        self.mic_source.connect("volume-changed", self._on_mic_matrix_volume_changed)
        self.mic_source.connect("mute-toggled", self._on_mic_matrix_mute_toggled)

        # User-defined app sources (persisted)
        for source_id, source in self._sources.items():
            self.matrix.add_source(
                source_id,
                name=source.get("name", source_id),
                icon_name=source.get("icon_name", "applications-multimedia-symbolic"),
                has_level=True,
                removable=True,
                editable=True,
                reorderable=True,
                is_capture=sources_module.kind(source) == sources_module.KIND_DEVICE,
            )
            self._wire_source_row(source_id)

        self.matrix.connect("add-source-clicked", self._on_add_source_clicked)
        self.matrix.connect("remove-source-clicked", self._on_remove_source_clicked)
        self.matrix.connect("edit-source-clicked", self._on_edit_source_clicked)
        self.matrix.connect("move-source-clicked", self._on_move_source_clicked)
        self.matrix.connect("add-mix-clicked", self._on_add_mix_clicked)
        self.matrix.connect("rename-mix-clicked", self._on_rename_mix_clicked)
        self.matrix.connect("remove-mix-clicked", self._on_remove_mix_clicked)
        self.matrix.connect("mix-output-changed", self._on_mix_output_changed)

        # --- Sidebar: device controls -----------------------------------------
        sidebar_scroll = Gtk.ScrolledWindow(
            vexpand=True,
            hscrollbar_policy=Gtk.PolicyType.NEVER,
            vscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
        )
        sidebar_clamp = Adw.Clamp(
            maximum_size=380,
            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12,
        )
        sidebar_scroll.set_child(sidebar_clamp)

        sidebar_content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        sidebar_clamp.set_child(sidebar_content)
        self._build_device_pane(sidebar_content)

        self.split.set_sidebar(sidebar_scroll)

    def _build_device_pane(self, parent):
        """Populate the sidebar: Microphone, Headphones, and device info."""
        # --- Mic controls ---
        mic_group = Adw.PreferencesGroup(title="Microphone")
        parent.append(mic_group)

        mute_row = Adw.SwitchRow(title="Mute", subtitle="Toggle microphone mute")
        mute_row.connect("notify::active", self._on_mute_changed)
        self.mute_row = mute_row
        mic_group.add(mute_row)

        gain_row = Adw.ActionRow(title="Gain")
        self.gain_label = Gtk.Label(label="—", width_chars=8, xalign=1)
        self.gain_label.add_css_class("monospace")
        gain_row.add_suffix(self.gain_label)

        # Preamp gain is set once and then wants leaving alone: a stray scroll
        # over the slider silently changes how loud you are to everyone else,
        # and nothing on screen makes that obvious afterwards.
        self.gain_lock = Gtk.ToggleButton(
            icon_name="changes-allow-symbolic", valign=Gtk.Align.CENTER,
            tooltip_text="Lock gain",
        )
        self.gain_lock.add_css_class("flat")
        self.gain_lock.connect("toggled", self._on_gain_lock_toggled)
        gain_row.add_suffix(self.gain_lock)
        mic_group.add(gain_row)

        self.gain_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            draw_value=False,
            adjustment=Gtk.Adjustment(lower=0x0000, upper=0x5000, step_increment=0x40, page_increment=0x200),
        )
        self.gain_scale.connect("value-changed", self._on_gain_changed)
        mic_group.add(_slider_row(self.gain_scale))

        knob_row = Adw.ActionRow(title="Knob Controls", subtitle="What the physical knob adjusts")
        self.knob_label = Gtk.Label(label="Gain")
        self.knob_label.add_css_class("dim-label")
        knob_row.add_suffix(self.knob_label)
        self.knob_row = knob_row
        mic_group.add(knob_row)

        # --- Headphone controls ---
        hp_group = Adw.PreferencesGroup(title="Headphones")
        parent.append(hp_group)

        hp_vol_row = Adw.ActionRow(title="Volume")
        self.hp_label = Gtk.Label(label="0.0 dB", width_chars=10, xalign=1)
        self.hp_label.add_css_class("monospace")
        hp_vol_row.add_suffix(self.hp_label)
        hp_group.add(hp_vol_row)

        self.hp_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            draw_value=False,
            adjustment=Gtk.Adjustment(lower=-60.0, upper=0.0, step_increment=0.5, page_increment=2.0),
        )
        self.hp_scale.connect("value-changed", self._on_hp_changed)
        hp_group.add(_slider_row(self.hp_scale))

        lowz_row = Adw.SwitchRow(title="Low Impedance", subtitle="For low impedance headphones")
        lowz_row.connect("notify::active", self._on_lowz_changed)
        self.lowz_row = lowz_row
        hp_group.add(lowz_row)

        mix_row = Adw.ActionRow(title="Monitor Mix", subtitle="Mic / PC monitoring balance")
        self.mix_label = Gtk.Label(label="—", width_chars=8, xalign=1)
        self.mix_label.add_css_class("monospace")
        mix_row.add_suffix(self.mix_label)
        self.mix_row = mix_row
        mix_row.set_visible(False)
        hp_group.add(mix_row)

        self.mix_scale = Gtk.Scale(
            orientation=Gtk.Orientation.HORIZONTAL,
            hexpand=True,
            draw_value=False,
            adjustment=Gtk.Adjustment(lower=0, upper=0x6400, step_increment=0x100, page_increment=0x800),
        )
        self.mix_scale.set_margin_start(12)
        self.mix_scale.set_margin_end(12)
        self.mix_scale.connect("value-changed", self._on_mix_changed)
        self.mix_scale_row = _slider_row(self.mix_scale)
        self.mix_scale_row.set_visible(False)
        hp_group.add(self.mix_scale_row)

        # Output routing is per mix and lives in each mix column's header
        # menu, not here — one device combo could only ever speak for one mix.

        # --- Device info ---
        # Titleless group so the expander reads as a single collapsed line: it
        # is reference material, looked at once, and does not deserve a
        # permanent three-row card in a narrow sidebar.
        info_group = Adw.PreferencesGroup()
        parent.append(info_group)

        info_expander = Adw.ExpanderRow(title="Device Info")
        info_group.add(info_expander)

        self.fw_row = Adw.ActionRow(title="Firmware")
        self.fw_label = Gtk.Label(label="—")
        self.fw_label.add_css_class("dim-label")
        self.fw_row.add_suffix(self.fw_label)
        info_expander.add_row(self.fw_row)

        self.api_row = Adw.ActionRow(title="API")
        self.api_label = Gtk.Label(label="—")
        self.api_label.add_css_class("dim-label")
        self.api_row.add_suffix(self.api_label)
        info_expander.add_row(self.api_row)

        self.serial_row = Adw.ActionRow(title="Serial")
        self.serial_label = Gtk.Label(label="—")
        self.serial_label.add_css_class("dim-label")
        self.serial_row.add_suffix(self.serial_label)
        info_expander.add_row(self.serial_row)

    def _refresh_mix_emptiness(self):
        """Mark every mix that no source currently feeds."""
        cells = self.mixer.cells()
        for mix_id in self._mixes:
            fed = any(
                state.get("volume", 0.0) > 0.0 and not state.get("muted")
                for key, state in cells.items()
                if key.rsplit(".", 1)[-1] == mix_id
            )
            self.matrix.set_mix_empty(mix_id, not fed)

    def _update_service_status(self):
        """Reflect the audio service in the header, and only when it matters.

        The capture fix works around a firmware race between playback and
        capture on the same device. A card with no playback side cannot hit it,
        so warning that the service is down is noise there -- which is the
        normal state for anyone monitoring through a headset rather than the
        Wave's own jack.
        """
        if service.is_running():
            self.service_btn.set_visible(False)
            return

        needed = bool(getattr(self.mixer, "hp", None)) if hasattr(self, "mixer") else True
        if not needed:
            self.service_btn.set_visible(False)
            return

        if service.is_failed():
            text = "The audio service failed to start."
        elif service.is_installed():
            text = "The audio service is installed but not running."
        else:
            text = "The audio service is not running."
        self.service_label.set_label(
            text + " Without it the microphone can fall silent when playback "
            "starts before capture."
        )
        self.uninstall_btn.set_visible(service.is_installed())
        self.service_btn.set_tooltip_text(text)
        self.service_btn.set_visible(True)

    def _on_uninstall_clicked(self, btn):
        dialog = Adw.AlertDialog(
            heading="Uninstall Capture Fix?",
            body="This will remove the audio service and USB permissions.\n\nYou can reinstall them by restarting OpenWave.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("uninstall", "Uninstall")
        dialog.set_response_appearance("uninstall", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.choose(self, None, self._on_uninstall_response)

    def _on_uninstall_response(self, dialog, result):
        response = dialog.choose_finish(result)
        if response != "uninstall":
            return
        success, message = setup.run_uninstall()
        self._update_service_status()
        if not success:
            err = Adw.AlertDialog(heading="Uninstall Failed", body=message)
            err.add_response("ok", "OK")
            err.choose(self, None, lambda d, r: d.choose_finish(r))

    def _usb_async(self, fn, on_done=None, on_error=None):
        """Run fn in a background thread; call on_done/on_error on GTK thread."""
        def _worker():
            try:
                result = fn()
                if on_done:
                    GLib.idle_add(on_done, result)
            except Exception as e:
                if on_error:
                    GLib.idle_add(on_error, e)
        threading.Thread(target=_worker, daemon=True).start()

    def _try_connect(self):
        self.status_label.set_label("Connecting...")
        def _connect():
            self.dev.disconnect()
            self.dev.connect()
            info = {}
            try:
                info = self.dev.read_device_info()
            except Exception:
                pass
            return {"state": self.dev.get_all(), "info": info}
        def _done(result):
            self._apply_profile(self.dev.profile)
            self.status_label.remove_css_class("dim-label")
            self._apply_state(result["state"])
            info = result["info"]
            self.fw_label.set_label(info.get("fw_version", "—"))
            self.api_label.set_label(info.get("api_version", "—"))
            self.serial_label.set_label(info.get("serial", "—"))
            self._start_polling()
        def _fail(e):
            self.status_label.set_label("Disconnected")
            self.status_label.add_css_class("dim-label")
        self._usb_async(_connect, _done, _fail)

    def _start_polling(self):
        """Start 10 Hz polling to sync hardware state."""
        if self._poll_id:
            GLib.source_remove(self._poll_id)
        self._poll_id = GLib.timeout_add(100, self._poll_tick)

    def _stop_polling(self):
        if self._poll_id:
            GLib.source_remove(self._poll_id)
            self._poll_id = None

    def _poll_tick(self):
        """Called every 100ms — read device state in background."""
        if not self.dev.connected:
            self._poll_id = None
            return False  # stop polling
        # Only poll if not already busy with a user-initiated write
        self._usb_async(self.dev.get_all, self._on_poll_result, self._on_poll_error)
        return True  # keep polling

    def _on_poll_result(self, state):
        if state != self._last_state:
            self._apply_state(state)

    def _on_poll_error(self, e):
        self.status_label.set_label("Disconnected")
        self.status_label.add_css_class("dim-label")
        self.dev.disconnect()
        self._stop_polling()

    def _apply_profile(self, profile):
        """Adapt the UI to the connected device model."""
        self._gain_max = profile.gain_max
        self.gain_scale.get_adjustment().set_upper(profile.gain_max)
        self.knob_row.set_visible(profile.has_vol_select)
        self.lowz_row.set_visible(profile.has_low_z)
        self.mix_row.set_visible(profile.has_monitor_mix)
        self.mix_scale_row.set_visible(profile.has_monitor_mix)
        if profile.has_monitor_mix:
            self.mix_scale.get_adjustment().set_upper(profile.mix_max)
        self.mic_source.set_name(profile.display_name)
        self.status_label.set_label(f"OpenWave — {profile.display_name}")

    def _format_gain(self, raw):
        scale = self.dev.profile.gain_scale if self.dev.profile else None
        if scale:
            return f"{raw / scale:.1f} dB"
        return f"0x{raw:04X}"

    def _apply_state(self, state):
        """Update UI from device state dict (must be called on GTK thread)."""
        self._updating_ui = True
        self._last_state = state
        self.mute_row.set_active(state["mute"])
        self.gain_scale.set_value(state["gain_raw"])
        self.gain_label.set_label(self._format_gain(state["gain_raw"]))
        self.hp_scale.set_value(state["hp_volume_db"])
        self.hp_label.set_label(f"{state['hp_volume_db']:.1f} dB")
        if "low_impedance" in state:
            self.lowz_row.set_active(state["low_impedance"])
        if "volume_select" in state:
            self.knob_label.set_label(KNOB_LABELS.get(state["volume_select"], "Gain"))
        if "monitor_mix" in state:
            self.mix_scale.set_value(state["monitor_mix"])
            self.mix_label.set_label(f"{state['monitor_mix'] / 256:.0f}%")
        self.mic_source.set_volume(state["gain_raw"] / self._gain_max)
        self.mic_source.set_muted(state["mute"])
        self._updating_ui = False

    def _on_usb_error(self, e):
        self.status_label.set_label("Disconnected")
        self.status_label.add_css_class("dim-label")
        self.dev.disconnect()
        self._stop_polling()

    def _on_mute_changed(self, row, _pspec):
        if self._updating_ui or not self.dev.connected:
            return
        muted = row.get_active()
        self._usb_async(lambda: self.dev.set_mute(muted), on_error=self._on_usb_error)

    def _on_gain_changed(self, scale):
        if self._updating_ui or not self.dev.connected:
            return
        val = int(scale.get_value())
        self.gain_label.set_label(self._format_gain(val))
        # Debounce — only send after slider stops moving for 200ms
        if hasattr(self, '_gain_timeout') and self._gain_timeout:
            GLib.source_remove(self._gain_timeout)
        self._gain_timeout = GLib.timeout_add(200, self._send_gain, val)

    def _send_gain(self, val):
        self._gain_timeout = None
        self._usb_async(lambda: self.dev.set_gain_raw(val), on_error=self._on_usb_error)
        return False

    def _on_hp_changed(self, scale):
        if self._updating_ui or not self.dev.connected:
            return
        db = scale.get_value()
        self.hp_label.set_label(f"{db:.1f} dB")
        if hasattr(self, '_hp_timeout') and self._hp_timeout:
            GLib.source_remove(self._hp_timeout)
        self._hp_timeout = GLib.timeout_add(200, self._send_hp, db)

    def _send_hp(self, db):
        self._hp_timeout = None
        self._usb_async(lambda: self.dev.set_hp_volume_db(db), on_error=self._on_usb_error)
        return False

    # ----- per-mix output routing (shown in each column header's menu) -----
    def _output_entries(self, mix_id, sinks, default_sink):
        """(entries, current, summary, monitored) for one mix's header menu."""
        current = self.mixer.get_output(mix_id)
        resolved = self.mixer.resolve_output(
            mix_id, sinks=sinks, default_sink=default_sink,
        )
        descriptions = {sink["name"]: sink["description"] for sink in sinks}

        auto_label = "Automatic"
        if current == OUTPUT_AUTO and resolved in descriptions:
            # Only name the device when Automatic is what is actually in force:
            # with an explicit sink chosen, resolve_output returns that sink,
            # and labelling Automatic with it would claim a resolution that is
            # not the one Automatic would pick.
            auto_label = f"Automatic — {descriptions[resolved]}"

        # Automatic stays first: it is the entry that describes the default
        # behaviour, and a mix with no stored choice lands on it.
        entries = [(OUTPUT_AUTO, auto_label), (OUTPUT_NONE, "Not monitored")]
        entries += [(sink["name"], sink["description"]) for sink in sinks]
        if current not in [name for name, _ in entries]:
            # A remembered device that is currently absent: show it rather than
            # silently substituting a sentinel.
            entries.append((current, f"{current} (unavailable)"))

        if current == OUTPUT_NONE:
            summary, monitored = "Not monitored", False
        elif resolved is None:
            summary, monitored = "No output", False
        else:
            summary, monitored = descriptions.get(resolved, resolved), True
        return entries, current, summary, monitored

    def _refresh_outputs(self):
        """Push the live sink list into every mix header's output menu."""
        sinks = list_output_sinks()
        default_sink = default_sink_name()
        for mix_id in self._mixes:
            entries, current, summary, monitored = self._output_entries(
                mix_id, sinks, default_sink,
            )
            self.matrix.set_mix_outputs(
                mix_id, entries, current, summary, monitored,
            )

    def _on_mix_output_changed(self, _matrix, mix_id, name):
        self.mixer.set_output(mix_id, name)
        # Re-label "Automatic — <device>" once the mixer has retargeted the
        # loopback. A burst of changes collapses into one refresh.
        if self._output_refresh_id is not None:
            GLib.source_remove(self._output_refresh_id)
        self._output_refresh_id = GLib.timeout_add(400, self._refresh_outputs_tick)

    def _refresh_outputs_tick(self):
        self._output_refresh_id = None
        self._refresh_outputs()
        return GLib.SOURCE_REMOVE

    # ----- mix create / rename / delete -----
    def _on_add_mix_clicked(self, _matrix):
        dialog = MixDialog(
            heading="Add Mix", confirm_label="Add Mix",
            name="", icon_name=mixes_module.DEFAULT_ICON,
        )
        dialog.connect("mix-confirmed", self._on_mix_created)
        dialog.present(self)

    def _on_mix_created(self, _dialog, name, icon_name):
        mix = mixes_module.new_mix(name=name, icon_name=icon_name)
        self._mixes = mixes_module.add(self._mixes, mix)
        self.matrix.add_mix(
            mix["id"],
            title=mix["name"],
            subtitle=mix.get("subtitle", ""),
            icon_name=mix["icon_name"],
        )
        for source_id in ["mic"] + list(self._sources):
            self._wire_cell(source_id, mix["id"])
        # install_mixes shells out to pw-cli/pactl for seconds at a time, so it
        # runs off the main thread; the mixer is told about the mix only once
        # the sink it would route into actually exists.
        self._usb_async(
            lambda defs=dict(self._mixes): setup.install_mixes(defs),
            on_done=self._on_mix_installed,
            on_error=self._on_mix_install_failed,
        )

    def _on_mix_installed(self, _ok):
        self.mixer.set_mixes(self._mixes)
        self._refresh_outputs()
        self._refresh_mix_emptiness()

    def _on_mix_install_failed(self, exc):
        """Register the mix anyway, and say that its sink is missing.

        The mixer must learn about the mix whether or not the sink was
        created: without this the column is drawn and persisted while every
        cell in it stays silently inert for the rest of the session, with
        nothing shown to explain why. _mix_sink() still resolves, so the cells
        reconcile as soon as the sink appears.
        """
        logging.error("Failed to install mix sinks: %s", exc)
        self.mixer.set_mixes(self._mixes)
        self._refresh_outputs()

    def _on_rename_mix_clicked(self, _matrix, mix_id):
        mix = self._mixes.get(mix_id)
        if mix is None:
            return
        dialog = MixDialog(
            heading="Rename Mix", confirm_label="Save",
            name=mix.get("name", ""),
            icon_name=mix.get("icon_name", mixes_module.DEFAULT_ICON),
        )
        dialog.connect("mix-confirmed", self._on_mix_renamed, mix_id)
        dialog.present(self)

    def _on_mix_renamed(self, _dialog, name, icon_name, mix_id):
        if mix_id not in self._mixes:
            return
        # Name and icon only. mixes.update already refuses id and sink, and
        # leaving `description` alone keeps the node.description PipeWire
        # publishes in step with the sink OBS or Discord is already bound to —
        # which is the whole reason a rename is safe.
        self._mixes = mixes_module.update(
            self._mixes, mix_id, name=name, icon_name=icon_name,
        )
        self.matrix.set_mix(mix_id, title=name, icon_name=icon_name)
        self.mixer.set_mixes(self._mixes)
        # Renders byte-identical config (sink and description are untouched),
        # so this only re-asserts that the sink is live. Still off the main
        # thread, because proving that costs a pactl round trip.
        self._usb_async(lambda defs=dict(self._mixes): setup.install_mixes(defs))

    def _on_remove_mix_clicked(self, _matrix, mix_id):
        if len(self._mixes) <= 1:
            return  # the header control is already insensitive; belt and braces
        mix = self._mixes.get(mix_id)
        if mix is None:
            return
        name = mix.get("name", "this mix")
        description = mix.get("description") or mix.get("sink", "")
        dialog = Adw.AlertDialog(
            heading="Delete mix?",
            body=f"“{name}” and its levels for every source are deleted, "
                 f"and the “{description}” audio device disappears. "
                 f"Anything recording or listening to it — OBS, Discord — "
                 f"loses that input until it is pointed somewhere else.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.choose(
            self, None, lambda d, r: self._on_remove_mix_response(d, r, mix_id),
        )

    def _on_remove_mix_response(self, dialog, result, mix_id):
        if dialog.choose_finish(result) != "delete":
            return
        if mix_id not in self._mixes:
            return
        # A slider left mid-drag has a pending _flush_cell_volume timeout that
        # would call set_cell and resurrect the very keys remove_mix purges.
        for key in [k for k in self._cell_debounce_ids if k[1] == mix_id]:
            GLib.source_remove(self._cell_debounce_ids.pop(key))
        # Column first, so nothing can drive a mix that is going away; then the
        # mixer, which captures the sink name before dropping the definition and
        # on its worker tears every loopback down before destroying the sink;
        # then the definition and the generated config catch up.
        self.matrix.remove_mix(mix_id)
        self.mixer.remove_mix(mix_id)
        self._mixes = mixes_module.remove(self._mixes, mix_id)
        self._usb_async(lambda defs=dict(self._mixes): setup.install_mixes(defs))

    def _on_lowz_changed(self, row, _pspec):
        if self._updating_ui or not self.dev.connected:
            return
        enabled = row.get_active()
        self._usb_async(lambda: self.dev.set_low_impedance(enabled), on_error=self._on_usb_error)

    def _on_mix_changed(self, scale):
        if self._updating_ui or not self.dev.connected:
            return
        val = int(scale.get_value())
        self.mix_label.set_label(f"{val / 256:.0f}%")
        if self._mix_timeout:
            GLib.source_remove(self._mix_timeout)
        self._mix_timeout = GLib.timeout_add(200, self._send_mix, val)

    def _send_mix(self, val):
        self._mix_timeout = None
        self._usb_async(lambda: self.dev.set_monitor_mix(val), on_error=self._on_usb_error)
        return False

    def _on_mic_matrix_volume_changed(self, _source, value):
        if self._updating_ui or not self.dev.connected:
            return
        raw = int(value * self._gain_max)
        self.gain_label.set_label(self._format_gain(raw))
        self._updating_ui = True
        self.gain_scale.set_value(raw)
        self._updating_ui = False
        if self._gain_timeout:
            GLib.source_remove(self._gain_timeout)
        self._gain_timeout = GLib.timeout_add(200, self._send_gain, raw)

    def _on_mic_matrix_mute_toggled(self, _source, muted):
        if self._updating_ui or not self.dev.connected:
            return
        self._updating_ui = True
        self.mute_row.set_active(muted)
        self._updating_ui = False
        self._usb_async(lambda: self.dev.set_mute(muted), on_error=self._on_usb_error)

    def _wire_matrix_cells(self):
        """Bind each per-cell slider/mute to the mixer + restore persisted levels."""
        source_ids = ["mic"] + list(self._sources.keys())
        for source_id in source_ids:
            for mix_id in self._mixes:
                self._wire_cell(source_id, mix_id)

    def _wire_cell(self, source_id, mix_id):
        cell = self.matrix.cell(source_id, mix_id)
        if cell is None:
            return
        state = self.mixer.get_cell(source_id, mix_id)
        cell.set_volume(state["volume"])
        cell.set_muted(state["muted"])
        cell.connect("volume-changed", self._on_cell_volume_changed, source_id, mix_id)
        cell.connect("mute-toggled", self._on_cell_mute_toggled, source_id, mix_id)

    def _start_stream_poll(self):
        """Poll for new/vanished PipeWire output streams every 2 s."""
        if self._stream_poll_id:
            GLib.source_remove(self._stream_poll_id)
        self._stream_poll_id = GLib.timeout_add_seconds(2, self._stream_poll_tick)

    # Capture devices change orders of magnitude less often than streams and
    # finding out costs its own pw-dump, so check every third stream tick
    # (~6 s) rather than adding a second timer with its own teardown.
    _DEVICE_POLL_EVERY = 3

    def _stream_poll_tick(self):
        self.mixer.poll_streams()
        self._device_poll_countdown -= 1
        check_devices = self._device_poll_countdown <= 0
        if check_devices:
            self._device_poll_countdown = self._DEVICE_POLL_EVERY
            self.mixer.request_capture_poll()
        for source_id, source in list(self._sources.items()):
            if sources_module.kind(source) == sources_module.KIND_DEVICE:
                if check_devices:
                    self._refresh_device_meter(source_id, source)
            else:
                self._refresh_app_meter(source_id)
        return True

    def _start_meters(self):
        """Begin metering the mic + any app source that already has a matching stream."""
        if self.mixer.mic:
            self.meter.start(
                "mic", self.mixer.mic,
                lambda level: self._set_source_level("mic", level),
            )
        for source_id in self._sources.keys():
            self._refresh_source_meter(source_id)

    def _refresh_source_meter(self, source_id):
        """Point a source's meter at whatever currently carries its audio."""
        source = self._sources.get(source_id)
        if not source:
            return
        if sources_module.kind(source) == sources_module.KIND_DEVICE:
            self._refresh_device_meter(source_id, source)
        else:
            self._refresh_app_meter(source_id)

    def _refresh_device_meter(self, source_id, source):
        """Meter a capture device straight off its node, as the mic row does.

        There is no stream to follow — the node *is* the audio — so this is the
        same call _start_meters makes for self.mixer.mic, and meter.py needs no
        change to serve it.

        _meter_targets holds a node name for a device source where it holds a
        stream id for an app source. The two never meet: a value is only ever
        compared against another value for the same source_id.
        """
        node_name = source.get("node_name")
        present = self.mixer.capture_device_present(node_name)
        cell = self.matrix.source(source_id)
        if cell is not None:
            cell.set_available(present, reason="Capture device not connected")
        if not present:
            # Stop rather than leave pw-cat holding a device that has gone, and
            # zero the bar so it does not freeze on its last value.
            if self._meter_targets.pop(source_id, None) is not None:
                self.meter.stop(source_id)
                self._set_source_level(source_id, 0.0)
            return
        if self._meter_targets.get(source_id) == node_name:
            return  # already metering this node
        self.meter.start(
            source_id, node_name,
            lambda level, sid=source_id: self._set_source_level(sid, level),
        )
        self._meter_targets[source_id] = node_name

    def _refresh_app_meter(self, source_id):
        """Re-point the meter at the stream the mixer actually routes for this
        source, and reflect whether the bound application is playing at all.
        Called on stream-poll changes and source add."""
        source = self._sources.get(source_id)
        if not source:
            return
        streams = self.mixer.streams()
        # The same claim function the mixer routes by, so the meter can never
        # end up watching a stream a different source owns.
        claimed = claim_streams(self._sources, streams).get(source_id, set())
        candidate = next(
            (s for sid, s in streams.items() if sid in claimed), None,
        )
        current = self._meter_targets.get(source_id)
        if candidate is None:
            # Waiting is set before the early return below: on the steady idle
            # path the meter is already stopped, so a set_waiting placed after
            # that return would fire once and never again.
            if any(stream_matches(source, s) for s in streams.values()):
                # The app is playing, but another source claimed the stream
                # first — see mixer.claim_streams for why only one may have it.
                hint = "Routed by another source"
            else:
                hint = "Waiting for audio"
            self._set_source_waiting(source_id, True, hint)
            if current is not None:
                self.meter.stop(source_id)
                self._meter_targets.pop(source_id, None)
                self._set_source_level(source_id, 0.0)
            return
        # Likewise before the `already metering` return, which is the steady
        # state for a running app and would otherwise leave the row dimmed
        # forever after the first tick that found it.
        self._set_source_waiting(source_id, False)
        if current == candidate["id"]:
            return  # already metering this stream
        self.meter.start(
            source_id, candidate["node_name"],
            lambda level, sid=source_id: self._set_source_level(sid, level),
        )
        self._meter_targets[source_id] = candidate["id"]

    def _set_source_waiting(self, source_id, waiting, hint="Waiting for audio"):
        cell = self.matrix.source(source_id)
        if cell is not None:
            cell.set_waiting(waiting, hint)

    def _set_source_level(self, source_id, level):
        cell = self.matrix.source(source_id)
        if cell is not None:
            cell.set_level(level)

    def _on_add_source_clicked(self, _matrix):
        dialog = AddSourceDialog(exclude_nodes=self._bound_capture_nodes())
        dialog.connect("source-confirmed", self._on_source_confirmed)
        dialog.connect("device-source-confirmed", self._on_device_source_confirmed)
        dialog.present(self)

    def _bound_capture_nodes(self):
        """Capture nodes that already have a row, so the picker cannot make a
        duplicate. The Wave's own mic is in the set: it is the built-in row,
        and a second row for it would double the same audio into every mix."""
        nodes = {
            source.get("node_name")
            for source in self._sources.values()
            if sources_module.kind(source) == sources_module.KIND_DEVICE
        }
        nodes.add(self.mixer.mic)
        return {node for node in nodes if node}

    def _on_source_confirmed(self, _dialog, name, match_app_name, icon_name):
        self._install_source(sources_module.new_source(
            name=name, match_app_name=match_app_name, icon_name=icon_name,
        ))

    def _on_device_source_confirmed(self, _dialog, name, node_name, icon_name):
        # Queue the re-snapshot before installing: the reconcile that
        # _install_source triggers refuses to wire a node the snapshot has not
        # seen, and the worker runs queued tasks in insertion order, so the
        # refresh lands first. Doing it synchronously would put a pw-dump on
        # the GTK thread in a click handler.
        self.mixer.request_capture_poll()
        self._install_source(sources_module.new_device_source(
            name=name, node_name=node_name, icon_name=icon_name,
        ))

    def _install_source(self, source):
        """Persist a new source of either kind, give it a row, and wire it up."""
        self._sources = sources_module.add(self._sources, source)
        self.matrix.add_source(
            source["id"],
            name=source["name"],
            icon_name=source["icon_name"],
            has_level=True,
            removable=True,
            editable=True,
            reorderable=True,
            is_capture=sources_module.kind(source) == sources_module.KIND_DEVICE,
        )
        self._wire_source_row(source["id"])
        for mix_id in self._mixes:
            self._wire_cell(source["id"], mix_id)
        self.mixer.set_sources(self._sources)
        self.mixer.poll_streams()
        self._refresh_source_meter(source["id"])
        self._refresh_mix_emptiness()

    def _wire_source_row(self, source_id):
        """Connect a source row's own level slider and mute.

        Distinct from the mix cells beside it: this is the source's level
        everywhere, applied to its intake sink ahead of the per-mix faders.
        """
        cell = self.matrix.source(source_id)
        if cell is None:
            return
        source = self._sources.get(source_id, {})
        cell.set_volume(float(source.get("level", 1.0)))
        cell.set_muted(bool(source.get("muted", False)))
        cell.connect("volume-changed", self._on_source_level_changed, source_id)
        cell.connect("mute-toggled", self._on_source_mute_toggled, source_id)

    def _on_source_level_changed(self, _cell, volume, source_id):
        self.mixer.set_source_level(
            source_id, volume, self._sources.get(source_id, {}).get("muted", False))
        sources_module.save(self._sources)

    def _on_source_mute_toggled(self, _cell, muted, source_id):
        self.mixer.set_source_level(
            source_id, self._sources.get(source_id, {}).get("level", 1.0), muted)
        sources_module.save(self._sources)

    def _on_move_source_clicked(self, _matrix, source_id, delta):
        before = list(self._sources)
        self._sources = sources_module.reorder(self._sources, source_id, delta)
        if list(self._sources) == before:
            return                     # already at that end of the list
        # Every MixCell is rebuilt by the reorder, so the cells must be wired
        # again: the old widgets are gone and the new ones carry no state.
        self.matrix.reorder_sources(list(self._sources))
        for sid in self._sources:
            self._wire_source_row(sid)
        self._wire_matrix_cells()
        self._refresh_mix_emptiness()
        self._start_meters()

    def _on_edit_source_clicked(self, _matrix, source_id):
        source = self._sources.get(source_id)
        if source is None:
            return
        dialog = AddSourceDialog(source=source)
        dialog.connect("source-edited", self._on_source_edited)
        dialog.present(self)

    def _on_source_edited(self, _dialog, source_id, name, binding, icon_name):
        if source_id not in self._sources:
            return  # removed while the dialog was open
        source = self._sources[source_id]
        is_device = sources_module.kind(source) == sources_module.KIND_DEVICE
        # Snapshot BEFORE update: sources.update mutates the record in place,
        # so reading afterwards would always compare a value to itself.
        old_binding = (
            source.get("node_name") if is_device
            else sources_module.format_bindings(source)
        )

        # sources_module.update, never new_source: the id is the prefix of every
        # "<source_id>.<mix_id>" cell key, so a fresh id would orphan the levels.
        fields = {"name": name, "icon_name": icon_name}
        if not is_device:
            # A device's binding is its node_name, which the dialog shows but
            # does not offer to edit — it is picked from live hardware, and
            # `binding` arrives empty for that flow.
            # Stored as a list; drop the superseded singular key so bindings()
            # cannot read a stale value from it.
            fields["match_app_names"] = sources_module.parse_bindings(binding)
            source.pop("match_app_name", None)
        self._sources = sources_module.update(self._sources, source_id, **fields)

        cell = self.matrix.source(source_id)
        if cell is not None:
            cell.set_name(name)
            cell.set_icon(icon_name)

        if not is_device and binding != old_binding:
            # _refresh_app_meter early-returns when the cached target is still
            # the current candidate, so a stale entry pointing at the OLD app's
            # stream would keep metering the wrong application forever.
            self.meter.stop(source_id)
            self._meter_targets.pop(source_id, None)
            self._set_source_level(source_id, 0.0)

        # poll_streams BEFORE set_sources: it refreshes Mixer._streams inline on
        # this thread, so the reconcile set_sources enqueues sees the current
        # stream set instead of a cache up to 2 s old.
        self.mixer.poll_streams()
        self.mixer.set_sources(self._sources)
        self._refresh_source_meter(source_id)

    def _on_remove_source_clicked(self, _matrix, source_id):
        source = self._sources.get(source_id, {})
        name = source.get("name", "this source")
        dialog = Adw.AlertDialog(
            heading="Remove source?",
            body=f"This deletes “{name}” and its mix levels. The bound application "
                 f"itself is not affected.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.choose(self, None, lambda d, r: self._on_remove_response(d, r, source_id))

    def _on_remove_response(self, dialog, result, source_id):
        if dialog.choose_finish(result) != "remove":
            return
        self.meter.stop(source_id)
        self._meter_targets.pop(source_id, None)
        self.matrix.remove_source(source_id)
        self._sources = sources_module.remove(self._sources, source_id)
        self.mixer.remove_source(source_id)

    _CELL_DEBOUNCE_MS = 150

    def _on_cell_volume_changed(self, _cell, value, source_id, mix_id):
        # During a drag, value-changed fires continuously; coalesce into a
        # single set_cell after the slider settles.
        key = (source_id, mix_id)
        prev = self._cell_debounce_ids.pop(key, None)
        if prev is not None:
            GLib.source_remove(prev)
        self._cell_debounce_ids[key] = GLib.timeout_add(
            self._CELL_DEBOUNCE_MS,
            self._flush_cell_volume, source_id, mix_id, value,
        )

    def _flush_cell_volume(self, source_id, mix_id, value):
        self._cell_debounce_ids.pop((source_id, mix_id), None)
        cur = self.mixer.get_cell(source_id, mix_id)
        self.mixer.set_cell(source_id, mix_id, value, cur["muted"])
        self._refresh_mix_emptiness()
        return False  # one-shot

    def _on_cell_mute_toggled(self, _cell, muted, source_id, mix_id):
        cur = self.mixer.get_cell(source_id, mix_id)
        self.mixer.set_cell(source_id, mix_id, cur["volume"], muted)
        self._refresh_mix_emptiness()


class WaveXLRApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.github.openwave",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE,
        )
        self._window = None
        self._start_hidden = False
        self._tray = None
        self.add_main_option(
            "hide", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE,
            "Start hidden in system tray", None,
        )

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        if options.contains("hide"):
            self._start_hidden = True
        self.activate()
        return 0

    def do_activate(self):
        if not self._window:
            self._load_css()
            if setup.needs_setup():
                self._show_setup_dialog()
                return
            self._window = WaveXLRWindow(application=self)
            # Hide-to-tray on close instead of quitting
            self._window.connect("close-request", self._on_close_request)
            self._setup_tray()
            if self._start_hidden:
                self._start_hidden = False  # only first launch
                return
        self._window.present()

    def _load_css(self):
        """Load OpenWave's stylesheet — alongside the .py files, or under share/."""
        beside_module = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "style.css"
        )
        css_path = (
            beside_module
            if os.path.exists(beside_module)
            else paths.data_file("style.css")
        )
        if css_path is None:
            return
        provider = Gtk.CssProvider()
        provider.load_from_path(css_path)
        display = Gdk.Display.get_default()
        if display is not None:
            Gtk.StyleContext.add_provider_for_display(
                display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

    def do_shutdown(self):
        """Stop polling, drop the USB link, and tear down loopback + meter
        subprocesses before the process exits."""
        if self._window is not None:
            self._window._save_ui_state()
            self._window._stop_polling()
            if hasattr(self._window, "meter"):
                self._window.meter.stop_all()
            if hasattr(self._window, "mixer"):
                self._window.mixer.stop()
            self._window.dev.disconnect()
        Adw.Application.do_shutdown(self)

    def _on_close_request(self, window):
        if self._tray:
            window.set_visible(False)
            return True  # prevent destroy, keep running in tray
        return False  # normal close → quit

    def _setup_tray(self):
        from .tray import TrayIcon
        self._tray = TrayIcon(
            on_activate=self._toggle_window,
            on_mute=self._toggle_mute,
            on_quit=self._quit_app,
        )
        self._tray.register()
        # Keep app alive when window is hidden
        self.hold()

    def _toggle_mute(self):
        if self._window and self._window.dev.connected:
            current = self._window._last_state and self._window._last_state.get("mute", False)
            self._window._usb_async(
                lambda: self._window.dev.set_mute(not current),
                on_error=self._window._on_usb_error,
            )

    def _quit_app(self):
        self.release()
        self.quit()

    def _toggle_window(self):
        if self._window:
            if self._window.get_visible():
                self._window.set_visible(False)
            else:
                self._window.present()

    def _show_setup_dialog(self):
        dialog = Adw.AlertDialog(
            heading="First-Time Setup",
            body="OpenWave needs to configure USB permissions and install the audio service.\n\nYou may be prompted for your password.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("setup", "Set Up")
        dialog.set_response_appearance("setup", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("setup")

        tmp_win = Adw.ApplicationWindow(application=self)
        tmp_win.present()

        dialog.choose(tmp_win, None, self._on_setup_response, tmp_win)

    def _on_setup_response(self, dialog, result, tmp_win):
        response = dialog.choose_finish(result)
        tmp_win.close()

        if response != "setup":
            self.quit()
            return

        success, message = setup.run_setup()
        if success:
            replug_dialog = Adw.AlertDialog(
                heading="Setup Complete",
                body=f"{message}.\n\nPlease replug your Elgato Wave device, then click Continue.",
            )
            replug_dialog.add_response("continue", "Continue")
            replug_dialog.set_default_response("continue")

            tmp_win2 = Adw.ApplicationWindow(application=self)
            tmp_win2.present()
            replug_dialog.choose(tmp_win2, None, self._on_replug_done, tmp_win2)
        else:
            err_dialog = Adw.AlertDialog(
                heading="Setup Failed",
                body=message,
            )
            err_dialog.add_response("ok", "OK")
            err_win = Adw.ApplicationWindow(application=self)
            err_win.present()
            err_dialog.choose(err_win, None, lambda d, r, w: (w.close(), self.quit()), err_win)

    def _on_replug_done(self, dialog, result, tmp_win):
        dialog.choose_finish(result)
        tmp_win.close()
        win = WaveXLRWindow(application=self)
        self._window = win
        win.present()


def main():
    app = WaveXLRApp()
    app.run(sys.argv)
