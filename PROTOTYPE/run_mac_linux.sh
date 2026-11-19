#!/bin/bash
# First run: creates a virtual environment, installs packages, then starts the app.
[ -d venv ] || python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python app.py
