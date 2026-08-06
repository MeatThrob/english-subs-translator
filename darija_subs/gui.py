"""Subtitle Translator — desktop control panel entry point.

Dispatches to the platform implementation (AppKit on macOS, Qt/PySide6
on Windows — override with DARIJA_SUBS_PLATFORM=mac|win):

    macOS:    ./gui.sh            (or: .venv/bin/python -m darija_subs.gui)
    Windows:  windows\\gui.bat    (or: .venv\\Scripts\\python -m darija_subs.gui)
"""

import sys

from .platform import impl


def main(argv=None):
    return impl.gui_main(argv)


if __name__ == "__main__":
    sys.exit(main())
