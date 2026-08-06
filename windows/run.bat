@echo off
rem darija-subs launcher. First run: run.bat --pick
cd /d "%~dp0.."
.venv\Scripts\python -m darija_subs.main %*
