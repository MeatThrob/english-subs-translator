@echo off
rem Subtitle Translator - desktop control panel (On/Off window,
rem language picker, style controls)
cd /d "%~dp0.."
start "" .venv\Scripts\pythonw -m darija_subs.gui %*
