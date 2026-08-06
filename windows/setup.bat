@echo off
rem darija-subs Windows setup: venv + dependencies + model download.
rem The model weights are NOT in this repo (their license is
rem non-commercial and separate from the code's MIT license); this
rem script downloads them from Hugging Face and converts them to int8.
rem Re-run safe.
cd /d "%~dp0"

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
.venv\Scripts\pip install -r requirements.txt
if errorlevel 1 (
  echo pip install failed. If the error mentions winsdk, your Python is
  echo probably newer than 3.12 - install Python 3.12 and re-run.
  exit /b 1
)

if exist models\terjman-nano-int8\model.bin goto :model_done
echo.
echo == Downloading Terjman-Nano-v2.1 and converting to int8 (one time).
echo == torch is needed only for this conversion (~200 MB download).
.venv\Scripts\pip install torch
.venv\Scripts\ct2-transformers-converter --model abdeljalilELmajjodi/Terjman-Nano-v2.1 --output_dir models\terjman-nano-int8 --quantization int8 --force
if errorlevel 1 (
  echo Model conversion failed - check your internet connection.
  exit /b 1
)
.venv\Scripts\python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('abdeljalilELmajjodi/Terjman-Nano-v2.1').save_pretrained('models/terjman-nano-int8')"
:model_done

echo.
echo Setup complete. Now run the self-tests (IMPORTANT - this port has
echo not been verified on Windows yet; these tests are how you find out):
echo   .venv\Scripts\python tests\bench_translate.py
echo   .venv\Scripts\python tests\bench_ocr.py
echo   .venv\Scripts\python tests\test_exclusion.py
echo   .venv\Scripts\python tests\e2e_test.py
echo Then start the app with:  run.bat --pick
