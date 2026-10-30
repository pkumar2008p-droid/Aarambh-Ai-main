"""Self-learning: every closed follow-up becomes a labelled sample (training_samples). Retraining builds a
challenger model, compares it with the current one on a hold-out set and promotes it ONLY if it is not worse.
Run from the Admin dashboard (Retrain AI button) or:  python train.py"""
import os, io, hashlib, threading
import numpy as np, pandas as pd, joblib
from sklearn.base import clone
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import f1_score, roc_auc_score
from sklearn.model_selection import train_test_split, cross_val_predict, StratifiedKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from xgboost import XGBClassifier
from .core import db, now_utc, iso, paged, log
from . import risk

FEATURES, TARGET, SEED = risk.FEATURES, "target_delayed_followup", 42
STATE = {"running": False, "last": None}
ROOT = os.path.dirname(__file__)


def default_pipe(df):
    num = [c for c in FEATURES if pd.api.types.is_numeric_dtype(df[c])]
    cat = [c for c in FEATURES if c not in num]
    prep = ColumnTransformer([
        ("n", Pipeline([("i", SimpleImputer(strategy="median")), ("s", StandardScaler())]), num),
        ("c", Pipeline([("i", SimpleImputer(strategy="most_frequent")), ("o", OneHotEncoder(handle_unknown="ignore"))]), cat)])
    return Pipeline([("prep", prep), ("model", XGBClassifier(n_estimators=250, max_depth=3, learning_rate=0.05, subsample=0.85,
                     colsample_bytree=0.85, eval_metric="logloss", random_state=SEED, n_jobs=1))])


def collect():
    frames = []
    base = os.path.join(ROOT, "data", "base_training.csv")          # your original 6000-row CSV (optional but recommended)
    if os.path.exists(base):
        frames.append(pd.read_csv(base)[FEATURES + [TARGET]])
    rows = paged(lambda: db.table("training_samples").select("features,label"))
    real = len(rows)
    if rows:
        frames.append(pd.DataFrame([{**r["features"], TARGET: r["label"]} for r in rows])[FEATURES + [TARGET]])
    return (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()), real


def run():
    risk.MODEL.load()
    df, real = collect()
    if len(df) < 200 or df[TARGET].nunique() < 2 or int(df[TARGET].value_counts().min()) < 4:
        return {"ok": False, "message": f"Not enough balanced data yet ({len(df)} rows, {real} real outcomes). Need at least 4 examples in each outcome class."}
    X, y = df[FEATURES], df[TARGET].astype(int)
    Xtr, Xte, ytr, yte = train_test_split(X, y, test_size=0.25, stratify=y, random_state=SEED)
    champ = risk.MODEL.b
    est = clone(champ["model"]) if champ else default_pipe(df)      # keeps previously tuned hyper-parameters
    oof = cross_val_predict(est, Xtr, ytr, cv=StratifiedKFold(3, shuffle=True, random_state=SEED), method="predict_proba")[:, 1]
    grid = np.linspace(0.1, 0.9, 81)
    th = float(grid[int(np.argmax([f1_score(ytr, (oof >= t).astype(int), zero_division=0) for t in grid]))])
    est.fit(Xtr, ytr)
    p = est.predict_proba(Xte)[:, 1]
    f1_new = float(f1_score(yte, (p >= th).astype(int), zero_division=0))
    auc = float(roc_auc_score(yte, p))
    f1_old = None
    if champ:
        try:
            po = champ["model"].predict_proba(Xte[champ["feature_columns"]])[:, 1]
            f1_old = float(f1_score(yte, (po >= champ["threshold"]).astype(int), zero_division=0))
        except Exception:
            log.exception("champion evaluation failed")
    if not (f1_new >= 0.75 and (f1_old is None or f1_new >= f1_old - 0.02)):
        return {"ok": True, "promoted": False, "message": f"Kept current model (new F1 {f1_new:.3f} vs old {f1_old}). {real} real outcomes used."}
    est.fit(X, y)                                                   # final model learns from ALL data
    bundle = {"model": est, "threshold": th, "model_name": "Self-trained XGBoost", "feature_columns": FEATURES,
              "target_column": TARGET, "trained_at": iso(now_utc())}
    buf = io.BytesIO(); joblib.dump(bundle, buf); raw = buf.getvalue()
    db.table("ml_models").update({"active": False}).eq("active", True).execute()
    db.table("ml_models").insert({"name": bundle["model_name"], "model_data": raw, "sha256": hashlib.sha256(raw).hexdigest(),
                                  "threshold": th, "n_samples": len(df), "active": True,
                                  "metrics": {"f1": f1_new, "roc_auc": auc, "real_outcomes": real}}).execute()
    risk.MODEL.load()
    return {"ok": True, "promoted": True, "message": f"New model live. F1 {f1_new:.3f}, AUC {auc:.3f}, {len(df)} rows ({real} real outcomes)."}


def _job():
    try:
        STATE["last"] = run()
    except Exception as e:
        log.exception("training failed")
        STATE["last"] = {"ok": False, "message": "Training failed – see server logs."}
    finally:
        STATE["running"] = False


def start():
    if STATE["running"]:
        return False
    STATE["running"] = True
    threading.Thread(target=_job, daemon=True).start()
    return True


def status():
    n = db.table("training_samples").select("id", count="exact").limit(1).execute().count or 0
    return {"model": risk.MODEL.meta, "samples": n, "running": STATE["running"], "last": STATE["last"]}


if __name__ == "__main__":
    print(run())
