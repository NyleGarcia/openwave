"""'Add Source' picker: source kind, then a per-kind picker, then name + icon.

Page 0 forks between an application source and a hardware capture device. The
app branch and the device branch share nothing but the final name/icon page,
which is parameterised so each branch supplies its own defaults and confirm
handler; each branch emits its own signal so neither has to know the other
exists.
"""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, GObject  # noqa: E402

from .mixer import list_audio_streams, list_capture_sources

ICON_CHOICES = (
    ("applications-multimedia-symbolic", "Generic"),
    ("applications-games-symbolic", "Games"),
    ("input-gaming-symbolic", "Controller"),
    ("audio-x-generic-symbolic", "Music"),
    ("multimedia-player-symbolic", "Player"),
    ("user-available-symbolic", "Voice"),
    ("system-users-symbolic", "Chat"),
    ("web-browser-symbolic", "Browser"),
    ("video-display-symbolic", "Video"),
    ("preferences-desktop-multimedia-symbolic", "Media"),
    ("audio-headphones-symbolic", "Headphones"),
    ("microphone-sensitivity-high-symbolic", "Mic"),
)


class AddSourceDialog(Adw.Dialog):
    __gsignals__ = {
        # (display_name, match_app_name, icon_name)
        "source-confirmed": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
        # (display_name, capture_node_name, icon_name). A second signal rather
        # than a `kind` argument on the first: the two flows then share no
        # signature, so neither has to be edited when the other changes.
        "device-source-confirmed": (GObject.SignalFlags.RUN_FIRST, None, (str, str, str)),
    }

    def __init__(self, *, exclude_nodes=()):
        super().__init__()
        self.set_title("Add Source")
        self.set_content_width(480)
        self.set_content_height(560)

        self._nav = Adw.NavigationView()
        self.set_child(self._nav)

        # Capture nodes that already have a matrix row. Keyword-only with a
        # default so an existing AddSourceDialog() call site keeps working.
        self._exclude_nodes = frozenset(exclude_nodes)
        self._selected_app = None
        self._selected_device = None
        self._selected_icon = ICON_CHOICES[0][0]

        self._nav.push(self._build_type_page())

    # ------------------------------------------------------------ page 0
    def _build_type_page(self):
        """Fork between the source kinds.

        A separate first page rather than a mode switch on the app picker: the
        two flows share only the name/icon page, and keeping them in separate
        pages means the app picker needs no knowledge of devices at all.
        """
        page = Adw.NavigationPage(title="Add Source")

        view = Adw.ToolbarView()
        page.set_child(view)

        header = Adw.HeaderBar()
        view.add_top_bar(header)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        clamp = Adw.Clamp(
            maximum_size=440,
            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12,
        )
        view.set_content(clamp)

        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=12,
            valign=Gtk.Align.START,
        )
        clamp.set_child(outer)

        hint = Gtk.Label(
            label="What should this row carry into your mixes?",
            wrap=True, xalign=0,
        )
        hint.add_css_class("dim-label")
        outer.append(hint)

        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class("boxed-list")
        outer.append(listbox)

        app_row = Adw.ActionRow(
            title="Application",
            subtitle="Follows every stream an app plays, now and later",
            activatable=True,
        )
        app_row.add_prefix(
            Gtk.Image.new_from_icon_name("applications-multimedia-symbolic")
        )
        app_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        app_row.connect(
            "activated", lambda _r: self._nav.push(self._build_picker_page()),
        )
        listbox.append(app_row)

        device_row = Adw.ActionRow(
            title="Capture Device",
            subtitle="A microphone or line input, such as a headset mic",
            activatable=True,
        )
        device_row.add_prefix(
            Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        )
        device_row.add_suffix(Gtk.Image.new_from_icon_name("go-next-symbolic"))
        device_row.connect(
            "activated", lambda _r: self._nav.push(self._build_device_page()),
        )
        listbox.append(device_row)

        return page

    # ------------------------------------------------------- device picker
    def _build_device_page(self):
        page = Adw.NavigationPage(title="Pick Capture Device")

        view = Adw.ToolbarView()
        page.set_child(view)

        header = Adw.HeaderBar()
        view.add_top_bar(header)

        self._device_next_btn = Gtk.Button(label="Next")
        self._device_next_btn.add_css_class("suggested-action")
        self._device_next_btn.set_sensitive(False)
        self._device_next_btn.connect("clicked", self._on_device_next)
        header.pack_end(self._device_next_btn)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        view.set_content(scroll)

        clamp = Adw.Clamp(
            maximum_size=440,
            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12,
        )
        scroll.set_child(clamp)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clamp.set_child(outer)

        hint = Gtk.Label(
            label="Pick a microphone or line input. OpenWave mixes it into each "
                  "mix at the level you set, alongside the Wave's own mic.",
            wrap=True, xalign=0,
        )
        hint.add_css_class("dim-label")
        outer.append(hint)

        self._device_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self._device_list.add_css_class("boxed-list")
        self._device_list.connect("row-selected", self._on_device_row_selected)
        outer.append(self._device_list)

        self._populate_devices()
        return page

    def _populate_devices(self):
        # Already-bound nodes are filtered out rather than shown disabled: the
        # Wave's own mic is the built-in row, and a second row for it would
        # double the same audio into every mix.
        devices = [
            d for d in list_capture_sources() if d["name"] not in self._exclude_nodes
        ]
        if not devices:
            empty = Adw.ActionRow(title="No other capture devices")
            empty.set_subtitle(
                "Connect a headset or microphone, then open this dialog again"
            )
            empty.set_sensitive(False)
            self._device_list.append(empty)
            return
        for device in devices:
            row = Adw.ActionRow(title=device["description"])
            # The node name disambiguates two inputs on one card that share a
            # description, and is what actually gets persisted.
            row.set_subtitle(device["name"])
            row.add_prefix(
                Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
            )
            row._device = device  # noqa: SLF001
            self._device_list.append(row)

    def _on_device_row_selected(self, _box, row):
        self._selected_device = (
            getattr(row, "_device", None) if row is not None else None
        )
        self._device_next_btn.set_sensitive(self._selected_device is not None)

    def _on_device_next(self, _btn):
        if not self._selected_device:
            return
        self._nav.push(self._build_config_page(
            default_name=self._selected_device["description"],
            default_icon="microphone-sensitivity-high-symbolic",
            on_confirm=self._on_device_confirm,
        ))

    def _on_device_confirm(self, _btn):
        if not self._selected_device:
            return
        name = (
            self._name_row.get_text().strip()
            or self._selected_device["description"]
        )
        self.emit(
            "device-source-confirmed",
            name, self._selected_device["name"], self._selected_icon,
        )
        self.close()

    # ------------------------------------------------------------ page 1
    def _build_picker_page(self):
        page = Adw.NavigationPage(title="Pick Application")

        view = Adw.ToolbarView()
        page.set_child(view)

        header = Adw.HeaderBar()
        view.add_top_bar(header)

        cancel_btn = Gtk.Button(label="Cancel")
        cancel_btn.connect("clicked", lambda _: self.close())
        header.pack_start(cancel_btn)

        self._next_btn = Gtk.Button(label="Next")
        self._next_btn.add_css_class("suggested-action")
        self._next_btn.set_sensitive(False)
        self._next_btn.connect("clicked", self._on_next)
        header.pack_end(self._next_btn)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        view.set_content(scroll)

        clamp = Adw.Clamp(
            maximum_size=440,
            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12,
        )
        scroll.set_child(clamp)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        clamp.set_child(outer)

        hint = Gtk.Label(
            label="Pick an application that's currently playing audio. "
                  "OpenWave will route any future streams from this app through the new source row.",
            wrap=True, xalign=0,
        )
        hint.add_css_class("dim-label")
        outer.append(hint)

        self._listbox = Gtk.ListBox()
        self._listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self._listbox.add_css_class("boxed-list")
        self._listbox.connect("row-selected", self._on_row_selected)
        outer.append(self._listbox)

        self._populate_apps()
        return page

    def _populate_apps(self):
        streams = list_audio_streams()
        apps = {}
        for s in streams:
            apps.setdefault(s["app_name"], []).append(s)

        if not apps:
            empty = Adw.ActionRow(title="No audio streams playing")
            empty.set_subtitle("Start playback in an app, then click + Add Source again")
            empty.set_sensitive(False)
            self._listbox.append(empty)
            return

        for app_name in sorted(apps.keys()):
            row = Adw.ActionRow(title=app_name)
            sample = apps[app_name][0].get("media_name") or apps[app_name][0].get("node_name", "")
            if sample:
                row.set_subtitle(sample)
            row.add_prefix(Gtk.Image.new_from_icon_name("applications-multimedia-symbolic"))
            row._app_name = app_name  # noqa: SLF001
            self._listbox.append(row)

    def _on_row_selected(self, _box, row):
        if row is None:
            self._selected_app = None
            self._next_btn.set_sensitive(False)
            return
        self._selected_app = getattr(row, "_app_name", None)
        self._next_btn.set_sensitive(self._selected_app is not None)

    def _on_next(self, _btn):
        if not self._selected_app:
            return
        self._nav.push(self._build_config_page())

    # ------------------------------------------------------------ page 2
    def _build_config_page(self, *, default_name=None, default_icon=None,
                           on_confirm=None):
        """Shared final page for every source flow.

        Every argument defaults to the app-picker behaviour, so an untouched
        `self._build_config_page()` call site keeps working verbatim — which
        matters, because the manual-app-entry flow lands on this same page.
        """
        page = Adw.NavigationPage(title="Name and Icon")

        view = Adw.ToolbarView()
        page.set_child(view)

        header = Adw.HeaderBar()
        view.add_top_bar(header)

        add_btn = Gtk.Button(label="Add Source")
        add_btn.add_css_class("suggested-action")
        add_btn.connect("clicked", on_confirm or self._on_confirm)
        header.pack_end(add_btn)

        scroll = Gtk.ScrolledWindow(vexpand=True)
        view.set_content(scroll)

        clamp = Adw.Clamp(
            maximum_size=440,
            margin_start=12, margin_end=12, margin_top=12, margin_bottom=12,
        )
        scroll.set_child(clamp)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        clamp.set_child(outer)

        # Name group
        name_group = Adw.PreferencesGroup(title="Name")
        outer.append(name_group)

        self._name_row = Adw.EntryRow(title="Source name")
        self._name_row.set_text(default_name or self._selected_app or "")
        name_group.add(self._name_row)

        # Icon picker
        icon_group = Adw.PreferencesGroup(title="Icon")
        outer.append(icon_group)

        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.SINGLE,
            max_children_per_line=6,
            min_children_per_line=4,
            column_spacing=6,
            row_spacing=6,
            margin_start=4, margin_end=4, margin_top=8, margin_bottom=8,
            homogeneous=True,
        )
        flow.add_css_class("openwave-icon-picker")
        first_child = None
        preselect = None
        for icon_name, tooltip in ICON_CHOICES:
            btn = Gtk.Image.new_from_icon_name(icon_name)
            btn.set_pixel_size(28)
            child = Gtk.FlowBoxChild()
            child.set_child(btn)
            child.set_tooltip_text(tooltip)
            child._icon_name = icon_name  # noqa: SLF001
            flow.append(child)
            if first_child is None:
                first_child = child
            if icon_name == default_icon:
                preselect = child
        flow.connect("selected-children-changed", self._on_icon_selected)
        icon_group.add(flow)

        # Falls back to the first choice, which is what every existing caller
        # got and still gets.
        chosen = preselect or first_child
        if chosen is not None:
            flow.select_child(chosen)
            self._selected_icon = chosen._icon_name  # noqa: SLF001

        return page

    def _on_icon_selected(self, flow):
        sel = flow.get_selected_children()
        if sel:
            self._selected_icon = getattr(sel[0], "_icon_name", self._selected_icon)

    def _on_confirm(self, _btn):
        if not self._selected_app:
            return
        name = self._name_row.get_text().strip() or self._selected_app
        self.emit("source-confirmed", name, self._selected_app, self._selected_icon)
        self.close()
