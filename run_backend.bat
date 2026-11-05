@echo off
title Mule Shield Analytical Backend
echo ====================================================
echo     Starting Mule Shield Backend on Port 8000
echo ====================================================
cd /d "%~dp0"
python -u backend/main.py
pause

