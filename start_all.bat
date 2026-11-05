@echo off
title Mule Shield System Launcher
echo ====================================================
echo    Starting Mule Shield Full Stack (Port 3000 ^& 8000)
echo ====================================================
cd /d "%~dp0"
node scripts/start.js
pause

