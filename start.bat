@echo off
title ComplianceAI Launcher

echo =======================================
echo Starting ComplianceAI...
echo =======================================

echo.
echo [1/2] Starting Python Backend Server...
start "ComplianceAI Backend" cmd /k "cd backend && python -m uvicorn main:app --host 127.0.0.1 --port 8000"

echo [2/2] Starting Electron Frontend...
start "ComplianceAI Frontend" cmd /k "cd frontend && npm run electron"

echo.
echo Both services have been launched in separate windows!
echo You can safely close this launcher window.
