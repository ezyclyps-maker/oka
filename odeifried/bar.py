"""The Intent Bar: press Super+Space, type what you want, press Enter.

It is a thin window. The work happens in a separate `odeifried run` process, so the kill switch
can end it instantly and a crash in a task never takes the bar down."""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "org.odeifried.Bar"
CSS = b"""
#bar { background: rgba(17, 19, 38, 0.97); border-radius: 16px; border: 1px solid rgba(129, 140, 248, 0.55); }
#bar entry { background: transparent; border: none; box-shadow: none; color: #ffffff; font-size: 19px;
             caret-color: #22d3ee; padding: 6px 4px; }
#bar entry:focus { box-shadow: none; }
#hint { color: #8b93c9; font-size: 11px; }
#you { color: #a5b4fc; font-size: 13px; font-weight: bold; }
#step { color: #9aa3d6; font-size: 12px; }
#good { color: #ffffff; font-size: 14px; }
#bad { color: #fca5a5; font-size: 14px; }
#ask { background: rgba(250, 204, 21, 0.12); border-radius: 10px; border: 1px solid rgba(250, 204, 21, 0.5); padding: 10px; }
#ask label { color: #fde68a; font-size: 13px; }
#bar button { border-radius: 8px; padding: 4px 14px; background: rgba(129,140,248,0.18); color: #e0e7ff;
              border: 1px solid rgba(129,140,248,0.4); box-shadow: none; text-shadow: none; }
#bar button:hover { background: rgba(129,140,248,0.35); }
#bar button#allow { background: #4f46e5; color: #ffffff; }
#bar button#stop { background: rgba(239,68,68,0.25); border-color: rgba(239,68,68,0.6); color: #fecaca; }
#bar button.on { background: #4f46e5; color: #ffffff; }
"""
ROUTE = {"instant": "instant", "skill": "from memory", "ai": "AI model", "mixed": "mixed", "chat": "chat"}


class Bar(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.win = None
        self.proc = None
        self.chat_mode = False
        self.history: list[dict] = []
        self.stream_label = None

    # ---- window
    def do_startup(self):
        Gtk.Application.do_startup(self)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        stop = Gio.SimpleAction.new("stop", None)
        stop.connect("activate", lambda *_: self.stop())
        self.add_action(stop)
        self.hold()                                   # stay alive in the background, ready for the hotkey

    def do_activate(self):
        if self.win is None:
            self.build()
        if self.win.is_visible() and self.win.is_active():
            self.win.hide()
        else:
            self.place()
            self.win.show_all()
            self.stop_btn.set_visible(self.proc is not None)
            self.win.present_with_time(Gtk.get_current_event_time() or Gdk.CURRENT_TIME)
            self.entry.grab_focus()

    def build(self):
        w = self.win = Gtk.ApplicationWindow(application=self, title="Odeifried")
        w.set_decorated(False)
        w.set_keep_above(True)
        w.set_skip_taskbar_hint(True)
        w.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        w.set_app_paintable(True)
        visual = w.get_screen().get_rgba_visual()
        if visual:
            w.set_visual(visual)
        w.connect("key-press-event", self.on_key)
        w.connect("delete-event", lambda *_: w.hide() or True)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, name="bar")
        outer.set_border_width(14)
        row = Gtk.Box(spacing=10)
        icon = Gtk.Image.new_from_icon_name("odeifried", Gtk.IconSize.DND)
        icon.set_pixel_size(30)
        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Tell Odeifried what to do...")
        self.entry.connect("activate", self.on_enter)
        self.mode_btn = Gtk.Button(label="Do")
        self.mode_btn.set_tooltip_text("Do = control the computer.  Chat = just talk (Tab switches)")
        self.mode_btn.connect("clicked", lambda *_: self.toggle_mode())
        self.stop_btn = Gtk.Button(label="Stop", name="stop")
        self.stop_btn.connect("clicked", lambda *_: self.stop())
        row.pack_start(icon, False, False, 0)
        row.pack_start(self.entry, True, True, 0)
        row.pack_start(self.stop_btn, False, False, 0)
        row.pack_start(self.mode_btn, False, False, 0)
        outer.pack_start(row, False, False, 0)

        self.log = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroll.set_max_content_height(380)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.add(self.log)
        outer.pack_start(self.scroll, True, True, 0)
        self.hint = Gtk.Label(name="hint", xalign=0)
        self.hint.set_text("Enter: run   Tab: Do / Chat   Esc: hide   Super+Esc: stop the AI   try: help")
        outer.pack_start(self.hint, False, False, 0)
        w.add(outer)
        w.set_default_size(720, -1)

    def place(self):
        display = Gdk.Display.get_default()
        mon = display.get_primary_monitor() or display.get_monitor(0)
        g = mon.get_geometry()
        width = min(720, g.width - 40)
        self.win.resize(width, 1)
        self.win.move(g.x + (g.width - width) // 2, g.y + int(g.height * 0.22))

    # ---- transcript
    def add(self, text: str, name: str, selectable: bool = False) -> Gtk.Label:
        lab = Gtk.Label(label=text, name=name, xalign=0)
        lab.set_line_wrap(True)
        lab.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lab.set_max_width_chars(80)
        lab.set_selectable(selectable)
        self.log.pack_start(lab, False, False, 0)
        lab.show()
        GLib.idle_add(self.scroll_down)
        return lab

    def scroll_down(self):
        adj = self.scroll.get_vadjustment()
        adj.set_value(adj.get_upper())
        self.win.resize(self.win.get_size()[0], 1)
        return False

    def toggle_mode(self):
        self.chat_mode = not self.chat_mode
        self.mode_btn.set_label("Chat" if self.chat_mode else "Do")
        ctx = self.mode_btn.get_style_context()
        ctx.add_class("on") if self.chat_mode else ctx.remove_class("on")
        self.entry.set_placeholder_text("Ask anything..." if self.chat_mode else "Tell Odeifried what to do...")
        self.entry.grab_focus()

    def on_key(self, _w, ev):
        if ev.keyval == Gdk.KEY_Escape:
            self.win.hide()
            return True
        if ev.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab) and self.entry.is_focus():
            self.toggle_mode()
            return True
        return False

    # ---- running a request
    def on_enter(self, entry):
        text = entry.get_text().strip()
        if not text:
            return
        if text.lower() in {"stop", "cancel"} and self.proc:
            entry.set_text("")
            return self.stop()
        if self.proc:
            self.hint.set_text("Still working on the last request. Stop it first (Super+Esc).")
            return
        if text.lower() in {"clear", "cls"}:
            for child in self.log.get_children():
                child.destroy()
            self.history.clear()
            entry.set_text("")
            return self.scroll_down()
        entry.set_text("")
        self.add(text, "you")
        cmd = [sys.executable, "-m", "odeifried.cli", "chat" if self.chat_mode else "run", text, "--events"]
        self.last_text, self.reply, self.stream_label = text, [], None
        self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, bufsize=1, start_new_session=True)
        self.stop_btn.show()
        GLib.io_add_watch(self.proc.stdout, GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP, self.on_output)

    def on_output(self, stream, condition):
        line = stream.readline()
        if not line:
            self.finish()
            return False
        try:
            self.on_event(json.loads(line))
        except ValueError:
            pass
        return True

    def on_event(self, e: dict):
        kind = e.get("type")
        if kind == "task" and e.get("total", 1) > 1:
            self.add(f"{e['index']}/{e['total']}  {e['text']}", "you")
        elif kind == "thinking":
            self.hint.set_text("Thinking... (the AI model is working; Super+Esc stops it)")
        elif kind == "step":
            self.add(f"• {e['tool'].replace('_', ' ')}", "step")
        elif kind == "output":
            self.add(e["text"][:400], "good" if e.get("ok", True) else "bad", selectable=True)
        elif kind == "refused":
            self.add(f"Refused: {e['reason']}", "bad")
        elif kind == "denied":
            self.add("Cancelled: you said no.", "step")
        elif kind == "confirm":
            self.ask(e)
        elif kind == "token":
            self.reply.append(e["text"])
            if self.stream_label is None:
                self.stream_label = self.add("", "good", selectable=True)
            self.stream_label.set_text("".join(self.reply))
            self.scroll_down()
        elif kind == "done":
            if e.get("chat"):
                self.history += [{"role": "user", "content": self.last_text},
                                 {"role": "assistant", "content": e.get("summary", "")}]
                self.history = self.history[-12:]
            elif e.get("route") == "ai" or not e.get("ok"):
                self.add(e.get("summary", ""), "good" if e.get("ok") else "bad", selectable=True)
            took = f"{e.get('seconds', 0):.1f}s" if "seconds" in e else ""
            self.hint.set_text(f"{'Done' if e.get('ok') else 'Not done'}  {took}  ({ROUTE.get(e.get('route'), '')})"
                               "   Enter: run   Tab: Do / Chat   Esc: hide")

    def ask(self, e: dict):
        """An Ask-tier action: show exactly what will happen and wait for a click. Default is No."""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, name="ask")
        lab = Gtk.Label(xalign=0)
        lab.set_line_wrap(True)
        lab.set_max_width_chars(78)
        lab.set_text(f"Permission needed: {e['reason']}\n\n{e['text']}")
        buttons = Gtk.Box(spacing=8)
        deny = Gtk.Button(label="Don't allow")
        allow = Gtk.Button(label="Allow", name="allow")

        def answer(ok: bool):
            if self.proc and self.proc.stdin:
                try:
                    self.proc.stdin.write(json.dumps({"confirm": e["id"], "allow": ok}) + "\n")
                    self.proc.stdin.flush()
                except OSError:
                    pass
            box.destroy()
            self.entry.grab_focus()
        deny.connect("clicked", lambda *_: answer(False))
        allow.connect("clicked", lambda *_: answer(True))
        buttons.pack_end(allow, False, False, 0)
        buttons.pack_end(deny, False, False, 0)
        box.pack_start(lab, False, False, 0)
        box.pack_start(buttons, False, False, 0)
        self.log.pack_start(box, False, False, 0)
        box.show_all()
        self.place()
        self.win.show()
        self.win.present()
        deny.grab_focus()
        GLib.idle_add(self.scroll_down)

    def finish(self):
        if self.proc:
            self.proc.wait()
            self.proc = None
        if self.win:
            self.stop_btn.hide()

    def stop(self):
        """Kill switch: end the worker and everything it started, immediately."""
        if not self.proc:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGKILL)
        except OSError:
            pass
        if self.win:
            self.add("Stopped. Say \"undo\" to reverse any file changes.", "bad")
            self.hint.set_text("Stopped.")


def main(argv: list[str] | None = None) -> int:
    return Bar().run(argv if argv is not None else sys.argv)


if __name__ == "__main__":
    sys.exit(main())
