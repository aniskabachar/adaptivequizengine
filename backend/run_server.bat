@echo off
REM Local development server (auto-reload). Run from the backend folder.
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
