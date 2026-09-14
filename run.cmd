@echo off
REM Nhay dup file nay de dung va chay voice agent.
REM Cua so se mo o thu muc repo va giu lai de doc log.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-and-run.ps1" %*
if errorlevel 1 pause
