#!/bin/zsh
# Subtitle Translator — desktop control panel (On/Off window)
cd "$(dirname "$0")"
exec .venv/bin/python -m darija_subs.gui "$@"
