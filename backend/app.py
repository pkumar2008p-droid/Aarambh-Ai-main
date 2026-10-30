"""AARAMBH Follow-up Navigator — Flask + PostgreSQL backend.

Render start command (from the repository root):
    gunicorn backend.app:app
"""

import hmac
import os
import re
import secrets
from datetime import timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import urlparse

import requests as http
from dotenv import load_dotenv
from flask import Flask, g, jsonify, request
from flask_cors import CORS
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.exceptions import HTTPException
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

# Resolve paths from this file, not from Render's current working directory.
BACKEND_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BACKEND_DIR.parent

# Load a local .env for development. Render environment variables take precedence.
load_dotenv(PROJECT_ROOT / ".env", override=False)

# This file is loaded as the `backend.app` package module by Gunicorn.
# Keep all backend modules in the same package to avoid import-path ambiguity.
from .core import *  # noqa: F401,F403,E402
from . import risk  # noqa: E402
from . import train as trainer  # noqa: E402

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
app.config.update(MAX_CONTENT_LENGTH=64 * 1024, JSON_SORT_KEYS=False)

# Cross-origin frontend support is opt-in and must use explicit trusted origins.
# Example: FRONTEND_ORIGINS=https://your-frontend.example,https://localhost:5173

def _normalise_origin(value):
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if (
        parsed.scheme not in ("http", "https")
        or not parsed.netloc
        or parsed.path not in ("", "/")
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "FRONTEND_ORIGINS must contain origins only, e.g. https://app.example.com"
        )
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


FRONTEND_ORIGINS = tuple(
    dict.fromkeys(
        _normalise_origin(origin)
        for origin in os.getenv("FRONTEND_ORIGINS", "").split(",")
        if origin.strip()
    )
)
if FRONTEND_ORIGINS:
    CORS(
        app,
        resources={r"/api/*": {"origins": list(FRONTEND_ORIGINS)}},
        supports_credentials=True,
        allow_headers=["Content-Type", "X-Requested-With"],
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    )

limiter = Limiter(
    get_remote_address,
    app=app,
    default_limits=["600/hour", "90/minute"],
    storage_uri="memory://",
)
risk.MODEL.load()
DUMMY = generate_password_hash("timing-equaliser")

# Cookie defaults retain the original same-site behavior. For a truly cross-site
# frontend, set COOKIE_SAMESITE=None in Render and serve the backend over HTTPS.
COOKIE_SAMESITE = os.getenv("COOKIE_SAMESITE", "Strict").strip().capitalize()
if COOKIE_SAMESITE not in ("Strict", "Lax", "None"):
    COOKIE_SAMESITE = "Strict"
COOKIE_SECURE = (not DEV) or COOKIE_SAMESITE == "None"

BARRIERS = ["Shared phone", "Long distance", "Unclear instructions", "Cost of travel", "Needs escort / wheelchair",
            "No one to accompany", "Language support needed", "Long waiting time"]
MODES = ["Call", "SMS", "WhatsApp", "Home / ASHA visit"]
RESULTS = ["Answered – will come", "Answered – needs help", "No answer", "Wrong number", "Switched off", "SMS delivered", "WhatsApp message sent"]
CLINICAL = ("doctor", "nurse", "clinician")


def err(m, c=400): return jsonify(error=m), c
def body(): return request.get_json(silent=True) or {}
def ip(): return request.remote_addr or ""

# ============================ security layer ============================
_connect_sources = " ".join(("'self'", *FRONTEND_ORIGINS))
CSP = (
    "default-src 'self'; script-src 'self'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data: https:; "
    f"connect-src {_connect_sources}; "
    "frame-src https://maps.google.com https://www.google.com; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'; "
    "object-src 'none'; upgrade-insecure-requests"
)

@app.after_request
def headers(r):
    h = r.headers
    h["Content-Security-Policy"] = CSP
    h["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
    h["X-Content-Type-Options"] = "nosniff"
    h["X-Frame-Options"] = "DENY"
    h["Referrer-Policy"] = "strict-origin-when-cross-origin"
    h["Permissions-Policy"] = "geolocation=(self), camera=(), microphone=(), payment=()"
    h["Cross-Origin-Opener-Policy"] = "same-origin"
    h["Cross-Origin-Resource-Policy"] = "same-origin"
    if request.path.startswith("/api") or request.path == "/":
        h["Cache-Control"] = "no-store"
    h.pop("Server", None)
    return r


@app.before_request
def guard():
    # CSRF: cookie policy + mandatory custom header + trusted-origin check
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        if request.headers.get("X-Requested-With") != "AARAMBH":
            return err("Forbidden", 403)
        o = request.headers.get("Origin")
        if o:
            request_origin = f"{request.scheme}://{request.host}".lower()
            same_origin = o.rstrip("/").lower() == request_origin
            trusted_frontend = o.rstrip("/").lower() in FRONTEND_ORIGINS
            if not (same_origin or trusted_frontend):
                return err("Forbidden", 403)
    g.sess = g.user = None
    if request.path.startswith("/api/"):
        s = get_session(request.cookies.get(COOKIE))
        if s:
            if s["kind"] == "patient":
                r = one("patients", s["subject"])
                if r:
                    g.user = {"type": "patient", "role": "patient", "id": r["id"], "name": dec(r["pii"]).get("name", ""), "lang": r["lang"]}
            else:
                r = one("staff", s["subject"])
                if r and r["active"]:
                    g.user = {"type": "staff", "role": r["role"], "id": r["id"], "name": r["name"], "dept": r["dept"], "code": r["staff_code"]}
            g.sess = s if g.user else None


def need_login(kind=None, roles=None):
    def deco(fn):
        @wraps(fn)
        def w(*a, **k):
            if not g.user:
                return err("Please log in", 401)
            if (kind and g.user["type"] != kind) or (roles and g.user["role"] not in roles):
                audit(g.user["name"], g.user["role"], "DENIED " + request.path, ip=ip())
                return err("Not allowed", 403)
            return fn(*a, **k)
        return w
    return deco


@app.errorhandler(Exception)
def fail(e):
    if isinstance(e, HTTPException):
        return err(e.name, e.code)
    log.exception("unhandled")
    return err("Something went wrong", 500)           # never leak internals

# ============================ shaping (decrypt only what a role needs) ============================
def P_of(r):
    d = dec(r["pii"])
    return {"id": r["id"], "name": d.get("name", ""), "phone": d.get("phone", ""), "altPhone": d.get("alt", ""), "area": d.get("area", ""),
            "lat": d.get("lat"), "lng": d.get("lng"), "age": r["age"], "lang": r["lang"], "sharedPhone": r["shared_phone"],
            "missedBefore": r["missed_before"], "digitalAccess": r["digital_access"]}

def C_of(c):
    return {"date": c["at"], "by": c["by_name"], "mode": c["mode"], "result": c["result"], "note": dec(c["note_enc"]).get("n", "") if c["note_enc"] else ""}

def F_of(r, cons):
    s = dec(r["secret"])
    return {"id": r["id"], "pid": r["patient_id"], "title": r["title"], "dept": r["dept"], "doctor": r["doctor"], "due": r["due"], "time": r["time"],
            "instructions": s.get("ins", ""), "docs": s.get("docs", []), "note": s.get("note", ""), "docsReady": r["docs_ready"] or [],
            "barriers": r["barriers"] or [], "contacts": [C_of(c) for c in cons], "status": r["status"], "confirmed": r["confirmed"],
            "planApproved": r["plan_approved"], "planApprovedBy": r["plan_approved_by"] or "",
            "reschedReq": {"date": r["resched_date"], "reason": r["resched_reason"] or ""} if r["resched_date"] else None,
            "callback": r["callback"], "escalated": r["escalated"], "outcome": r["outcome"] or "", "completedOn": r["completed_on"], "createdAt": r["created_at"]}

def group(rows):
    d = {}
    for c in rows:
        d.setdefault(c["followup_id"], []).append(c)
    return d

def minimal_p(P): return {"id": P["id"], "name": P["name"], "age": P["age"], "lang": P["lang"], "area": "", "phone": "", "altPhone": "", "sharedPhone": False, "missedBefore": 0, "distKm": 0}
def minimal_f(f):
    return {k: f[k] for k in ("id", "pid", "title", "dept", "due", "time", "status", "confirmed")} | {"doctor": "", "docs": [], "docsReady": [], "barriers": [], "contacts": [], "instructions": "", "note": "", "planApproved": False, "reschedReq": None, "callback": False}

def all_contacts(frs):
    return group(by_ids("contacts", "followup_id", [f["id"] for f in frs])) if frs else {}

@app.get("/api/bootstrap")
@need_login()
def bootstrap():
    u, role = g.user, g.user["role"]
    if u["type"] == "patient":
        P = P_of(one("patients", u["id"]))
        frs = paged(lambda: db.table("followups").select("*").eq("patient_id", u["id"]))
        cons = all_contacts(frs)
        fus = []
        for r in frs:
            f = F_of(r, cons.get(r["id"], []))
            f["contacts"] = [{"date": c["date"], "mode": c["mode"], "result": c["result"], "by": "Hospital", "note": ""} for c in f["contacts"]]
            f["note"], f["risk"], f["escalated"] = "", {"score": 0, "level": "low", "reasons": [], "actions": []}, False
            fus.append(f)
        P["distKm"] = risk.road_km(P["lat"], P["lng"])
        return jsonify(patients=[P], followups=fus, rx=[], tasks=[])
    audit(u["name"], role, "view_data", ip=ip())
    out = {"rx": [], "tasks": []}
    if role in ("ward", "pharmacist"):
        tasks = paged(lambda: db.table("tasks").select("*")) if role == "ward" else []
        rxs = paged(lambda: db.table("rx").select("*")) if role == "pharmacist" else []
        fids = {t["followup_id"] for t in tasks if t["followup_id"]} | {x["followup_id"] for x in rxs}
        frs = by_ids("followups", "id", fids) if fids else []
        pids = {t["patient_id"] for t in tasks} | {x["patient_id"] for x in rxs}
        pmap = {r["id"]: P_of(r) for r in (by_ids("patients", "id", pids) if pids else [])}
        fus = [minimal_f(F_of(r, [])) for r in frs]
        out["tasks"] = [{"id": t["id"], "pid": t["patient_id"], "fid": t["followup_id"], "type": t["type"], "from": t["from_loc"], "to": t["to_loc"], "when": t["when_txt"], "status": t["status"], "source": t["source"]} for t in tasks]
        out["rx"] = [{"id": x["id"], "fid": x["followup_id"], "pid": x["patient_id"], "items": dec(x["items_enc"]).get("i", []), "status": x["status"]} for x in rxs]
        out["patients"] = [minimal_p(p) for p in pmap.values()]
        out["followups"] = fus
        return jsonify(out)
    frs = paged(lambda: db.table("followups").select("*").eq("doctor", u["name"])) if role == "doctor" else paged(lambda: db.table("followups").select("*"))
    cons = all_contacts(frs)
    pmap = {r["id"]: P_of(r) for r in paged(lambda: db.table("patients").select("*"))}
    fus = [F_of(r, cons.get(r["id"], [])) for r in frs if r["patient_id"] in pmap]
    risk.attach(fus, pmap)
    for p in pmap.values():
        p["distKm"] = risk.road_km(p["lat"], p["lng"]); p.pop("lat"); p.pop("lng")
    out.update(patients=list(pmap.values()), followups=fus)
    if role == "admin":
        out["tasks"] = [{"id": t["id"], "status": t["status"], "type": t["type"], "pid": t["patient_id"]} for t in paged(lambda: db.table("tasks").select("*"))]
        out["rx"] = [{"id": x["id"], "status": x["status"], "fid": x["followup_id"], "pid": x["patient_id"], "items": []} for x in paged(lambda: db.table("rx").select("*"))]
        out["ml"] = trainer.status()
    return jsonify(out)

# ============================ authentication ============================
def send_sms(phone, text):
    sid, tok, frm = os.getenv("TWILIO_SID"), os.getenv("TWILIO_TOKEN"), os.getenv("TWILIO_FROM")
    if not (sid and tok and frm):
        log.warning("SMS not configured; OTP delivery is unavailable.")
        return
    try:
        response = http.post(f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", auth=(sid, tok),
                             data={"To": "+91" + phone, "From": frm, "Body": text}, timeout=10)
        response.raise_for_status()
    except Exception:
        log.exception("sms failed")

def issue(resp, token, kind):
    resp.set_cookie(COOKIE, token, max_age=int(ABSOLUTE[kind].total_seconds()), httponly=True, secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE, path="/")
    return resp

@app.post("/api/auth/patient/request-otp")
@limiter.limit("4/minute;15/hour")
def request_otp():
    ph = phone_ok(body().get("phone"))
    if ph:
        r = db.table("patients").select("id,is_demo").eq("phone_hash", hmac_hex(ph)).limit(1).execute().data
        if r:
            pid, n = r[0]["id"], now_utc()
            recent = db.table("otps").select("id", count="exact").eq("patient_id", pid).gte("created_at", iso(n - timedelta(minutes=15))).execute().count or 0
            if recent < 3:
                allow_demo_otp = DEV or os.getenv("ALLOW_DEMO_OTP", "false").strip().lower() == "true"
                demo = os.getenv("DEMO_OTP") if r[0]["is_demo"] and allow_demo_otp else None
                code = demo or f"{secrets.randbelow(10**6):06d}"
                db.table("otps").insert({"patient_id": pid, "code_hash": hmac_hex(pid + code), "expires_at": iso(n + timedelta(minutes=5))}).execute()
                if not demo:
                    send_sms(ph, f"{code} is your AARAMBH login OTP. Valid 5 minutes. Do not share it.")
    return jsonify(ok=True)                                  # same answer whether or not the number exists

@app.post("/api/auth/patient/verify")
@limiter.limit("10/minute")
def verify_otp():
    d = body()
    ph, otp = phone_ok(d.get("phone")), clean(d.get("otp"), 6)
    bad = err("Invalid or expired OTP", 401)
    if not ph or not re.fullmatch(r"\d{6}", otp):
        return bad
    r = db.table("patients").select("*").eq("phone_hash", hmac_hex(ph)).limit(1).execute().data
    if not r:
        return bad
    pid = r[0]["id"]
    rows = db.table("otps").select("*").eq("patient_id", pid).eq("used", False).gt("expires_at", iso(now_utc())).order("created_at", desc=True).limit(1).execute().data
    if not rows or rows[0]["attempts"] >= 5:
        return bad
    o = rows[0]
    if not hmac.compare_digest(o["code_hash"], hmac_hex(pid + otp)):
        db.table("otps").update({"attempts": o["attempts"] + 1}).eq("id", o["id"]).execute()
        audit("patient", "patient", "otp_failed", "patient", pid, ip())
        return bad
    db.table("otps").update({"used": True}).eq("id", o["id"]).execute()
    token = create_session("patient", pid, ip())
    audit("patient", "patient", "login", "patient", pid, ip())
    user = {"type": "patient", "id": pid, "name": dec(r[0]["pii"]).get("name", ""), "lang": r[0]["lang"]}
    return issue(jsonify(ok=True, user=user), token, "patient")

@app.post("/api/auth/staff/login")
@limiter.limit("6/minute;30/hour")
def staff_login():
    d = body()
    code, pw, role = clean(d.get("id"), 20).upper(), str(d.get("pw") or "")[:128], clean(d.get("role"), 20)
    bad = err("Incorrect ID or password", 401)
    r = db.table("staff").select("*").eq("staff_code", code).limit(1).execute().data
    s = r[0] if r else None
    if not s or not s["active"] or s["role"] != role:
        check_password_hash(DUMMY, pw)                       # same timing for unknown accounts
        audit(code, role, "login_failed", ip=ip())
        return bad
    if s["locked_until"] and ts(s["locked_until"]) > now_utc():
        return err("Too many attempts. Try again in 15 minutes.", 429)
    if not check_password_hash(s["pw_hash"], pw):
        n = s["failed_count"] + 1
        db.table("staff").update({"failed_count": 0 if n >= 5 else n, "locked_until": iso(now_utc() + timedelta(minutes=15)) if n >= 5 else None}).eq("id", s["id"]).execute()
        audit(code, role, "login_failed", ip=ip())
        return bad
    db.table("staff").update({"failed_count": 0, "locked_until": None}).eq("id", s["id"]).execute()
    token = create_session("staff", s["id"], ip())
    audit(s["name"], role, "login", ip=ip())
    return issue(jsonify(ok=True, user={"type": "staff", "id": s["id"], "name": s["name"], "role": role, "dept": s["dept"]}), token, "staff")

@app.post("/api/auth/logout")
def logout():
    drop_session(request.cookies.get(COOKIE))
    r = jsonify(ok=True)
    r.delete_cookie(COOKIE, path="/", secure=COOKIE_SECURE, samesite=COOKIE_SAMESITE)
    return r

@app.get("/api/session")
def session_info():
    return jsonify(user=g.user)

@app.get("/healthz")
def liveness():
    """Render process liveness check; does not require the DB schema yet."""
    return jsonify(ok=True, service="AARAMBH API"), 200


@app.get("/api/health")
def health():
    try:
        if not db_ping():
            return jsonify(ok=False, database="schema_incomplete"), 503
        return jsonify(ok=True, database="connected"), 200
    except Exception:
        log.exception("database health check failed")
        return jsonify(ok=False, database="unavailable"), 503

# ============================ shared mutation helpers ============================
def add_contact(fid, by, mode, result, note=""):
    db.table("contacts").insert({"followup_id": fid, "at": iso(today()), "by_name": by, "mode": mode, "result": result, "note_enc": enc({"n": clean(note, 300)}) if note else None}).execute()

def upd(fid, **k): db.table("followups").update(k).eq("id", fid).execute()

def build_one(row):
    pr = one("patients", row["patient_id"])
    return F_of(row, db.table("contacts").select("*").eq("followup_id", row["id"]).order("created_at").execute().data), P_of(pr)

def snapshot(fid):
    """Remember the features as they looked BEFORE the due date; they become the training sample once the outcome is known."""
    try:
        row = one("followups", fid)
        if row and row["status"] == "pending":
            F, P = build_one(row)
            if risk.days_to(F["due"]) >= 0:
                db.table("risk_snapshots").upsert({"followup_id": fid, "features": risk.features(F, P), "captured_at": iso(now_utc())}).execute()
    except Exception:
        log.exception("snapshot failed")

# ============================ patient actions ============================
def own(fid):
    r = db.table("followups").select("*").eq("id", fid).eq("patient_id", g.user["id"]).limit(1).execute().data
    return r[0] if r else None

def patient_action(fn):
    @wraps(fn)
    @need_login("patient")
    def w(fid):
        row = own(clean(fid, 40))
        if not row or row["status"] != "pending":
            return err("Not found", 404)
        out = fn(row)
        snapshot(row["id"])
        return out or jsonify(ok=True)
    return w

@app.post("/api/patient/followups/<fid>/confirm")
@patient_action
def p_confirm(row):
    upd(row["id"], confirmed=True)
    add_contact(row["id"], "Patient (portal)", "Portal", "Patient confirmed the visit")

@app.post("/api/patient/followups/<fid>/reschedule")
@patient_action
def p_resched(row):
    d, reason = date_ok(body().get("date")), clean(body().get("reason"), 80)
    if not d or d <= today() or d > today() + timedelta(days=120):
        return err("Choose a valid future date")
    upd(row["id"], resched_date=iso(d), resched_reason=reason)
    add_contact(row["id"], "Patient (portal)", "Portal", f"Reschedule requested to {iso(d)}", reason)

@app.post("/api/patient/followups/<fid>/barrier")
@patient_action
def p_barrier(row):
    b = [x for x in (body().get("barriers") or []) if x in BARRIERS and x != "Needs escort / wheelchair"][:8]
    note = clean(body().get("note"), 200)
    if not b and not note:
        return err("Select at least one option")
    upd(row["id"], barriers=list(dict.fromkeys((row["barriers"] or []) + b)))
    add_contact(row["id"], "Patient (portal)", "Portal", "Barrier reported: " + (", ".join(b) or "other"), note)

@app.post("/api/patient/followups/<fid>/assist")
@patient_action
def p_assist(row):
    a = [x for x in (body().get("options") or []) if x in ("wheel", "lang", "call")]
    if not a:
        return err("Select at least one option")
    bars = list(row["barriers"] or [])
    if "wheel" in a:
        db.table("tasks").insert({"patient_id": row["patient_id"], "followup_id": row["id"], "type": "Wheelchair + escort", "from_loc": "OPD Registration",
                                  "to_loc": row["dept"], "when_txt": "On arrival, " + row["due"], "source": "Patient request"}).execute()
        bars.append("Needs escort / wheelchair")
    if "lang" in a:
        bars.append("Language support needed")
    upd(row["id"], barriers=list(dict.fromkeys(bars)), **({"callback": True} if "call" in a else {}))
    add_contact(row["id"], "Patient (portal)", "Portal", "Help requested", ", ".join(a))

@app.post("/api/patient/followups/<fid>/docs")
@patient_action
def p_docs(row):
    n = len(dec(row["secret"]).get("docs", []))
    try:
        i = int(body().get("i"))
    except (TypeError, ValueError):
        return err("Invalid")
    if not 0 <= i < n:
        return err("Invalid")
    s = set(row["docs_ready"] or [])
    s.add(i) if body().get("checked") else s.discard(i)
    upd(row["id"], docs_ready=sorted(s))

@app.post("/api/patient/followups/<fid>/transport")
@patient_action
def p_transport(row):
    ride = clean(body().get("ride"), 60)
    P = P_of(one("patients", row["patient_id"]))
    bars = list(row["barriers"] or [])
    if risk.road_km(P["lat"], P["lng"]) > 20 and "Long distance" not in bars:
        bars.append("Long distance")
    upd(row["id"], callback=True, barriers=bars)
    add_contact(row["id"], "Patient (portal)", "Portal", "Requested transport help", f"{ride} to hospital on {row['due']}")

@app.post("/api/patient/profile")
@need_login("patient")
def p_profile():
    d = body()
    alt = clean(d.get("alt"), 10)
    if alt and not phone_ok(alt):
        return err("Enter a valid 10-digit number")
    lang = d.get("lang") if d.get("lang") in ("en", "hi", "pa") else "en"
    r = one("patients", g.user["id"])
    pii = dec(r["pii"]); pii["alt"] = alt
    db.table("patients").update({"pii": enc(pii), "lang": lang}).eq("id", r["id"]).execute()
    return jsonify(ok=True)

# ============================ staff actions ============================
def staff_fu(fid, roles=CLINICAL):
    if g.user["type"] != "staff" or g.user["role"] not in roles:
        return None
    row = one("followups", clean(fid, 40))
    if not row or (g.user["role"] == "doctor" and row["doctor"] != g.user["name"]):
        return None
    return row

def staff_action(roles=CLINICAL, audit_as=None):
    def deco(fn):
        @wraps(fn)
        @need_login("staff", roles)
        def w(fid):
            row = staff_fu(fid, roles)
            if not row:
                return err("Not found", 404)
            out = fn(row)
            if audit_as:
                audit(g.user["name"], g.user["role"], audit_as, "followup", row["id"], ip())
            snapshot(row["id"])
            return out or jsonify(ok=True)
        return w
    return deco

@app.post("/api/staff/followups/<fid>/approve")
@staff_action(audit_as="approve_plan")
def s_approve(row):
    upd(row["id"], plan_approved=True, plan_approved_by=g.user["name"])
    add_contact(row["id"], g.user["name"], "Review", "Navigation plan approved")

@app.post("/api/staff/followups/<fid>/contact")
@staff_action(audit_as="log_contact")
def s_contact(row):
    d = body()
    mode, res = d.get("mode"), d.get("result")
    if mode not in MODES or res not in RESULTS:
        return err("Invalid")
    add_contact(row["id"], g.user["name"], mode, res, d.get("note"))
    if res == "Answered – will come":
        upd(row["id"], confirmed=True)
    if res == "Answered – needs help":
        upd(row["id"], callback=False)

@app.post("/api/staff/followups/<fid>/note")
@staff_action(("doctor",), "doctor_note")
def s_note(row):
    s = dec(row["secret"]); s["note"] = clean(body().get("note"), 500)
    upd(row["id"], secret=enc(s))

@app.post("/api/staff/followups/<fid>/reschedule/<decision>")
@need_login("staff", CLINICAL)
def s_resched(fid, decision):
    row = staff_fu(fid)
    if not row or not row["resched_date"] or decision not in ("ok", "no"):
        return err("Not found", 404)
    if decision == "ok":
        add_contact(row["id"], g.user["name"], "Review", "Reschedule approved to " + row["resched_date"])
        upd(row["id"], due=row["resched_date"], resched_date=None, resched_reason=None, confirmed=False)
    else:
        add_contact(row["id"], g.user["name"], "Review", "Reschedule declined – call patient")
        upd(row["id"], resched_date=None, resched_reason=None)
    audit(g.user["name"], g.user["role"], "reschedule_" + decision, "followup", row["id"], ip())
    snapshot(row["id"])
    return jsonify(ok=True)

@app.post("/api/staff/followups/<fid>/outcome")
@need_login("staff", CLINICAL)
def s_outcome(fid):
    row = staff_fu(fid)
    o = body().get("o")
    if not row or row["status"] != "pending" or o not in ("attended", "elsewhere", "cancelled", "escalate"):
        return err("Not found", 404)
    by = g.user["name"]
    if o == "escalate":
        upd(row["id"], escalated=True); add_contact(row["id"], by, "Review", "Escalated to follow-up desk")
    elif o == "cancelled":
        upd(row["id"], status="closed", outcome="Cancelled by doctor"); add_contact(row["id"], by, "Review", "Outcome recorded: Cancelled by doctor")
    else:
        text = "Attended at this hospital" if o == "attended" else "Received care elsewhere (verified)"
        upd(row["id"], status="completed", outcome=text, completed_on=iso(today()))
        add_contact(row["id"], by, "Review", "Outcome recorded: " + text)
        snap = db.table("risk_snapshots").select("features").eq("followup_id", row["id"]).limit(1).execute().data
        if snap:                                              # LEARN: label = visit happened after the due date
            db.table("training_samples").upsert({"followup_id": row["id"], "features": snap[0]["features"], "label": int(today() > date_ok(row["due"]))}, on_conflict="followup_id").execute()
    audit(by, g.user["role"], "outcome_" + o, "followup", row["id"], ip())
    return jsonify(ok=True)

@app.post("/api/staff/followups")
@need_login("staff", ("doctor", "clinician"))
def s_create():
    d = body()
    pr = one("patients", clean(d.get("pid"), 40))
    due, tm = date_ok(d.get("due")), time_ok(d.get("time"))
    title, dept, ins = clean(d.get("title"), 120), clean(d.get("dept"), 80), clean(d.get("ins"), 600)
    doctor = g.user["name"] if g.user["role"] == "doctor" else clean(d.get("doctor"), 80)
    if not (pr and due and tm and title and dept and ins and doctor) or due < today():
        return err("Please fill all fields correctly")
    docs = [clean(x, 80) for x in str(d.get("docs") or "").split(",") if clean(x, 80)][:15]
    meds = [clean(x, 120) for x in str(d.get("meds") or "").split("\n") if clean(x, 120)][:20]
    r = db.table("followups").insert({"patient_id": pr["id"], "title": title, "dept": dept, "doctor": doctor, "due": iso(due), "time": tm,
                                      "secret": enc({"ins": ins, "docs": docs, "note": ""})}).execute().data[0]
    if meds:
        db.table("rx").insert({"followup_id": r["id"], "patient_id": pr["id"], "items_enc": enc({"i": meds})}).execute()
    audit(g.user["name"], g.user["role"], "create_plan", "followup", r["id"], ip())
    snapshot(r["id"])
    return jsonify(ok=True, id=r["id"])

@app.post("/api/staff/tasks/<tid>/done")
@need_login("staff", ("ward",))
def s_task(tid):
    db.table("tasks").update({"status": "done"}).eq("id", clean(tid, 40)).execute()
    audit(g.user["name"], "ward", "task_done", "task", tid, ip())
    return jsonify(ok=True)

@app.post("/api/staff/rx/<rid>/dispense")
@need_login("staff", ("pharmacist",))
def s_rx(rid):
    db.table("rx").update({"status": "dispensed"}).eq("id", clean(rid, 40)).execute()
    audit(g.user["name"], "pharmacist", "dispense", "rx", rid, ip())
    return jsonify(ok=True)

@app.post("/api/admin/retrain")
@need_login("staff", ("admin",))
@limiter.limit("3/hour")
def retrain():
    audit(g.user["name"], "admin", "retrain_ai", ip=ip())
    return jsonify(ok=True, started=trainer.start())

# ============================ AI chatbot (admin / travel help only, no medical advice) ============================
SYSTEM = ("You are AARAMBH Assistant for a district hospital follow-up portal in India. Help ONLY with: logging in, understanding follow-up dates, "
          "documents to bring, travel/ride planning, requesting reschedule, wheelchair or callback. NEVER give medical advice, diagnosis, medicine or "
          "dose information. For any emergency tell the person to call 108 (ambulance) or 112 immediately. For medical questions tell them to ask their doctor. "
          "Reply in the user's language (English, Hindi or Punjabi), in at most 4 short sentences. Never ask for or repeat phone numbers, OTPs or passwords.")
EMERG = re.compile(r"ambulance|emergency|chest pain|bleed|accident|unconscious|breath|suicide|आपात|एम्बुलेंस|ਐਮਰਜੈਂਸੀ", re.I)

@app.post("/api/chat")
@need_login()
@limiter.limit("12/minute")
def chat():
    m = clean(body().get("message"), 500)
    if not m:
        return err("Empty")
    if EMERG.search(m):
        return jsonify(reply="If this is an emergency, call 108 (ambulance) or 112 right now. I cannot give medical advice.")
    key = os.getenv("ANTHROPIC_API_KEY")
    if not key:
        return jsonify(reply=None)
    try:
        r = http.post("https://api.anthropic.com/v1/messages", timeout=20,
                      headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
                      json={"model": os.getenv("CLAUDE_MODEL", "claude-haiku-5-5"), "max_tokens": 300, "system": SYSTEM, "messages": [{"role": "user", "content": m}]})
        r.raise_for_status()
        return jsonify(reply="".join(b.get("text", "") for b in r.json().get("content", []))[:900])
    except Exception:
        log.exception("llm failed")
        return jsonify(reply=None)

# ============================ frontend ============================
@app.get("/")
def index():
    # The React frontend is deployed as a separate Render Static Site.
    return jsonify(service="AARAMBH API", ok=True, health="/api/health")

if __name__ == "__main__":
    app.run(port=int(os.getenv("PORT", 5000)))
