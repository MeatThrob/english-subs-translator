"""End-to-end test: Qt fake player + real app, compare event timelines.
Portable — run it on the Windows box after setup (and it also runs on
macOS, which is how this port was validated before shipping).

Run: .venv\\Scripts\\python tests\\e2e_test.py
"""

import json
import os
import signal
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
if sys.prefix != sys.base_prefix:
    PY = sys.executable          # already inside a venv: keep using it
else:
    PY = os.path.join(
        ROOT, ".venv", "Scripts" if os.name == "nt" else "bin",
        "python.exe" if os.name == "nt" else "python",
    )
    if not os.path.exists(PY):
        PY = sys.executable

# Exercise the Windows/Qt platform stack even when validating on macOS.
ENV = {**os.environ, "DARIJA_SUBS_PLATFORM": "win"}

MAX_SHOW_LATENCY = 2.0
MAX_HIDE_LATENCY = 1.2

TMP = os.environ.get("TEMP", "/tmp")
PLAYER_LOG = os.path.join(TMP, "darija_e2e_player.jsonl")
APP_LOG = os.path.join(TMP, "darija_e2e_app.jsonl")
TRIGGER = os.path.join(TMP, "darija_e2e_go")


def screen_info():
    out = subprocess.check_output([PY, "-c", (
        "from PySide6.QtWidgets import QApplication;"
        "a=QApplication([]);s=a.primaryScreen();g=s.geometry();"
        "print(g.x(),g.y(),g.width(),g.height(),s.devicePixelRatio())"
    )], text=True)
    x, y, w, h, dpr = out.split()
    return int(x), int(y), int(w), int(h), float(dpr)


def read_log(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def norm(s):
    from darija_subs.textnorm import normalize
    return normalize(s).lower().rstrip(".!?").strip()


CONFIG_PATH = os.path.join(ROOT, "config.json")


def main():
    """Run the e2e but never leave the real config.json altered — the
    app under test saves the fake-player rect into it."""
    cfg0 = None
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, "rb") as f:
            cfg0 = f.read()
    try:
        return _run()
    finally:
        if cfg0 is not None:
            with open(CONFIG_PATH, "wb") as f:
                f.write(cfg0)
        elif os.path.exists(CONFIG_PATH):
            os.remove(CONFIG_PATH)


def _run():
    for p in (PLAYER_LOG, APP_LOG, TRIGGER):
        if os.path.exists(p):
            os.remove(p)

    sx, sy, sw, sh, dpr = screen_info()
    W, H = min(1000, sw - 80), 110
    X = sx + (sw - W) // 2
    Y = sy + sh - 320
    RECT = f"{X},{Y},{W},{H}"
    print(f"screen {sw}x{sh} dpr={dpr} -> test region {RECT}")

    player = subprocess.Popen(
        [PY, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "fake_player.py"),
         "--rect", RECT, "--log", PLAYER_LOG, "--trigger", TRIGGER],
        env=ENV)
    time.sleep(2)

    extra = [a for a in os.environ.get("E2E_EXTRA", "").split() if a]
    app = subprocess.Popen(
        [PY, "-m", "darija_subs.main", "--region", RECT, "--dpr", str(dpr),
         "--log", APP_LOG, "--quiet", "--no-cache"] + extra,
        cwd=ROOT, env=ENV)

    t0 = time.time()
    ready = False
    while time.time() - t0 < 90:
        if any(e["event"] == "ready" for e in read_log(APP_LOG)):
            ready = True
            break
        if app.poll() is not None:
            print("FAIL: app exited early with", app.returncode)
            player.terminate()
            return 1
        time.sleep(0.3)
    if not ready:
        print("FAIL: app never became ready")
        player.terminate(); app.terminate()
        return 1

    open(TRIGGER, "w").close()  # GO

    t0 = time.time()
    while time.time() - t0 < 90:
        if any(e["event"] == "done" for e in read_log(PLAYER_LOG)):
            break
        time.sleep(0.5)
    time.sleep(0.4)
    if os.name == "nt":
        app.terminate()
    else:
        app.send_signal(signal.SIGINT)
    player.wait(timeout=10)
    try:
        app.wait(timeout=10)
    except subprocess.TimeoutExpired:
        app.kill()

    pl = read_log(PLAYER_LOG)
    al = read_log(APP_LOG)
    shows = [e for e in pl if e["event"] == "show"]
    hides = [e for e in pl if e["event"] == "hide"]
    app_shows = [e for e in al if e["event"] == "show"]
    app_hides = [e for e in al if e["event"] == "hide"]

    ok = True
    lat_show, lat_hide = [], []
    used = set()
    print(f"player: {len(shows)} shows / {len(hides)} hides | "
          f"app: {len(app_shows)} shows / {len(app_hides)} hides")

    for e in shows:
        want = norm(e["text"])
        m = next((a for a in app_shows
                  if id(a) not in used and a["t"] >= e["t"] - 0.05
                  and a["t"] - e["t"] <= MAX_SHOW_LATENCY
                  and norm(a["en"]) == want), None)
        if m is None:
            print(f"  MISS show: {e['text']!r}")
            ok = False
        else:
            used.add(id(m))
            lat_show.append(m["t"] - e["t"])
            print(f"  show +{m['t']-e['t']:.2f}s ocr={m['ocr_ms']}ms "
                  f"tr={m['tr_ms']}ms | {e['text'][:40]!r} -> {m['ar'][:40]}")

    for e in hides:
        m = next((a for a in app_hides
                  if id(a) not in used and a["t"] >= e["t"] - 0.05
                  and a["t"] - e["t"] <= MAX_HIDE_LATENCY), None)
        if m is None:
            print(f"  MISS hide at t={e['t']:.2f}")
            ok = False
        else:
            used.add(id(m))
            lat_hide.append(m["t"] - e["t"])

    # Spurious-event guard: matching only proves the expected events
    # happened; a feedback loop (overlay visible to capture) shows up as
    # a storm of EXTRA show/hide flaps the matcher would ignore. Small
    # surplus is allowed for partial-line OCR refinements. (On macOS
    # validation runs the Qt overlay is NOT capture-excluded — that is
    # Windows-only — so cover mode is expected to trip this there.)
    extra_shows = len(app_shows) - len(lat_show)
    extra_hides = len(app_hides) - len(lat_hide)
    limit = max(2, len(shows) // 2)
    if extra_shows > limit or extra_hides > limit:
        print(f"  SPURIOUS: {extra_shows} unmatched app shows / "
              f"{extra_hides} unmatched hides (limit {limit}) — "
              f"overlay feedback or flapping")
        ok = False

    if lat_show:
        print(f"show latency: avg {sum(lat_show)/len(lat_show)*1000:.0f} ms,"
              f" max {max(lat_show)*1000:.0f} ms")
    if lat_hide:
        print(f"hide latency: avg {sum(lat_hide)/len(lat_hide)*1000:.0f} ms,"
              f" max {max(lat_hide)*1000:.0f} ms")
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
