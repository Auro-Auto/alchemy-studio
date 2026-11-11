@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\connect_studio.ps1"
if errorlevel 1 pause
