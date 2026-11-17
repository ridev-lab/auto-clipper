@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Auto Clipper
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo  Auto Clipper n'est pas encore installe : lance d'abord setup.bat
  echo.
  pause
  exit /b 1
)

rem Mise a jour de yt-dlp (une fois par jour au plus) : YouTube casse
rem regulierement les anciennes versions.
".venv\Scripts\python.exe" -m clipper.selfupdate

rem Demarre Ollama si besoin, lance le serveur et ouvre le navigateur
".venv\Scripts\python.exe" server.py
if errorlevel 1 pause
