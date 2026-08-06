#!/bin/zsh
# darija-subs launcher. First run: ./run.sh --pick
cd "$(dirname "$0")"
exec .venv/bin/python -m darija_subs.main "$@"
