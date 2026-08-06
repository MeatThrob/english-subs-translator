"""darija-subs: live English-subtitle -> translated overlay (200+ languages).

Pipeline (all local, no network at runtime):
  screen region capture -> on-device OCR (English)
  -> CTranslate2 int8 translation (Terjman for Darija, NLLB-200 for
     every other language) with a per-language disk cache
  -> click-through overlay that mirrors the English show/hide.

One shared core, two platforms (auto-detected):
  macOS   — Quartz capture, Apple Vision OCR, AppKit overlay (verified)
  Windows — mss capture, WinRT OCR, Qt overlay (experimental)

Usage (run.sh on macOS, windows\\run.bat on Windows):
  run.sh --pick            # first run: drag-select the subtitle area, then it starts
  run.sh                   # reuse saved region + language
  run.sh --lang Spanish    # translate into another language (name or NLLB code)
  run.sh --list-langs      # print all 202 supported languages
  run.sh --headless        # no overlay window; prints/logs events (testing)
"""

import argparse
import os
import sys

from . import langs
from .pipeline import (CACHE_DIR, CONFIG_PATH, LEGACY_CACHE, MODELS_DIR,
                       EventLog, Pipeline, load_config, save_config,
                       saved_region)
from .platform import impl as plat
from .translate import migrate_legacy_cache


def parse_args(argv):
    p = argparse.ArgumentParser(prog="darija-subs", description=__doc__)
    p.add_argument("--pick", action="store_true",
                   help="manually drag-select a region instead of the "
                        "automatic lower-half scan")
    p.add_argument("--band", type=float, default=None,
                   help="auto-scan band: fraction of screen height where the "
                        "scan starts (0.5 = lower half). Passing it "
                        "explicitly overrides a saved/picked region")
    p.add_argument("--region", type=str,
                   help="x,y,w,h in global screen points (top-left origin; "
                        "logical coordinates on Windows)")
    p.add_argument("--dpr", type=float, default=None,
                   help="devicePixelRatio for --region (Windows scaled "
                        "displays; default: saved or 1.0)")
    p.add_argument("--position", choices=["above", "below", "cover"], default=None,
                   help="where the translation goes relative to the English "
                        "(cover = hide the English)")
    p.add_argument("--font-size", type=float, default=None)
    p.add_argument("--lang", default=None,
                   help="target language: NLLB code (spa_Latn) or a name "
                        "('Spanish', 'Darija'). Default: last used, else Darija")
    p.add_argument("--list-langs", action="store_true",
                   help="print the 202 supported languages and exit")
    p.add_argument("--model", default=None,
                   help="override the model dir (default: auto-chosen "
                        "per language — Terjman for Darija, NLLB otherwise)")
    p.add_argument("--beam", type=int, default=2,
                   help="beam size (2 = best quality/speed tradeoff, measured)")
    p.add_argument("--ocr", default="auto",
                   choices=["auto", "winrt", "vision", "rapid"],
                   help="OCR engine (auto = the platform default)")
    p.add_argument("--interval", type=int, default=50, help="poll interval ms")
    p.add_argument("--hide-frames", type=int, default=2,
                   help="consecutive empty OCR reads before hiding the translation")
    p.add_argument("--turbo", action="store_true",
                   help="lowest latency: near-continuous OCR (~25ms interval) "
                        "and hide on the first empty read. Higher CPU use; "
                        "rare flicker possible on washed-out scenes")
    p.add_argument("--fast-ocr", action="store_true",
                   help="Vision fast mode (macOS only, lower accuracy)")
    p.add_argument("--headless", action="store_true", help="no overlay window; log events only")
    p.add_argument("--log", type=str, default=None, help="append JSONL events to this file")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--quiet", action="store_true", help="don't echo events to stdout")
    return p.parse_args(argv)


def resolve_language(args, cfg) -> str | None:
    """--lang (name or code) > saved config > Darija. None = bad input."""
    if args.lang:
        code = langs.resolve(args.lang)
        if code is None:
            print(f"ERROR: unknown language {args.lang!r}. "
                  f"Try --list-langs.", file=sys.stderr)
        return code
    saved = cfg.get("target_lang", langs.DEFAULT_TARGET)
    return saved if saved in langs.LANGS else langs.DEFAULT_TARGET


def finalize_args(args, cfg) -> int:
    """Resolve language + model and migrate the legacy cache. Shared by
    the CLI and the GUIs on both platforms so all agree on routing.
    Returns 0 or an error exit code."""
    code = resolve_language(args, cfg)
    if code is None:
        return 2
    args.lang = code
    if args.model is None:
        args.model = langs.pick_model(code, MODELS_DIR)
    if not os.path.isdir(args.model):
        print(
            f"ERROR: translation model not found at {args.model}\n"
            f"Run: {plat.SETUP_HINT}",
            file=sys.stderr,
        )
        return 2
    if not args.no_cache:
        migrate_legacy_cache(
            LEGACY_CACHE, CACHE_DIR,
            os.path.join(MODELS_DIR, langs.SPECIALIST_MODELS["ary_Arab"]),
        )
    return 0


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])
    cfg = load_config()

    if args.list_langs:
        for code in sorted(langs.LANGS, key=lambda c: langs.LANGS[c][0]):
            print(f"{code:10s}  {langs.label(code)}")
        return 0

    if args.turbo:
        if args.interval == 50:
            args.interval = 25
        if args.hide_frames == 2:
            args.hide_frames = 1

    if args.position is None:
        args.position = cfg.get("position", "above")
    if args.font_size is None:
        args.font_size = cfg.get("font_size", plat.DEFAULT_FONT_SIZE)

    rc = finalize_args(args, cfg)
    if rc:
        return rc

    err = plat.check_permission()
    if err:
        print(err, file=sys.stderr)
        return 3

    # Region precedence (both platforms): --region > --pick > explicit
    # --band > saved/picked region from config.json > lower-half auto-scan.
    region = None
    dpr = args.dpr
    region_from_config = False
    if args.region:
        region = tuple(float(v) for v in args.region.split(","))
    elif not args.pick:
        if args.band is None:
            region = saved_region(cfg)
            if region is not None:
                region_from_config = True
                if dpr is None:
                    dpr = float(cfg.get("dpr", 1.0))
        if region is None:
            region_from_config = False
            # macOS resolves this immediately (Quartz needs no app);
            # Windows returns None here and the Qt glue resolves it
            # once a QApplication exists.
            region = plat.auto_region(0.5 if args.band is None else args.band)

    log = EventLog(args.log, echo=not args.quiet)

    if args.headless:
        if region is None:
            print("ERROR: --headless needs --region or a saved config", file=sys.stderr)
            return 2
        backend = plat.make_backend(args, region, dpr)
        pipe = Pipeline(region, args, log, backend)
        try:
            pipe.run()  # blocking, Ctrl+C to stop
        except KeyboardInterrupt:
            pass
        return 0

    return plat.run_gui(args, cfg, region, dpr, log,
                        region_from_config=region_from_config)


if __name__ == "__main__":
    sys.exit(main())
