"""Platform selection: one shared core, one platform module.

`impl` is the active platform (darija_subs.platform.mac or .win),
picked from sys.platform. DARIJA_SUBS_PLATFORM=mac|win overrides —
that is how the Windows/Qt stack is exercised on macOS (the Qt overlay,
picker and GUI run fine there; only the WinRT OCR and the
capture-exclusion syscall are Windows-only).

Each platform module exposes the same surface:
    DEFAULT_FONT_SIZE   float
    SETUP_HINT          str, how to fetch models on this platform
    check_permission()  -> None | error message (exit 3)
    display_bounds()    -> [(x, y, w, h), ...] | None (None = unknown)
    auto_region(band)   -> (x, y, w, h) | None (None = resolve in run_gui)
    make_backend(args, region, dpr) -> pipeline backend (see pipeline.py)
    run_gui(args, cfg, region, dpr, log, region_from_config) -> exit code
    gui_main(argv)      -> control-panel entry point
"""

import importlib
import os
import sys

name = os.environ.get("DARIJA_SUBS_PLATFORM") or (
    "mac" if sys.platform == "darwin" else "win")
if name not in ("mac", "win"):
    raise RuntimeError(f"DARIJA_SUBS_PLATFORM must be 'mac' or 'win', got {name!r}")

impl = importlib.import_module("." + name, __package__)
