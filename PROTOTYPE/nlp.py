"""NLP engine: masks personal identifiers, then classifies a complaint by
category (subject), urgency and sentiment using TF-IDF + logistic regression."""
import csv, json, re
from datetime import datetime
from pathlib import Path
import joblib
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

BASE = Path(__file__).parent
MODEL_PATH, METRICS_PATH, DATA_PATH = BASE / 'model.joblib', BASE / 'model_metrics.json', BASE / 'dataset.csv'
TARGETS = ['category', 'urgency', 'sentiment']
NRC = re.compile(r'\b\d{6}/\d{2}/\d\b')
PHONE = re.compile(r'(?:\+?260|\b0)\d{9}\b')
LONGNUM = re.compile(r'\b\d{7,}\b')
HIGH_WORDS = ['no income', 'stopped', 'funeral', 'died', 'passed away', 'widow', 'bereaved', 'sick', 'cannot afford', 'bedridden', 'asked me for money', 'must pay']
_cache = {}

def mask(text):
    """Privacy step: replace NRC numbers, phone numbers and long numbers."""
    text = NRC.sub('[NRC]', text); text = PHONE.sub('[PHONE]', text); return LONGNUM.sub('[NUMBER]', text)

def clean(text):
    return re.sub(r'\s+', ' ', re.sub(r"[^a-z0-9\[\]' ]", ' ', mask(text).lower())).strip()

def _pipe():
    return Pipeline([('tfidf', TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
                     ('clf', LogisticRegression(max_iter=3000, C=8, class_weight='balanced'))])

def train(extra=None):
    """Train on dataset.csv (+ officer corrections). Evaluates on a held-out 20% test set, then refits on all data."""
    rows = list(csv.DictReader(open(DATA_PATH, encoding='utf-8')))
    for t, c, u, s in (extra or []): rows.append({'text': t, 'category': c, 'urgency': u, 'sentiment': s})
    X = [clean(r['text']) for r in rows]
    models, metrics = {}, {}
    for tgt in TARGETS:
        y = [r[tgt] for r in rows]
        Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.2, random_state=42, stratify=y)
        p = _pipe().fit(Xtr, ytr); pred = p.predict(Xte)
        rep = classification_report(yte, pred, output_dict=True, zero_division=0)
        metrics[tgt] = {'accuracy': round(accuracy_score(yte, pred), 4), 'macro_precision': round(rep['macro avg']['precision'], 4),
                        'macro_recall': round(rep['macro avg']['recall'], 4), 'macro_f1': round(rep['macro avg']['f1-score'], 4),
                        'per_class': {k: round(v['f1-score'], 3) for k, v in rep.items() if isinstance(v, dict) and k not in ('macro avg', 'weighted avg')}}
        if tgt == 'category':
            labels = sorted(set(y)); metrics[tgt]['labels'] = labels
            metrics[tgt]['confusion'] = confusion_matrix(yte, pred, labels=labels).tolist()
        models[tgt] = _pipe().fit(X, y)
    meta = {'trained_at': datetime.now().strftime('%Y-%m-%d %H:%M'), 'n_rows': len(rows), 'n_corrections': len(extra or []), 'version': 'lr-' + datetime.now().strftime('%Y%m%d%H%M')}
    joblib.dump({'models': models, 'meta': meta}, MODEL_PATH)
    METRICS_PATH.write_text(json.dumps({'meta': meta, 'metrics': metrics}, indent=1))
    _cache.clear()
    return meta, metrics

def load():
    if 'm' not in _cache: _cache['m'] = joblib.load(MODEL_PATH)
    return _cache['m']

def ensure_model():
    if not DATA_PATH.exists():
        import generate_dataset
        with open(DATA_PATH, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f); w.writerow(['text', 'category', 'urgency', 'sentiment', 'role', 'institution']); w.writerows(generate_dataset.build())
    if not MODEL_PATH.exists(): train()

def classify(text):
    """Returns the masked text and, for each target, (label, confidence)."""
    m = load()['models']; x = [clean(text)]; out = {'masked_text': mask(text), 'model_version': load()['meta']['version'], 'rules': []}
    for tgt in TARGETS:
        proba = m[tgt].predict_proba(x)[0]; i = proba.argmax()
        out[tgt] = str(m[tgt].classes_[i]); out[tgt + '_conf'] = round(float(proba[i]), 3)
    low = text.lower()
    if out['urgency'] != 'high' and any(w in low for w in HIGH_WORDS):   # safety rule: vulnerable situations are never low priority
        out['rules'].append('Urgency raised to high: text mentions a vulnerable situation (no income, stopped pension, bereavement or illness).')
        out['urgency'] = 'high'
    return out
