@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Auto Clipper - Installation
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8

echo.
echo  ==============================================
echo    Auto Clipper - installation
echo  ==============================================
echo.

rem --- Python 3.12 -----------------------------------------------------------
set "PY="
where py >nul 2>&1 && py -3.12 -c "import sys" >nul 2>&1 && set "PY=py -3.12"
if not defined PY (
  where python >nul 2>&1 && python -c "import sys; sys.exit(0 if sys.version_info[:2]==(3,12) else 1)" >nul 2>&1 && set "PY=python"
)
if not defined PY (
  echo  [ERREUR] Python 3.12 est introuvable.
  echo           Installe-le avec :  winget install -e --id Python.Python.3.12
  echo           puis ferme cette fenetre et relance setup.bat
  echo.
  pause
  exit /b 1
)
echo  [OK]     Python 3.12 : %PY%

rem --- environnement virtuel ----------------------------------------------------
if not exist ".venv\Scripts\python.exe" (
  echo           Creation de l'environnement virtuel .venv ...
  %PY% -m venv .venv
  if errorlevel 1 (
    echo  [ERREUR] Impossible de creer l'environnement virtuel.
    pause
    exit /b 1
  )
)

rem --- installation complete (ffmpeg, Ollama, dependances, modeles) -----------
".venv\Scripts\python.exe" -m clipper.installer
set ERR=%ERRORLEVEL%
echo.
if %ERR% neq 0 (
  echo  Installation incomplete : corrige les points signales ci-dessus puis relance setup.bat
) else (
  echo  Installation terminee ! Double-clique sur start.bat pour lancer Auto Clipper.
)
echo.
pause
exit /b %ERR%
