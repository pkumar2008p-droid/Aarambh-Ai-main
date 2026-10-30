"""One-time setup:  python -m backend.seed            -> creates staff accounts with RANDOM passwords (printed once)
                   python -m backend.seed --demo     -> also loads demo patients / follow-ups (hackathon demo)"""
import sys, secrets, string
from datetime import timedelta
from werkzeug.security import generate_password_hash
from .core import db, enc, hmac_hex, iso, now_utc, today

STAFF = [("DOC101", "doctor", "Dr. Harpreet Singh", "Medicine OPD"), ("DOC103", "doctor", "Dr. Rajiv Mehta", "Surgery OPD"),
         ("DOC104", "doctor", "Dr. Anil Verma", "Cardiology OPD"), ("NUR201", "nurse", "Nurse Simran Kaur", "OPD Ward"),
         ("CLN401", "clinician", "Clinician Manpreet", "Follow-up Desk"), ("WB301", "ward", "Ward Boy Sukhdev", "Patient Assistance"),
         ("PH501", "pharmacist", "Pharmacist Rohit", "Hospital Pharmacy"), ("ADM001", "admin", "Hospital Administrator", "Administration")]


def new_pw():
    a = string.ascii_letters + string.digits + "@#%+="
    return "".join(secrets.choice(a) for _ in range(16))


def day(n): return iso(today() + timedelta(days=n))


def seed_staff():
    have = {r["staff_code"] for r in db.table("staff").select("staff_code").execute().data}
    print("\n=== STAFF LOGINS (shown ONCE - save them now) ===")
    for code, role, name, dept in STAFF:
        if code in have:
            continue
        pw = new_pw()
        db.table("staff").insert({"staff_code": code, "role": role, "name": name, "dept": dept, "pw_hash": generate_password_hash(pw)}).execute()
        print(f"{role:11} {code:7} {pw}   ({name})")


# name, phone, age, area, lat, lng, lang, shared_phone, missed_before
PAT = [("Gurpreet Kaur", "9000000001", 58, "Jagraon", 30.7886, 75.4725, "pa", True, 1), ("Ramesh Kumar", "9000000002", 64, "Haibowal, Ludhiana", 30.9330, 75.8210, "hi", False, 0),
       ("Sunita Devi", "9000000003", 41, "Doraha", 30.7967, 76.0267, "hi", False, 0), ("Baljit Singh", "9000000004", 72, "Khanna", 30.7058, 76.2219, "pa", True, 2),
       ("Anita Sharma", "9000000005", 29, "Model Town, Ludhiana", 30.8890, 75.8420, "en", False, 0), ("Mohd. Irfan", "9000000006", 35, "Sahnewal", 30.8403, 75.9529, "hi", False, 1)]
# patient idx, title, dept, doctor, due offset, time, instructions, docs, barriers, [(day, by, mode, result, note)], status
FU = [(0, "BP review + blood test", "Medicine OPD", "Dr. Harpreet Singh", -4, "10:30", "Come for the blood test as instructed by the lab desk. Bring your previous prescription and lab slip.", ["Previous prescription", "Lab slip", "ID card"], ["Shared phone", "Long distance"], [(-5, "Nurse Simran Kaur", "Call", "No answer", "")], "pending"),
      (1, "Diabetes review", "Medicine OPD", "Dr. Harpreet Singh", 3, "11:00", "Bring your glucose diary and all current medicines.", ["Glucose diary", "Current medicines"], [], [], "pending"),
      (1, "Eye check-up (referral)", "Ophthalmology OPD", "Dr. Neha Arora", 10, "12:00", "Referral visit. Bring your previous eye reports if any.", ["Referral slip"], [], [], "pending"),
      (2, "Post-discharge wound check", "Surgery OPD", "Dr. Rajiv Mehta", 0, "09:30", "Bring the discharge summary. Wear loose clothing.", ["Discharge summary"], ["Unclear instructions"], [(-1, "Nurse Simran Kaur", "Call", "Answered – needs help", "Asked what to bring")], "pending"),
      (3, "Cardiology clinic follow-up", "Cardiology OPD", "Dr. Anil Verma", -9, "10:00", "Bring your ECG report and medicine strips.", ["ECG report", "Medicine strips"], ["Long distance", "Needs escort / wheelchair"], [(-7, "Nurse Simran Kaur", "Call", "No answer", ""), (-4, "Clinician Manpreet", "Call", "Wrong number", "Number belongs to a neighbour")], "pending"),
      (4, "Antenatal check-up", "Gynaecology OPD", "Dr. Kavita Gill", 7, "09:00", "Bring your maternity card and previous reports.", ["Maternity card", "Previous reports"], [], [], "pending"),
      (5, "Chest clinic review", "Chest OPD", "Dr. Sandeep Bedi", -2, "11:30", "Bring your treatment card and reports.", ["Treatment card"], ["Cost of travel"], [(-3, "Nurse Simran Kaur", "SMS", "SMS delivered", "")], "pending"),
      (0, "Medication review", "Medicine OPD", "Dr. Harpreet Singh", -30, "10:00", "Routine review.", [], [], [], "completed"),
      (3, "Physiotherapy session", "Physiotherapy", "Dr. Anil Verma", -20, "12:00", "Routine session.", [], [], [], "completed")]
RX = [(0, ["Amlodipine 5 mg - 30 tabs", "Telmisartan 40 mg - 30 tabs"]), (1, ["Metformin 500 mg - 60 tabs", "Glucose test strips - 1 box"]),
      (4, ["Atorvastatin 20 mg - 30 tabs", "Aspirin 75 mg - 30 tabs"]), (3, ["Dressing kit - 1", "Paracetamol 500 mg - 10 tabs"])]
TASKS = [(3, 4, "Wheelchair + escort", "OPD Registration", "Cardiology OPD, Room 7", "On arrival"), (2, 3, "Guide to department", "OPD Registration", "Surgery OPD", "Today 09:30"),
         (0, 0, "Escort to lab (sample collection)", "OPD Registration", "Pathology Lab", "On arrival")]


def seed_demo():
    if db.table("patients").select("id").eq("is_demo", True).limit(1).execute().data:
        print("Demo data already present."); return
    pids = []
    for n, ph, age, area, la, ln, lang, sh, ms in PAT:
        r = db.table("patients").insert({"phone_hash": hmac_hex(ph), "pii": enc({"name": n, "phone": ph, "alt": "", "area": area, "lat": la, "lng": ln}),
                                         "age": age, "lang": lang, "shared_phone": sh, "missed_before": ms, "is_demo": True}).execute().data[0]
        pids.append(r["id"])
    fids = []
    for pi, title, dept, doc, off, tm, ins, docs, bars, cons, st in FU:
        r = db.table("followups").insert({"patient_id": pids[pi], "title": title, "dept": dept, "doctor": doc, "due": day(off), "time": tm,
                                          "secret": enc({"ins": ins, "docs": docs, "note": ""}), "barriers": bars, "status": st,
                                          "outcome": "Attended at this hospital" if st == "completed" else None,
                                          "completed_on": day(off) if st == "completed" else None, "confirmed": st == "completed",
                                          "created_at": iso(now_utc() - timedelta(days=max(5, -off + 3)))}).execute().data[0]
        fids.append(r["id"])
        for d, by, mode, res, note in cons:
            db.table("contacts").insert({"followup_id": r["id"], "at": day(d), "by_name": by, "mode": mode, "result": res, "note_enc": enc({"n": note}) if note else None}).execute()
    for fi, items in RX:
        db.table("rx").insert({"followup_id": fids[fi], "patient_id": pids[FU[fi][0]], "items_enc": enc({"i": items})}).execute()
    for pi, fi, typ, fr, to, when in TASKS:
        db.table("tasks").insert({"patient_id": pids[pi], "followup_id": fids[fi], "type": typ, "from_loc": fr, "to_loc": to, "when_txt": when, "source": "Care plan"}).execute()
    print("Demo patients loaded. Demo patient phones: 9000000001 ... 9000000006 (OTP = your DEMO_OTP value)")


if __name__ == "__main__":
    seed_staff()
    if "--demo" in sys.argv:
        seed_demo()
