@echo off
REM First run: creates a virtual environment, installs packages, then starts the app.
if not exist venv (python -m venv venv)
call venv\Scripts\activate
pip install -r requirements.txt
python app.py
pause
