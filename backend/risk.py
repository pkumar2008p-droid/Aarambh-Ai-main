"""Risk engine = transparent rules (explainable) + trained XGBoost model (learned). Administrative risk only, NOT clinical urgency."""
import os, io, math, re, hashlib
from datetime import date
import pandas as pd
from .core import db, today, log, ts

H_LAT = float(os.getenv("HOSPITAL_LAT", "30.9097"))
H_LNG = float(os.getenv("HOSPITAL_LNG", "75.8527"))
FAILED = {"No answer", "Wrong number", "Switched off"}
OKP = ("Answered", "SMS delivered", "WhatsApp")
OUTCOME = {"Answered – will come": "answered", "Answered – needs help": "answered", "No answer": "no_answer",
           "Wrong number": "wrong_number", "Switched off": "switched_off",
           "SMS delivered": "message_sent", "WhatsApp message sent": "message_sent"}
FEATURES = ["days_to_due_date", "prior_missed_count", "contact_attempts", "contact_verified", "record_completeness",
            "record_age_days", "instructions_clarity", "contact_status", "travel_barrier", "document_barrier",
            "language_barrier", "digital_access", "followup_task_type", "staff_capacity_barrier", "last_contact_outcome"]


def road_km(lat, lng):
    if lat is None or lng is None:
        return 0.0
    r = math.radians
    a, b, c, d = lat, lng, H_LAT, H_LNG
    x = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2) ** 2
    return max(0.5, round(2 * 6371 * math.asin(math.sqrt(x)) * 1.3, 1))


def days_to(due): return (date.fromisoformat(due) - today()).days
def staff_contacts(F): return [c for c in F["contacts"] if c["by"] != "Patient (portal)"]


def features(F, P):
    sc = staff_contacts(F)
    last = sc[-1]["result"] if sc else None
    fails = [c["result"] in FAILED for c in sc]
    km = road_km(P["lat"], P["lng"])
    b = F["barriers"]
    d = days_to(F["due"])
    filled = [P["phone"], P["area"], P["lat"] is not None, P["lang"], F["instructions"], F["due"], F["time"],
              F["doctor"], F["dept"], bool(P["altPhone"]) or not P["sharedPhone"]]
    if len(fails) >= 2 and all(fails[-2:]):
        cs = "unreachable"
    elif any(fails) or (P["sharedPhone"] and not P["altPhone"]):
        cs = "intermittent"
    else:
        cs = "reachable"
    da = P.get("digitalAccess") or "good"
    if da == "good" and P["sharedPhone"]:
        da = "limited"
    return {
        "days_to_due_date": d, "prior_missed_count": P["missedBefore"], "contact_attempts": len(sc),
        "contact_verified": int(any(c["result"].startswith(OKP) for c in sc)),
        "record_completeness": round(sum(bool(x) for x in filled) / len(filled), 3),
        "record_age_days": max(0, (today() - ts(F["createdAt"]).date()).days),
        "instructions_clarity": "unclear" if "Unclear instructions" in b else "clear",
        "contact_status": cs,
        "travel_barrier": int("Long distance" in b or "Cost of travel" in b or km > 20),
        "document_barrier": int(bool(F["docs"]) and len(F["docsReady"]) < len(F["docs"]) and d <= 3),
        "language_barrier": int("Language support needed" in b),
        "digital_access": da,
        "followup_task_type": "investigation" if re.search(r"test|lab|ecg|scan|x-?ray|sample", F["title"], re.I) else "appointment",
        "staff_capacity_barrier": int("Long waiting time" in b),
        "last_contact_outcome": OUTCOME.get(last, "none") if last else "none"}


BARR = {"Unclear instructions": (10, "Instructions unclear to the patient", "Send simplified instructions in the patient’s language"),
        "Needs escort / wheelchair": (10, "Needs escort / wheelchair", "Book a ward-boy escort and wheelchair at arrival"),
        "Cost of travel": (10, "Travel cost is a concern", "Share low-cost ride options; check eligibility for hospital transport support"),
        "No one to accompany": (8, "No one to accompany the patient", "Ask the community health worker / family to help"),
        "Language support needed": (6, "Needs language support", "Assign a staff member who speaks the patient’s language"),
        "Long waiting time": (6, "Long waiting time reported", "Offer a timed slot to cut waiting")}


def rules(F, P):
    d, R, A, s = days_to(F["due"]), [], [], 0
    if d < 0:
        s += min(40, 10 + (-d) * 3); R.append(f"Overdue by {-d} day(s)"); A.append("Call the patient today and offer the earliest available slot")
    elif d <= 2 and not F["confirmed"]:
        s += 10; R.append("Due within 2 days and not yet confirmed"); A.append("Send a staff-approved reminder with clear instructions")
    if P["sharedPhone"] and not P["altPhone"]:
        s += 15; R.append("Shared phone number – message may not reach the patient"); A.append("Ask who answers this phone and record an alternate contact")
    km = road_km(P["lat"], P["lng"])
    if km > 40:
        s += 20; R.append(f"Lives about {km} km from the hospital"); A.append("Share travel / ride options and try to bundle lab + consultation on one day")
    elif km > 20:
        s += 10; R.append(f"Lives about {km} km from the hospital"); A.append("Share travel options and expected leave-by time")
    for x in F["barriers"]:
        if x in BARR:
            s += BARR[x][0]; R.append(BARR[x][1]); A.append(BARR[x][2])
    if P["missedBefore"] >= 1:
        s += 15; R.append(f"Missed {P['missedBefore']} earlier follow-up(s)")
    sc = staff_contacts(F)
    if sc and sc[-1]["result"] in FAILED:
        s += 10; R.append(f"Last contact attempt failed ({sc[-1]['result']})"); A.append("Retry at a different time, or reach the family / community health worker")
    if F["escalated"]:
        s += 10; R.append("Escalated by staff")
    if F["confirmed"]:
        s -= 25
    return max(0, min(100, s)), R, list(dict.fromkeys(A))


class _Model:
    def __init__(self): self.b, self.meta = None, {}

    def load(self):
        import joblib
        try:
            r = db.table("ml_models").select("*").eq("active", True).order("trained_at", desc=True).limit(1).execute().data
            if r:
                raw = r[0]["model_data"]
                if not isinstance(raw, (bytes, bytearray)):
                    raise ValueError("stored model is missing its binary payload")
                if hashlib.sha256(raw).hexdigest() != r[0]["sha256"]:
                    raise ValueError("model integrity check failed")
                self.b = joblib.load(io.BytesIO(raw))
                self.meta = {"name": r[0]["name"], "source": "PostgreSQL (self-trained)", "trained_at": r[0]["trained_at"], "n": r[0]["n_samples"]}
                return
        except Exception:
            log.exception("remote model not loaded")
        p = os.path.join(os.path.dirname(__file__), "models", "aarambh_best_model_bundle.joblib")
        try:
            if os.path.exists(p):
                self.b = joblib.load(p)
                self.meta = {"name": self.b.get("model_name"), "source": "bundled file"}
        except Exception:
            log.exception("bundled model not loaded (sklearn/xgboost version mismatch? run Retrain)")
            self.b = None

    def predict(self, feats):
        if not self.b or not feats:
            return [None] * len(feats)
        try:
            X = pd.DataFrame(feats)[self.b["feature_columns"]]
            return [float(x) for x in self.b["model"].predict_proba(X)[:, 1]]
        except Exception:
            log.exception("prediction failed; using rules only")
            return [None] * len(feats)


MODEL = _Model()


def level(s): return "high" if s >= 60 else "medium" if s >= 30 else "low"


def attach(fus, pmap):
    """Adds f['risk'] to every follow-up (rules + ML blend). Always explains WHY."""
    todo = []
    for f in fus:
        if f["status"] != "pending":
            f["risk"] = {"score": 0, "level": "low", "reasons": [], "actions": [], "ml": None}
            continue
        P = pmap[f["pid"]]
        s, R, A = rules(f, P)
        f["risk"] = {"score": s, "level": level(s), "reasons": R, "actions": A, "ml": None}
        todo.append((f, features(f, P), s))
    for (f, _, s), p in zip(todo, MODEL.predict([t[1] for t in todo])):
        if p is None:
            continue
        sc = round(0.6 * p * 100 + 0.4 * s)
        f["risk"].update(score=sc, level=level(sc), ml=round(p, 3))
        f["risk"]["reasons"].insert(0, f"AI model estimates a {round(p * 100)}% chance of a delayed follow-up")
