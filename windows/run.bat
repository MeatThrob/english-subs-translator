@echo off
cd /d "%~dp0"
.venv\Scripts\python -m darija_subs.main %*
