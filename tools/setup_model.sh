#!/bin/zsh
# One-time model download + int8 conversion. Re-run safe (--force).
set -e
cd "$(dirname "$0")/.."

echo "== Terjman-Nano-v2.1 (serves Darija: specialized, 77M, fastest)"
.venv/bin/ct2-transformers-converter \
  --model abdeljalilELmajjodi/Terjman-Nano-v2.1 \
  --output_dir models/terjman-nano-int8 --quantization int8 --force
.venv/bin/python -c "
from transformers import AutoTokenizer
AutoTokenizer.from_pretrained('abdeljalilELmajjodi/Terjman-Nano-v2.1').save_pretrained('models/terjman-nano-int8')"

echo "== facebook/nllb-200-distilled-600M (serves the other 201 languages)"
.venv/bin/ct2-transformers-converter \
  --model facebook/nllb-200-distilled-600M \
  --output_dir models/nllb-600m-int8 --quantization int8 --force
.venv/bin/python -c "
from transformers import AutoTokenizer
AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-600M').save_pretrained('models/nllb-600m-int8')"

echo "Done. Models in models/"
