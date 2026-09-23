"""
4.1.0: the countdown card's placement, size modes and remembered position,
plus a run of the real window code against a stand-in for tkinter (tkinter
itself isn't installed in the build sandbox, so this checks the logic and
wiring -- not how it looks). Run: python3 test_overlay_card.py
"""
import os, sys, tempfile, types
import focus_overlay as fo

PASS = FAIL = 0
def check(label, ok):
    global PASS, FAIL
    PASS, FAIL = (PASS + 1, FAIL) if ok else (PASS, FAIL + 1)
    print(f"  [{'OK' if ok else 'FAIL'}]{'   ' if ok else ' '}{label}")

print("Test 1: placement")
check("default: bottom-right, above the taskbar", fo.default_position(240, 58, 1920, 1080) == (1920 - 240 - 16, 1080 - 58 - 56))
check("a spot off-screen is pulled back on", fo.clamp_position(5000, -40, 300, 132, 1920, 1080) == (1620, 0))
check("a spot on-screen is kept", fo.clamp_position(100, 200, 300, 132, 1920, 1080) == (100, 200))
p = os.path.join(tempfile.mkdtemp(), "pos.json")
check("no saved spot yet -> None", fo.load_position(p) is None)
check("position saved and read back", fo.save_position(123, 456, p) and fo.load_position(p) == (123, 456))
open(p, "w").write("garbage")
check("a damaged file is ignored, not a crash", fo.load_position(p) is None)

print("Test 2: size modes")
check("no session -> slim 'ready' bar", fo.card_mode("idle", False) == "idle")
check("server unreachable -> slim bar too", fo.card_mode("error", False) == "idle")
check("session -> full card", fo.card_mode("on_track", False) == "full" and fo.card_mode("drifted", False) == "full")
check("collapsed -> tiny pill, whatever the state", fo.card_mode("on_track", True) == "mini")
check("slim bar text", fo.idle_line("No focus session running", "idle") == "● Ready · no focus session"
      and fo.idle_line("x", "error") == "● Can't reach Jarvis" and fo.idle_line("Watching your screen", "idle").startswith("👁"))

print("Test 3: the window code, run against a stand-in tkinter")
log = {"geometry": [], "after": 0}
WIDGETS = []
class W:
    def __init__(self, *a, **k): self.cfg = dict(k); self.binds = {}; self.packed = False; WIDGETS.append(self)
    def pack(self, **k): self.packed = True
    def pack_forget(self): self.packed = False
    def place(self, **k): pass
    def bind(self, ev, fn): self.binds[ev] = fn
    def config(self, **k): self.cfg.update(k)
    configure = config
class Tk(W):
    x, y = 0, 0
    def title(self, t): pass
    def overrideredirect(self, b): pass
    def attributes(self, *a): pass
    def winfo_screenwidth(self): return 1920
    def winfo_screenheight(self): return 1080
    def winfo_x(self): return self.x
    def winfo_y(self): return self.y
    def geometry(self, g):
        log["geometry"].append(g)
        if "+" in g:
            parts = g.split("+"); self.x, self.y = int(parts[-2]), int(parts[-1])
    def after(self, ms, fn): log["after"] += 1; log["next"] = fn
    def mainloop(self): pass
    def destroy(self): log["destroyed"] = True
fake = types.ModuleType("tkinter")
fake.Tk, fake.Frame, fake.Label, fake.Button = Tk, W, W, W
fake.messagebox = types.SimpleNamespace(askyesno=lambda *a, **k: True)
sys.modules["tkinter"] = fake; sys.modules["tkinter.messagebox"] = fake.messagebox
fo.POSITION_FILE = os.path.join(tempfile.mkdtemp(), "pos.json")
seq = [(None, "offline"), ({"status": {"active": False}}, None), ({"status": {"active": True, "task": "essay", "remaining_seconds": 600}}, None)]
fo.fetch_status = lambda *a, **k: seq.pop(0) if len(seq) > 1 else seq[0]
fo.load_position = lambda path=None: None
fo.save_position = lambda x, y, path=None: log.setdefault("saved", []).append((x, y)) or True
try:
    fo.run(); ok = True; err = None
except Exception as e:
    ok, err = False, repr(e)
check(f"builds and polls without errors{'' if ok else ': ' + str(err)}", ok)
first = log["geometry"][0] if log["geometry"] else ""
check("opens bottom-right (above the taskbar)", first == f"240x58+{1920 - 240 - 16}+{1080 - 58 - 56}")
check("keeps polling", log["after"] >= 1)
log["next"](); log["next"]()                               # "ready", then a session starts
g = log["geometry"][-1]
check("session starts -> grows to the full card, same bottom-right corner",
      g == f"300x132+{1920 - 16 - 300}+{1080 - 56 - 132}")
label = lambda text: next(w for w in WIDGETS if w.cfg.get("text") == text)
label("–").binds["<Button-1>"](None)
check("collapse -> tiny pill, corner kept", log["geometry"][-1] == f"150x30+{1920 - 16 - 150}+{1080 - 56 - 30}")
check("the pill still shows the timer", any(w.cfg.get("text") == "⏱ 10:00" for w in WIDGETS))
label("▢").binds["<Button-1>"](None)
check("expand again -> full card", log["geometry"][-1].startswith("300x132+"))
bar = next(w for w in WIDGETS if w.cfg.get("cursor") == "fleur" and "<ButtonPress-1>" in w.binds)
root = next(w for w in WIDGETS if isinstance(w, Tk))
bar.binds["<ButtonPress-1>"](types.SimpleNamespace(x_root=root.x + 10, y_root=root.y + 5))
bar.binds["<B1-Motion>"](types.SimpleNamespace(x_root=410, y_root=305))
bar.binds["<ButtonRelease-1>"](None)
check("drag by the title bar moves it, and the spot is saved", (root.x, root.y) == (400, 300) and log["saved"][-1] == (400, 300))
label("✕").binds["<Button-1>"](None)
check("✕ asks, then closes Jarvis (and remembers the spot)", log.get("destroyed") is True)
print(f"\n{PASS} passed, {FAIL} failed")
