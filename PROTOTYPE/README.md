# AI-Based Pension Complaints Analysis and Resolution System (prototype)

Student: Gift Chitalu (202308731), ICT431 Capstone, Mulungushi University.
Supervisor: Mr Kaluba.

A working prototype that classifies pension complaints by **subject, urgency and sentiment**,
routes them to a department and officer, sets a service deadline, escalates overdue cases,
and reports on performance. All data is **simulated**. Do not enter real personal information.

## What you need to download
1. **Python 3.10 or newer** from https://www.python.org/downloads/
   (on Windows, tick "Add python.exe to PATH" during installation).
2. Nothing else by hand. The two Python packages (Flask, scikit-learn) are installed by pip.
3. Optional: **VS Code** from https://code.visualstudio.com/ to read and edit the code.

## Run it
**Windows:** double-click `run_windows.bat`.
**macOS / Linux:** `./run_mac_linux.sh`

Or by hand, in a terminal opened inside this folder:

    python -m venv venv
    venv\Scripts\activate          (macOS/Linux: source venv/bin/activate)
    pip install -r requirements.txt
    python app.py

Then open **http://127.0.0.1:5000** in your browser. The first start takes about 10 seconds:
it trains the model and fills the database with 70 demo complaints.
To reset everything, stop the app and delete `complaints.db`, `model.joblib` and `model_metrics.json`.

## Demo accounts (password: demo123)
| Username | Role |
|---|---|
| officer51, officer52 | Complaint officers in Claims |
| officer21, officer22 | Complaint officers in Compliance |
| officer11 ... officer82 | Officers of the 8 departments (officer<dept><1 or 2>) |
| supervisor | Supervisor (all cases, reports, AI model page) |
| admin | Administrator (also retrains the model) |

## Try this
1. Submit a complaint on the home page, for example: *My husband died 4 months ago and we have received no funeral grant.* Note the reference number.
2. Log in as `supervisor`, open **Dashboard**, find the case and see the AI labels, department and due date.
3. On the case page, correct a label and save. The correction is stored.
4. Log in as `admin`, open **AI model**, and press **Retrain model** to learn from the corrections.
5. Open **Reports** to see volumes, overdue cases and resolution times.

## Files
| File | Purpose |
|---|---|
| app.py | Web app, database, routing, SLA and escalation logic |
| nlp.py | Masks identifiers, trains and runs the classifiers |
| generate_dataset.py | Creates the simulated dataset (dataset.csv) |
| templates/, static/ | Web pages and styling |

## Limits
- Training data is simulated from templates, so accuracy is far higher than real complaints would give.
- SLA hours (48 / 120 / 240) are assumptions. Change `SLA_HOURS` in app.py.
- No real email or SMS alerts (notifications are stored in the database).
- Demo security only (shared password, development server). Do not expose it to the internet.
