"""AI-Based Pension Complaints Analysis and Resolution System (prototype).
Run:  python app.py      then open  http://127.0.0.1:5000
"""
import csv, hashlib, io, logging, os, random, sqlite3, threading
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from functools import wraps
from pathlib import Path
from flask import Flask, Response, flash, g, has_request_context, redirect, render_template, request, session, url_for, abort
from werkzeug.security import check_password_hash, generate_password_hash
import nlp

BASE = Path(__file__).parent
DB_PATH = BASE / 'complaints.db'
app = Flask(__name__)
app.config['TEMPLATES_AUTO_RELOAD'] = True      # edits to HTML templates show without restarting

# ---- Application log file (rotating): logs/app.log ----
LOG_DIR = BASE / 'logs'; LOG_DIR.mkdir(exist_ok=True)
_fh = RotatingFileHandler(LOG_DIR / 'app.log', maxBytes=1_000_000, backupCount=5, encoding='utf-8')
_fh.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(message)s'))
app.logger.addHandler(_fh); app.logger.setLevel(logging.INFO)
logging.getLogger('werkzeug').addHandler(_fh)
app.secret_key = os.environ.get('SECRET_KEY', 'dev-only-change-me')

CATEGORIES = {'registration': 'Registration and onboarding', 'missing_contributions': 'Missing or unremitted contributions', 'employer_arrears': 'Employer arrears and penalties',
              'statement_discrepancy': 'Statement discrepancy', 'portal_access': 'iCARE access and portal errors', 'delayed_claim': 'Delayed claim', 'rejected_claim': 'Rejected or queried claim',
              'payment_issue': 'Wrong or missing payment', 'life_certificate': 'Life certificate or suspension', 'survivor_funeral': 'Survivor benefit or funeral grant', 'service_conduct': 'Staff conduct and service quality'}
DEPARTMENTS = ['Registration and Customer Service', 'Compliance', 'Member Records', 'IT / e-Services', 'Claims', 'Benefit Payments', 'Pensions Administration', 'Integrity and Service Quality']
ROUTING = {'registration': DEPARTMENTS[0], 'missing_contributions': DEPARTMENTS[1], 'employer_arrears': DEPARTMENTS[1], 'statement_discrepancy': DEPARTMENTS[2], 'portal_access': DEPARTMENTS[3],
           'delayed_claim': DEPARTMENTS[4], 'rejected_claim': DEPARTMENTS[4], 'payment_issue': DEPARTMENTS[5], 'life_certificate': DEPARTMENTS[6], 'survivor_funeral': DEPARTMENTS[4], 'service_conduct': DEPARTMENTS[7]}
SLA_HOURS = {'high': 48, 'medium': 120, 'low': 240}          # assumption: configurable in the sla_rules table
STATUSES = ['Assigned', 'In Progress', 'Awaiting Info', 'Escalated', 'Resolved', 'Closed']
OPEN_STATES = ('Assigned', 'In Progress', 'Awaiting Info', 'Escalated')
URGENCY_ORDER = ['low', 'medium', 'high']
CONF_THRESHOLD = 0.55
FMT = '%Y-%m-%d %H:%M:%S'
now = lambda: datetime.now()

SCHEMA = """
CREATE TABLE institutions (institution_id INTEGER PRIMARY KEY, code TEXT, name TEXT);
CREATE TABLE departments (department_id INTEGER PRIMARY KEY, name TEXT);
CREATE TABLE routing_rules (category TEXT PRIMARY KEY, department_id INTEGER);
CREATE TABLE sla_rules (urgency TEXT PRIMARY KEY, hours_to_resolve INTEGER);
CREATE TABLE officers (officer_id INTEGER PRIMARY KEY, department_id INTEGER, username TEXT UNIQUE, full_name TEXT, role TEXT, password_hash TEXT);
CREATE TABLE complainants (complainant_id INTEGER PRIMARY KEY, institution_id INTEGER, role TEXT, full_name TEXT, contact TEXT);
CREATE TABLE complaints (complaint_id INTEGER PRIMARY KEY, reference_no TEXT UNIQUE, complainant_id INTEGER, department_id INTEGER, officer_id INTEGER,
  text TEXT, status TEXT, created_at TEXT, due_at TEXT, resolved_at TEXT, is_escalated INTEGER DEFAULT 0);
CREATE TABLE predictions (prediction_id INTEGER PRIMARY KEY, complaint_id INTEGER, category TEXT, urgency TEXT, sentiment TEXT, confidence REAL, needs_review INTEGER,
  model_version TEXT, corrected_category TEXT, corrected_urgency TEXT, corrected_sentiment TEXT);
CREATE TABLE case_updates (update_id INTEGER PRIMARY KEY, complaint_id INTEGER, officer_id INTEGER, note TEXT, new_status TEXT, created_at TEXT);
CREATE TABLE escalations (escalation_id INTEGER PRIMARY KEY, complaint_id INTEGER, reason TEXT, created_at TEXT);
CREATE TABLE notifications (notification_id INTEGER PRIMARY KEY, officer_id INTEGER, complaint_id INTEGER, message TEXT, created_at TEXT);
"""

def db():
    if 'db' not in g:
        g.db = sqlite3.connect(DB_PATH); g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(_):
    d = g.pop('db', None)
    if d: d.close()

def q(sql, args=(), one=False):
    cur = db().execute(sql, args); rows = cur.fetchall()
    return (rows[0] if rows else None) if one else rows

# ---------- audit trail: who did what, when (tamper-evident hash chain) ----------
AUDIT_SQL = """CREATE TABLE IF NOT EXISTS audit_log (audit_id INTEGER PRIMARY KEY, created_at TEXT, actor_id INTEGER, actor_name TEXT, actor_role TEXT,
  action TEXT, target_type TEXT, target_id TEXT, detail TEXT, ip TEXT, prev_hash TEXT, entry_hash TEXT);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);"""
GENESIS = '0' * 64
_audit_lock = threading.Lock()

def _row_hash(prev, created, aid, name, role, action, ttype, tid, detail, ip):
    parts = [prev, created, '' if aid is None else str(aid), name or '', role or '', action, ttype or '', str(tid or ''), detail or '', ip or '']
    return hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()

def current_actor():
    if has_request_context() and 'uid' in session:
        return session['uid'], session.get('username') or session.get('name', ''), session['role']
    return None, 'system', 'system'

def audit(con, action, target_type='', target_id='', detail='', actor=None):
    """Append one entry to the audit log. Each entry's hash includes the previous entry's hash, so editing or deleting a row is detectable."""
    aid, name, role = actor if actor else current_actor()
    ip = request.remote_addr if has_request_context() else ''
    created = datetime.now(timezone.utc).strftime(FMT); detail = (detail or '')[:500]; tid = str(target_id or '')
    with _audit_lock:
        last = con.execute('SELECT entry_hash FROM audit_log ORDER BY audit_id DESC LIMIT 1').fetchone()
        prev = last[0] if last else GENESIS
        h = _row_hash(prev, created, aid, name, role, action, target_type, tid, detail, ip)
        con.execute('INSERT INTO audit_log(created_at,actor_id,actor_name,actor_role,action,target_type,target_id,detail,ip,prev_hash,entry_hash) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (created, aid, name, role, action, target_type, tid, detail, ip, prev, h))
        con.commit()

def verify_audit(con):
    """Recompute the whole chain. Returns (ok, entries, first_bad_id, head_hash)."""
    prev, n = GENESIS, 0
    for r in con.execute('SELECT * FROM audit_log ORDER BY audit_id').fetchall():
        n += 1
        good = r['prev_hash'] == prev and r['entry_hash'] == _row_hash(prev, r['created_at'], r['actor_id'], r['actor_name'], r['actor_role'], r['action'], r['target_type'], r['target_id'], r['detail'], r['ip'])
        if not good: return False, n, r['audit_id'], prev
        prev = r['entry_hash']
    return True, n, None, prev

def ensure_schema():
    """Adds newer tables and accounts to an existing database without touching existing data."""
    con = sqlite3.connect(DB_PATH)
    con.executescript(AUDIT_SQL)
    existing = con.execute("SELECT 1 FROM officers WHERE username='member1'").fetchone()
    if not existing:
        con.execute(
            'INSERT INTO officers(department_id,username,full_name,role,password_hash) VALUES (NULL,?,?,?,?)',
            ('member1', 'Demo Member', 'member', generate_password_hash('demo123'))
        )
    con.commit(); con.close()

def init_db():
    con = sqlite3.connect(DB_PATH); con.executescript(SCHEMA)
    con.executemany('INSERT INTO institutions(code,name) VALUES (?,?)', [('NAPSA', 'National Pension Scheme Authority'), ('PSPF', 'Public Service Pensions Fund'), ('LASF', 'Local Authorities Superannuation Fund')])
    con.executemany('INSERT INTO departments(name) VALUES (?)', [(d,) for d in DEPARTMENTS])
    for cat, dep in ROUTING.items(): con.execute('INSERT INTO routing_rules VALUES (?,?)', (cat, DEPARTMENTS.index(dep) + 1))
    con.executemany('INSERT INTO sla_rules VALUES (?,?)', list(SLA_HOURS.items()))
    pw = generate_password_hash('demo123')
    for i, dep in enumerate(DEPARTMENTS, 1):
        for k in (1, 2): con.execute('INSERT INTO officers(department_id,username,full_name,role,password_hash) VALUES (?,?,?,?,?)', (i, f'officer{i}{k}', f'Officer {i}{k} ({dep})', 'officer', pw))
    con.execute('INSERT INTO officers(department_id,username,full_name,role,password_hash) VALUES (NULL,?,?,?,?)', ('supervisor', 'Demo Supervisor', 'supervisor', pw))
    con.execute('INSERT INTO officers(department_id,username,full_name,role,password_hash) VALUES (NULL,?,?,?,?)', ('admin', 'Demo Administrator', 'admin', pw))
    con.execute('INSERT INTO officers(department_id,username,full_name,role,password_hash) VALUES (NULL,?,?,?,?)', ('member1', 'Demo Member', 'member', pw))
    con.commit(); con.close()

# ---------- core logic: classify -> route -> assign -> SLA (Algorithm 3.1 in the proposal) ----------
def pick_officer(con, dept_id):
    return con.execute("""SELECT o.officer_id FROM officers o LEFT JOIN complaints c ON c.officer_id=o.officer_id AND c.status IN ('Assigned','In Progress','Awaiting Info','Escalated')
        WHERE o.department_id=? AND o.role='officer' GROUP BY o.officer_id ORDER BY COUNT(c.complaint_id), o.officer_id LIMIT 1""", (dept_id,)).fetchone()['officer_id']

def notify(con, officer_id, complaint_id, msg):
    con.execute('INSERT INTO notifications(officer_id,complaint_id,message,created_at) VALUES (?,?,?,?)', (officer_id, complaint_id, msg, now().strftime(FMT)))

def create_complaint(con, text, role, inst_code, name, contact, created=None):
    created = created or now()
    r = nlp.classify(text)
    urgency, notes = r['urgency'], list(r['rules'])
    if r['sentiment'] == 'negative' and r['sentiment_conf'] >= 0.7 and urgency != 'high':
        urgency = URGENCY_ORDER[URGENCY_ORDER.index(urgency) + 1]; notes.append(f'Urgency raised to {urgency}: strongly negative sentiment.')
    needs_review = int(r['category_conf'] < CONF_THRESHOLD)
    inst = con.execute('SELECT institution_id FROM institutions WHERE code=?', (inst_code,)).fetchone()['institution_id']
    cid = con.execute('INSERT INTO complainants(institution_id,role,full_name,contact) VALUES (?,?,?,?)', (inst, role, name or 'Anonymous', contact or '')).lastrowid
    dept = con.execute('SELECT department_id FROM routing_rules WHERE category=?', (r['category'],)).fetchone()['department_id']
    officer = pick_officer(con, dept)
    due = created + timedelta(hours=SLA_HOURS[urgency])
    pk = con.execute('INSERT INTO complaints(complainant_id,department_id,officer_id,text,status,created_at,due_at) VALUES (?,?,?,?,?,?,?)',
                     (cid, dept, officer, r['masked_text'], 'Assigned', created.strftime(FMT), due.strftime(FMT))).lastrowid
    ref = f'PC-{created.year}-{pk:05d}'
    con.execute('UPDATE complaints SET reference_no=? WHERE complaint_id=?', (ref, pk))
    con.execute('INSERT INTO predictions(complaint_id,category,urgency,sentiment,confidence,needs_review,model_version) VALUES (?,?,?,?,?,?,?)',
                (pk, r['category'], urgency, r['sentiment'], r['category_conf'], needs_review, r['model_version']))
    con.execute('INSERT INTO case_updates(complaint_id,officer_id,note,new_status,created_at) VALUES (?,?,?,?,?)',
                (pk, None, 'Complaint received, classified by the AI engine and assigned. ' + ' '.join(notes), 'Assigned', created.strftime(FMT)))
    notify(con, officer, pk, f'New {urgency}-urgency case {ref}: {CATEGORIES[r["category"]]}')
    return pk, ref

def run_escalations(con):
    """Escalate every open case whose due date has passed and alert the supervisors."""
    n = 0
    for c in con.execute("SELECT complaint_id, reference_no FROM complaints WHERE is_escalated=0 AND status IN ('Assigned','In Progress','Awaiting Info') AND due_at < ?", (now().strftime(FMT),)).fetchall():
        con.execute("UPDATE complaints SET is_escalated=1, status='Escalated' WHERE complaint_id=?", (c['complaint_id'],))
        con.execute('INSERT INTO escalations(complaint_id,reason,created_at) VALUES (?,?,?)', (c['complaint_id'], 'Due date passed without resolution', now().strftime(FMT)))
        con.execute('INSERT INTO case_updates(complaint_id,officer_id,note,new_status,created_at) VALUES (?,?,?,?,?)', (c['complaint_id'], None, 'Escalated automatically: due date passed.', 'Escalated', now().strftime(FMT)))
        for s in con.execute("SELECT officer_id FROM officers WHERE role='supervisor'").fetchall(): notify(con, s['officer_id'], c['complaint_id'], f'Escalation: case {c["reference_no"]} is overdue')
        audit(con, 'case_escalated', 'complaint', c['complaint_id'], f'{c["reference_no"]}: due date passed without resolution', actor=(None, 'system', 'system'))
        n += 1
    con.commit(); return n

def seed_demo(con, n=70):
    rows = list(csv.DictReader(open(nlp.DATA_PATH, encoding='utf-8'))); random.seed(7); random.shuffle(rows)
    for r in rows[:n]:
        created = now() - timedelta(hours=random.randint(2, 24 * 12))
        pk, _ = create_complaint(con, r['text'], r['role'], r['institution'], f'Demo user {random.randint(1, 999)}', '', created)
        c = con.execute('SELECT due_at, officer_id FROM complaints WHERE complaint_id=?', (pk,)).fetchone(); due = datetime.strptime(c['due_at'], FMT)
        roll = random.random()
        if roll < 0.40:
            res = min(now(), created + timedelta(hours=random.randint(4, 24 * 9)))
            con.execute("UPDATE complaints SET status='Resolved', resolved_at=? WHERE complaint_id=?", (res.strftime(FMT), pk))
            con.execute('INSERT INTO case_updates(complaint_id,officer_id,note,new_status,created_at) VALUES (?,?,?,?,?)', (pk, c['officer_id'], 'Resolved (demo data).', 'Resolved', res.strftime(FMT)))
        elif roll < 0.60:
            con.execute("UPDATE complaints SET status='In Progress' WHERE complaint_id=?", (pk,))
    con.commit(); run_escalations(con)

def bootstrap():
    nlp.ensure_model()
    fresh = not DB_PATH.exists()
    if fresh: init_db()
    ensure_schema()
    con = sqlite3.connect(DB_PATH); con.row_factory = sqlite3.Row
    if fresh:
        seed_demo(con); audit(con, 'demo_seed', 'system', '', 'Loaded simulated demo complaints', actor=(None, 'system', 'system'))
    audit(con, 'app_start', 'system', '', 'Application started; model ' + nlp.load()['meta']['version'], actor=(None, 'system', 'system'))
    con.close(); app.logger.info('Application started')

# ---------- auth ----------
def staff(*roles):
    def deco(f):
        @wraps(f)
        def w(*a, **k):
            if 'uid' not in session: return redirect(url_for('login', next=request.path))
            if session['role'] not in ('officer', 'supervisor', 'admin'):
                flash('This area is only available to authorised staff.', 'error')
                return redirect(url_for('submit'))
            if roles and session['role'] not in roles:
                audit(db(), 'access_denied', 'page', request.path, f'Role {session["role"]} tried to open a page restricted to {"/".join(roles)}'); abort(403)
            return f(*a, **k)
        return w
    return deco

@app.context_processor
def inject():
    unread = 0
    if 'uid' in session: unread = q('SELECT COUNT(*) n FROM notifications WHERE officer_id=?', (session['uid'],), one=True)['n']
    return dict(session=session, CATEGORIES=CATEGORIES, STATUSES=STATUSES, notif_count=unread, now=now())

@app.after_request
def log_request(resp):
    if not request.path.startswith('/static'):
        app.logger.info('%s user=%s %s %s -> %s', request.remote_addr, session.get('username', '-'), request.method, request.path, resp.status_code)
    return resp

@app.template_filter('dt')
def dt(v): return v[:16] if v else '-'

# ---------- public pages ----------
@app.route('/', methods=['GET', 'POST'])
def submit():
    if request.method == 'POST':
        if 'uid' not in session:
            flash('Please log in before submitting a complaint.', 'error')
            return redirect(url_for('member_login', next=request.full_path.rstrip('?')))
        text = request.form.get('text', '').strip()
        if len(text) < 15:
            flash('Please describe the problem in a little more detail (at least 15 characters).', 'error'); return render_template('submit.html', form=request.form)
        con = db(); pk, ref = create_complaint(con, text, request.form.get('role', 'employee'), request.form.get('institution', 'NAPSA'), request.form.get('name'), request.form.get('contact')); con.commit()
        p = con.execute('SELECT category,urgency,sentiment,confidence,model_version FROM predictions WHERE complaint_id=?', (pk,)).fetchone()
        audit(con, 'complaint_created', 'complaint', pk, f'{ref} via web form ({request.form.get("role", "employee")}, {request.form.get("institution", "NAPSA")}); AI: {p["category"]}/{p["urgency"]}/{p["sentiment"]} (confidence {p["confidence"]}); model {p["model_version"]}', actor=(None, 'public', 'public'))
        return redirect(url_for('submitted', ref=ref))
    return render_template('submit.html', form={})

@app.route('/submitted/<ref>')
def submitted(ref):
    return render_template('submitted.html', ref=ref)

@app.route('/track', methods=['GET', 'POST'])
def track():
    ref = (request.form.get('ref') or request.args.get('ref') or '').strip().upper()
    c = q('SELECT * FROM complaints WHERE reference_no=?', (ref,), one=True) if ref else None
    if ref and not c: flash('No complaint found with that reference number.', 'error')
    if ref: audit(db(), 'track_lookup' if c else 'track_not_found', 'complaint', c['complaint_id'] if c else '', f'Reference {ref[:30]}', actor=(None, 'public', 'public'))
    hist = q("SELECT new_status, created_at FROM case_updates WHERE complaint_id=? ORDER BY update_id", (c['complaint_id'],)) if c else []
    return render_template('track.html', c=c, hist=hist, ref=ref)

# ---------- staff pages ----------
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        u = q('SELECT * FROM officers WHERE username=?', (request.form.get('username', '').strip(),), one=True)
        if u and check_password_hash(u['password_hash'], request.form.get('password', '')):
            session.update(uid=u['officer_id'], name=u['full_name'], role=u['role'], dept=u['department_id'], username=u['username'])
            audit(db(), 'login', 'officer', u['officer_id'], 'Signed in', actor=(u['officer_id'], u['username'], u['role']))
            if u['role'] == 'member':
                return redirect(url_for('submit'))
            return redirect(request.args.get('next') or url_for('dashboard'))
        tried = request.form.get('username', '').strip()[:40]
        audit(db(), 'login_failed', 'officer', '', f'Failed sign-in for username "{tried}"', actor=(None, tried or '(blank)', 'unknown'))
        app.logger.warning('Failed login for "%s" from %s', tried, request.remote_addr)
        flash('Wrong username or password.', 'error')
    return render_template('login.html')

@app.route('/member/login', methods=['GET', 'POST'])
def member_login():
    if request.method == 'POST':
        u = q('SELECT * FROM officers WHERE username=? AND role=?',
              (request.form.get('username', '').strip(), 'member'), one=True)
        if u and check_password_hash(u['password_hash'], request.form.get('password', '')):
            session.update(uid=u['officer_id'], name=u['full_name'], role=u['role'], dept=u['department_id'], username=u['username'])
            audit(db(), 'member_login', 'member', u['officer_id'], 'Member signed in', actor=(u['officer_id'], u['username'], u['role']))
            return redirect(url_for('submit'))
        tried = request.form.get('username', '').strip()[:40]
        audit(db(), 'login_failed', 'member', '', f'Failed member sign-in for username "{tried}"', actor=(None, tried or '(blank)', 'unknown'))
        flash('Wrong member username or password.', 'error')
    return render_template('member_login.html')

@app.route('/logout')
def logout():
    if 'uid' in session: audit(db(), 'logout', 'officer', session['uid'], 'Signed out')
    session.clear(); return redirect(url_for('submit'))

@app.route('/dashboard')
@staff()
def dashboard():
    con = db(); run_escalations(con)
    f = {k: request.args.get(k, '') for k in ('status', 'urgency', 'scope')}
    where, args = [], []
    scope = f['scope'] or ('mine' if session['role'] == 'officer' else 'all')
    if scope == 'mine' and session['role'] == 'officer': where.append('c.officer_id=?'); args.append(session['uid'])
    if f['status']: where.append('c.status=?'); args.append(f['status'])
    if f['urgency']: where.append('COALESCE(p.corrected_urgency,p.urgency)=?'); args.append(f['urgency'])
    rows = q(f"""SELECT c.*, COALESCE(p.corrected_category,p.category) cat, COALESCE(p.corrected_urgency,p.urgency) urg, COALESCE(p.corrected_sentiment,p.sentiment) sent, p.needs_review,
        d.name dept, o.full_name officer, cm.role crole, i.code inst FROM complaints c JOIN predictions p ON p.complaint_id=c.complaint_id JOIN departments d ON d.department_id=c.department_id
        LEFT JOIN officers o ON o.officer_id=c.officer_id JOIN complainants cm ON cm.complainant_id=c.complainant_id JOIN institutions i ON i.institution_id=cm.institution_id
        {'WHERE ' + ' AND '.join(where) if where else ''} ORDER BY CASE c.status WHEN 'Escalated' THEN 0 WHEN 'Resolved' THEN 2 WHEN 'Closed' THEN 3 ELSE 1 END,
        CASE COALESCE(p.corrected_urgency,p.urgency) WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END, c.due_at LIMIT 200""", args)
    notes = q('SELECT n.message, n.created_at FROM notifications n WHERE officer_id=? ORDER BY notification_id DESC LIMIT 5', (session['uid'],))
    return render_template('dashboard.html', rows=rows, f=f, scope=scope, notes=notes)

@app.route('/case/<int:cid>', methods=['GET', 'POST'])
@staff()
def case(cid):
    con = db()
    c = q("""SELECT c.*, p.category, p.urgency, p.sentiment, p.confidence, p.needs_review, p.model_version, p.corrected_category, p.corrected_urgency, p.corrected_sentiment,
        d.name dept, o.full_name officer, cm.role crole, cm.full_name cname, i.code inst FROM complaints c JOIN predictions p ON p.complaint_id=c.complaint_id JOIN departments d ON d.department_id=c.department_id
        LEFT JOIN officers o ON o.officer_id=c.officer_id JOIN complainants cm ON cm.complainant_id=c.complainant_id JOIN institutions i ON i.institution_id=cm.institution_id WHERE c.complaint_id=?""", (cid,), one=True)
    if not c: abort(404)
    if request.method == 'GET':   # record who opened the case (not more than once per 5 minutes per person)
        who = current_actor()[1]; since = (datetime.now(timezone.utc) - timedelta(minutes=5)).strftime(FMT)
        if not con.execute("SELECT 1 FROM audit_log WHERE action='case_view' AND target_id=? AND actor_name=? AND created_at>?", (str(cid), who, since)).fetchone():
            audit(con, 'case_view', 'complaint', cid, f'Opened case {c["reference_no"]}')
    if request.method == 'POST':
        f = request.form; status = f.get('status', c['status']); note = f.get('note', '').strip()
        cat, urg, sen = f.get('category', c['category']), f.get('urgency', c['urgency']), f.get('sentiment', c['sentiment'])
        cur = (c['corrected_category'] or c['category'], c['corrected_urgency'] or c['urgency'], c['corrected_sentiment'] or c['sentiment'])
        changes = []; had_note = bool(note)
        if (cat, urg, sen) != cur:   # officer corrected the AI: keep it for retraining
            con.execute('UPDATE predictions SET corrected_category=?, corrected_urgency=?, corrected_sentiment=? WHERE complaint_id=?', (cat, urg, sen, cid))
            note = (note + ' ' if note else '') + f'[Labels corrected to {cat}/{urg}/{sen}]'
            changes.append(f'labels {cur[0]}/{cur[1]}/{cur[2]} -> {cat}/{urg}/{sen}')
            if urg != cur[1]:
                due = datetime.strptime(c['created_at'], FMT) + timedelta(hours=SLA_HOURS[urg]); con.execute('UPDATE complaints SET due_at=? WHERE complaint_id=?', (due.strftime(FMT), cid)); changes.append(f'due date {c["due_at"][:16]} -> {due.strftime(FMT)[:16]}')
            dept = con.execute('SELECT department_id FROM routing_rules WHERE category=?', (cat,)).fetchone()['department_id']
            if dept != c['department_id']:
                con.execute('UPDATE complaints SET department_id=?, officer_id=? WHERE complaint_id=?', (dept, pick_officer(con, dept), cid)); note += ' [Re-routed to ' + DEPARTMENTS[dept - 1] + ']'; changes.append(f'department {c["dept"]} -> {DEPARTMENTS[dept - 1]}')
        if session['role'] in ('supervisor', 'admin') and f.get('officer_id') and int(f['officer_id']) != c['officer_id']:
            new_o = con.execute('SELECT full_name FROM officers WHERE officer_id=?', (int(f['officer_id']),)).fetchone()
            con.execute('UPDATE complaints SET officer_id=? WHERE complaint_id=?', (int(f['officer_id']), cid))
            note = (note + ' ' if note else '') + f'[Reassigned from {c["officer"]} to {new_o["full_name"]} by {session["name"]}]'
            changes.append(f'officer {c["officer"]} -> {new_o["full_name"]}')
            audit(con, 'case_reassign', 'complaint', cid, f'{c["reference_no"]}: {c["officer"]} -> {new_o["full_name"]}')
        resolved = now().strftime(FMT) if status in ('Resolved', 'Closed') and not c['resolved_at'] else c['resolved_at']
        con.execute('UPDATE complaints SET status=?, resolved_at=? WHERE complaint_id=?', (status, resolved, cid))
        con.execute('INSERT INTO case_updates(complaint_id,officer_id,note,new_status,created_at) VALUES (?,?,?,?,?)', (cid, session['uid'], note or 'Status updated.', status, now().strftime(FMT)))
        if status != c['status']: changes.append(f'status {c["status"]} -> {status}')
        if had_note: changes.append('note added')
        audit(con, 'case_update', 'complaint', cid, f'{c["reference_no"]}: ' + ('; '.join(changes) or 'saved with no change'))
        con.commit(); flash('Case updated.', 'ok'); return redirect(url_for('case', cid=cid))
    hist = q('SELECT u.*, o.full_name FROM case_updates u LEFT JOIN officers o ON o.officer_id=u.officer_id WHERE complaint_id=? ORDER BY update_id', (cid,))
    officers = q("SELECT officer_id, full_name FROM officers WHERE role='officer' ORDER BY department_id, officer_id")
    return render_template('case.html', c=c, hist=hist, officers=officers, CAT=CATEGORIES)

@app.route('/reports')
@staff('supervisor', 'admin')
def reports():
    con = db(); run_escalations(con); n = now().strftime(FMT)
    k = dict(total=q('SELECT COUNT(*) n FROM complaints', one=True)['n'],
             open=q("SELECT COUNT(*) n FROM complaints WHERE status IN ('Assigned','In Progress','Awaiting Info','Escalated')", one=True)['n'],
             overdue=q("SELECT COUNT(*) n FROM complaints WHERE status IN ('Assigned','In Progress','Awaiting Info','Escalated') AND due_at<?", (n,), one=True)['n'],
             escalated=q('SELECT COUNT(*) n FROM escalations', one=True)['n'],
             corrections=q('SELECT COUNT(*) n FROM predictions WHERE corrected_category IS NOT NULL', one=True)['n'],
             review=q('SELECT COUNT(*) n FROM predictions WHERE needs_review=1', one=True)['n'])
    avg = q("SELECT AVG((julianday(resolved_at)-julianday(created_at))*24) h FROM complaints WHERE resolved_at IS NOT NULL", one=True)['h']
    within = q("SELECT AVG(CASE WHEN resolved_at<=due_at THEN 1.0 ELSE 0 END) r FROM complaints WHERE resolved_at IS NOT NULL", one=True)['r']
    k['avg_hours'] = round(avg, 1) if avg is not None else None; k['within_sla'] = round(within * 100) if within is not None else None
    def grp(expr, join=''): return q(f'SELECT {expr} label, COUNT(*) n FROM complaints c JOIN predictions p ON p.complaint_id=c.complaint_id {join} GROUP BY label ORDER BY n DESC')
    charts = {'By subject': [(CATEGORIES[r['label']], r['n']) for r in grp('COALESCE(p.corrected_category,p.category)')],
              'By urgency': [(r['label'], r['n']) for r in grp('COALESCE(p.corrected_urgency,p.urgency)')],
              'By sentiment': [(r['label'], r['n']) for r in grp('COALESCE(p.corrected_sentiment,p.sentiment)')],
              'By department': [(r['label'], r['n']) for r in grp('d.name', 'JOIN departments d ON d.department_id=c.department_id')],
              'By institution': [(r['label'], r['n']) for r in grp('i.code', 'JOIN complainants cm ON cm.complainant_id=c.complainant_id JOIN institutions i ON i.institution_id=cm.institution_id')],
              'By complainant role': [(r['label'], r['n']) for r in grp('cm.role', 'JOIN complainants cm ON cm.complainant_id=c.complainant_id')]}
    return render_template('reports.html', k=k, charts=charts)

@app.route('/model')
@staff('supervisor', 'admin')
def model():
    import json
    return render_template('model.html', m=json.loads(nlp.METRICS_PATH.read_text()), CAT=CATEGORIES)

@app.route('/admin/escalate', methods=['POST'])
@staff('supervisor', 'admin')
def admin_escalate():
    n = run_escalations(db()); audit(db(), 'escalation_check', 'system', '', f'Manual escalation check run; {n} case(s) escalated')
    flash(f'Escalation check complete: {n} case(s) escalated.', 'ok'); return redirect(request.referrer or url_for('dashboard'))

@app.route('/admin/retrain', methods=['POST'])
@staff('admin')
def admin_retrain():
    extra = [(r['text'], r['corrected_category'], r['corrected_urgency'] or r['urgency'], r['corrected_sentiment'] or r['sentiment'])
             for r in q('SELECT c.text, p.* FROM predictions p JOIN complaints c ON c.complaint_id=p.complaint_id WHERE p.corrected_category IS NOT NULL')]
    old = nlp.load()['meta']['version']; meta, mt = nlp.train(extra)
    audit(db(), 'model_retrain', 'model', meta['version'], f'{old} -> {meta["version"]}; rows {meta["n_rows"]}; officer corrections {meta["n_corrections"]}; test accuracy subject {mt["category"]["accuracy"]}, urgency {mt["urgency"]["accuracy"]}, sentiment {mt["sentiment"]["accuracy"]}; previous model archived')
    flash(f'Model retrained with {meta["n_corrections"]} officer correction(s). Version {meta["version"]}.', 'ok'); return redirect(url_for('model'))

@app.route('/audit')
@staff('admin')
def audit_page():
    con = db(); f = {k: request.args.get(k, '').strip() for k in ('action', 'actor')}
    page = max(1, int(request.args.get('page', 1) or 1)); per = 50; where, args = [], []
    if f['action']: where.append('action=?'); args.append(f['action'])
    if f['actor']: where.append('actor_name LIKE ?'); args.append('%' + f['actor'] + '%')
    w = ('WHERE ' + ' AND '.join(where)) if where else ''
    total = con.execute(f'SELECT COUNT(*) FROM audit_log {w}', args).fetchone()[0]
    rows = con.execute(f'SELECT * FROM audit_log {w} ORDER BY audit_id DESC LIMIT ? OFFSET ?', args + [per, (page - 1) * per]).fetchall()
    actions = [r[0] for r in con.execute('SELECT DISTINCT action FROM audit_log ORDER BY action').fetchall()]
    ok, n, bad, head = verify_audit(con)
    audit(con, 'audit_view', 'audit_log', '', f'Viewed the audit log (page {page})')
    return render_template('audit.html', rows=rows, f=f, actions=actions, total=total, page=page, pages=max(1, -(-total // per)), chain=dict(ok=ok, n=n, bad=bad, head=head))

@app.route('/audit/export.csv')
@staff('admin')
def audit_export():
    con = db(); buf = io.StringIO(); w = csv.writer(buf)
    w.writerow(['audit_id', 'created_at_utc', 'actor_id', 'actor_name', 'actor_role', 'action', 'target_type', 'target_id', 'detail', 'ip', 'prev_hash', 'entry_hash'])
    for r in con.execute('SELECT * FROM audit_log ORDER BY audit_id'): w.writerow(list(r))
    audit(con, 'audit_export', 'audit_log', '', 'Exported the audit log as CSV')
    return Response(buf.getvalue(), mimetype='text/csv', headers={'Content-Disposition': 'attachment; filename=audit_log.csv'})

bootstrap()
if __name__ == '__main__':
    app.run(debug=False, port=5000)
