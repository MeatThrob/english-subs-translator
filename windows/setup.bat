@echo off
rem darija-subs Windows setup: venv + dependencies + model downloads.
rem Everything lands at the REPO ROOT (one level up from this script):
rem the venv in .venv\ and both translation models in models\.
rem The model weights are NOT in this repo (their licenses are
rem non-commercial and separate from the code's MIT license); this
rem script downloads them from Hugging Face and converts them to int8.
rem Re-run safe.
cd /d "%~dp0.."

where py >nul 2>nul
if errorlevel 1 (
  echo Python launcher not found. Install Python 3.12 from python.org
  echo and check "Add python.exe to PATH" during install.
  exit /b 1
)

rem winsdk (the Windows OCR bindings) ships wheels for Python 3.10-3.12,
rem so prefer 3.12 and fall back down, not up.
py -3.12 -m venv .venv 2>nul || py -3.11 -m venv .venv 2>nul || py -3.10 -m venv .venv 2>nul || py -3 -m venv .venv
if errorlevel 1 (
  echo Failed to create venv. Install Python 3.12 from python.org.
  exit /b 1
)
.venv\Scripts\python -c "import sys; print('venv Python', sys.version.split()[0])"

.venv\Scripts\python -m pip install --upgrade pip
.venv\Scripts\pip install -r windows\requirements.txt
if errorlevel 1 (
  echo pip install failed. If the error mentions winsdk, your Python is
  echo probably newer than 3.12 - install Python 3.12 and re-run.
  exit /b 1
)

rem ---- models: BOTH are needed for the 202-language support ---------
rem   terjman-nano-int8  serves Darija (the default target)
rem   nllb-600m-int8     serves the other 201 languages
if exist models\terjman-nano-int8\model.bin if exist models\nllb-600m-int8\model.bin goto :models_done

echo.
echo == torch is needed only for the one-time conversions (~200 MB).
.venv\Scripts\pip install torch
if errorlevel 1 (
  echo torch install failed - check your internet connection.
  exit /b 1
)

if exist models\terjman-nano-int8\model.bin goto :terjman_done
echo.
echo == Downloading Terjman-Nano-v2.1 and converting to int8 (one time,
echo == ~300 MB download, ~80 MB on disk). Serves Darija.
.venv\Scripts\ct2-transformers-converter --model abdeljalilELmajjodi/Terjman-Nano-v2.1 --output_dir models\terjman-nano-int8 --quantization int8 --force
if errorlevel 1 (
  echo Terjman conversion failed - check your internet connection.
  exit /b 1
)
.venv\Scripts\python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('abdeljalilELmajjodi/Terjman-Nano-v2.1').save_pretrained('models/terjman-nano-int8')"
:terjman_done

if exist models\nllb-600m-int8\model.bin goto :nllb_done
echo.
echo == Downloading facebook/nllb-200-distilled-600M and converting to
echo == int8 (one time, ~2.5 GB download, ~635 MB on disk). Serves the
echo == other 201 languages - without it, every non-Darija language
echo == fails at model load.
.venv\Scripts\ct2-transformers-converter --model facebook/nllb-200-distilled-600M --output_dir models\nllb-600m-int8 --quantization int8 --force
if errorlevel 1 (
  echo NLLB conversion failed - check your internet connection.
  exit /b 1
)
.venv\Scripts\python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('facebook/nllb-200-distilled-600M').save_pretrained('models/nllb-600m-int8')"
:nllb_done
:models_done

echo.
echo Setup complete. Now run the self-tests (IMPORTANT - this port has
echo not been verified on Windows yet; these tests are how you find out;
echo see windows\FINISHING.md for what each one proves):
echo   .venv\Scripts\python windows\tests\bench_translate.py
echo   .venv\Scripts\python windows\tests\bench_ocr.py
echo   .venv\Scripts\python windows\tests\test_downscale.py
echo   .venv\Scripts\python windows\tests\test_exclusion.py
echo   .venv\Scripts\python windows\tests\test_win_fonts.py
echo   .venv\Scripts\python windows\tests\e2e_test.py
echo Then start the app with:  windows\run.bat --pick
