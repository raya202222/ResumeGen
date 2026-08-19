"""
╔══════════════════════════════════════════════════════╗
║          ResumeGen — Automated Resume Builder        ║
║  BUBT CSE Group 15 · Raya · Owalid · Maisha          ║
╠══════════════════════════════════════════════════════╣
║  HOW TO RUN:                                         ║
║  1. pip install flask reportlab                      ║
║  2. python app.py                                    ║
║  3. Open http://127.0.0.1:5000 in your browser       ║
╚══════════════════════════════════════════════════════╝
"""

# ── Auto-install missing packages ──────────────────────────────────────────
import subprocess, sys

def install(pkg):
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "-q"])

for pkg in ["flask", "reportlab", "werkzeug"]:
    try:
        __import__(pkg if pkg != "reportlab" else "reportlab.lib")
    except ImportError:
        print(f"Installing {pkg}...")
        install(pkg)

# ── Imports ─────────────────────────────────────────────────────────────────
import os, re, json, random, string, sqlite3
from datetime import datetime
from io import BytesIO

from flask import (Flask, render_template_string, request, redirect,
                   url_for, session, flash, send_file)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                 HRFlowable, Table, TableStyle)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER

# ── App setup ───────────────────────────────────────────────────────────────
app = Flask(__name__)
app.secret_key = "resumegen-bubt-2024"
DB  = "resumegen.db"
UPLOADS = "uploads"
os.makedirs(UPLOADS, exist_ok=True)

# ════════════════════════════════════════════════════════════════════════════
#  DATABASE
# ════════════════════════════════════════════════════════════════════════════
def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c

def init_db():
    c = db()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT, email TEXT UNIQUE, phone TEXT,
            password TEXT, verified INTEGER DEFAULT 0, otp TEXT
        );
        CREATE TABLE IF NOT EXISTS resumes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, title TEXT DEFAULT 'My Resume',
            personal TEXT, education TEXT, experience TEXT,
            skills TEXT, extras TEXT,
            template TEXT DEFAULT 'classic',
            color TEXT DEFAULT '#3B82F6',
            updated_at TEXT
        );
    """)
    c.commit(); c.close()

def current_user():
    if "uid" not in session: return None
    c = db()
    u = c.execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    c.close(); return u

# ════════════════════════════════════════════════════════════════════════════
#  ATS SCORER
# ════════════════════════════════════════════════════════════════════════════

def run_ats(draft):
    p    = draft.get("personal", {})
    ed   = draft.get("education", {})
    exps = draft.get("experience", [])
    sk   = draft.get("skills", {})
    ex   = draft.get("extras", {})

    score   = 0
    results = []   # list of (category, points, max, status, tips)

    # ── 1. Contact Info (20 pts) ──────────────────────────────────────────
    contact_pts = 0
    contact_tips = []
    checks = [("name",4,"Full name"),("email",4,"Email address"),("phone",4,"Phone number"),
              ("city",3,"City / location"),("linkedin",5,"LinkedIn URL")]
    for key, pts, label in checks:
        if p.get(key): contact_pts += pts
        else: contact_tips.append(f"Add your {label}")
    score += contact_pts
    results.append(("Contact Information", contact_pts, 20,
                    "✅ Complete" if contact_pts==20 else "⚠️ Incomplete", contact_tips))

    # ── 2. Career Objective (10 pts) ─────────────────────────────────────
    obj = ex.get("objective","").strip()
    obj_pts = 0; obj_tips = []
    if obj:
        words = len(obj.split())
        if words >= 30: obj_pts = 10
        elif words >= 15: obj_pts = 6; obj_tips.append("Expand your objective to at least 30 words")
        else: obj_pts = 3; obj_tips.append("Your objective is too short — aim for 30+ words")
    else:
        obj_tips.append("Add a career objective / personal summary")
    score += obj_pts
    results.append(("Career Objective", obj_pts, 10,
                    "✅ Strong" if obj_pts==10 else ("⚠️ Too short" if obj_pts>0 else "❌ Missing"), obj_tips))

    # ── 3. Education (15 pts) ─────────────────────────────────────────────
    edu_pts = 0; edu_tips = []
    ssc = ed.get("ssc",{}); hsc = ed.get("hsc",{}); bsc = ed.get("bsc",{})
    if bsc.get("university"): edu_pts += 7
    else: edu_tips.append("Add your BSc/Bachelor's degree details")
    if hsc.get("institute"): edu_pts += 4
    else: edu_tips.append("Add your HSC details")
    if ssc.get("institute"): edu_pts += 4
    else: edu_tips.append("Add your SSC details")
    score += edu_pts
    results.append(("Education", edu_pts, 15,
                    "✅ Complete" if edu_pts==15 else "⚠️ Incomplete", edu_tips))

    # ── 4. Work Experience (20 pts) ──────────────────────────────────────
    exp_pts = 0; exp_tips = []
    if exps:
        exp_pts += min(len(exps)*6, 12)
        descs = [e for e in exps if e.get("desc","").strip()]
        if descs: exp_pts += 8
        else: exp_tips.append("Add job descriptions to your experience entries")
        if exp_pts > 20: exp_pts = 20
    else:
        exp_tips.append("Add internship or work experience — even part-time counts")
        exp_pts = 0
    score += exp_pts
    results.append(("Work Experience", exp_pts, 20,
                    "✅ Strong" if exp_pts>=15 else ("⚠️ Could improve" if exp_pts>0 else "❌ Missing"), exp_tips))

    # ── 5. Skills (15 pts) ───────────────────────────────────────────────
    sk_pts = 0; sk_tips = []
    tech = sk.get("technical","").strip()
    soft = sk.get("soft","").strip()
    langs = sk.get("languages","").strip()
    if tech:
        tech_count = len([s for s in tech.split(",") if s.strip()])
        if tech_count >= 6: sk_pts += 8
        elif tech_count >= 3: sk_pts += 5; sk_tips.append("Add more technical skills (aim for 6+)")
        else: sk_pts += 3; sk_tips.append("List more technical skills — include tools, languages, frameworks")
    else: sk_tips.append("Add technical skills (Python, Flask, SQL, etc.)")
    if soft: sk_pts += 4
    else: sk_tips.append("Add soft skills (Teamwork, Leadership, etc.)")
    if langs: sk_pts += 3
    else: sk_tips.append("Add languages you know with proficiency level")
    score += sk_pts
    results.append(("Skills", sk_pts, 15,
                    "✅ Strong" if sk_pts>=12 else ("⚠️ Needs more" if sk_pts>0 else "❌ Missing"), sk_tips))

    # ── 6. Certifications & Extras (10 pts) ─────────────────────────────
    ext_pts = 0; ext_tips = []
    if ex.get("certifications","").strip(): ext_pts += 5
    else: ext_tips.append("Add certifications (Coursera, Udemy, etc.) to stand out")
    if ex.get("awards","").strip(): ext_pts += 3
    else: ext_tips.append("List any awards or achievements")
    if ex.get("hobbies","").strip(): ext_pts += 2
    else: ext_tips.append("Add hobbies & interests")
    score += ext_pts
    results.append(("Certifications & Extras", ext_pts, 10,
                    "✅ Good" if ext_pts>=7 else ("⚠️ Add more" if ext_pts>0 else "❌ Missing"), ext_tips))

    # ── 7. References (5 pts) ────────────────────────────────────────────
    ref_pts = 0; ref_tips = []
    if ex.get("references","").strip(): ref_pts = 5
    else: ref_tips.append("Add at least one reference (or write 'Available upon request')")
    score += ref_pts
    results.append(("References", ref_pts, 5,
                    "✅ Present" if ref_pts==5 else "❌ Missing", ref_tips))

    # ── 8. Profile completeness bonus (5 pts) ────────────────────────────
    bonus = 0; bonus_tips = []
    extra_fields = ["father","mother","dob","github","portfolio","current_address"]
    filled = sum(1 for f in extra_fields if p.get(f,"").strip())
    if filled >= 4: bonus = 5
    elif filled >= 2: bonus = 3; bonus_tips.append("Fill more personal details for a complete profile")
    else: bonus_tips.append("Add more personal details (DOB, address, GitHub, etc.)")
    score += bonus
    results.append(("Profile Completeness", bonus, 5,
                    "✅ Detailed" if bonus==5 else "⚠️ Add more", bonus_tips))

    # ── Grade ─────────────────────────────────────────────────────────────
    if score >= 90:   grade, grade_color = "A+", "#22C55E"
    elif score >= 80: grade, grade_color = "A",  "#22C55E"
    elif score >= 70: grade, grade_color = "B+", "#3B82F6"
    elif score >= 60: grade, grade_color = "B",  "#3B82F6"
    elif score >= 50: grade, grade_color = "C",  "#F59E0B"
    elif score >= 40: grade, grade_color = "D",  "#F97316"
    else:             grade, grade_color = "F",  "#EF4444"

    all_tips = []
    for cat, pts, mx, status, tips in results:
        all_tips.extend(tips)

    return {"score": score, "grade": grade, "grade_color": grade_color,
            "results": results, "tips": all_tips[:6]}


# ════════════════════════════════════════════════════════════════════════════
#  PDF GENERATOR
# ════════════════════════════════════════════════════════════════════════════
def hex_color(h):
    h = h.lstrip("#")
    return colors.Color(*[int(h[i:i+2],16)/255 for i in (0,2,4)])

def make_pdf(draft):
    buf = BytesIO()
    color   = hex_color(draft.get("color","#3B82F6"))
    gray    = colors.Color(.3,.3,.3)
    p       = draft.get("personal",{})
    ed      = draft.get("education",{})
    exps    = draft.get("experience",[])
    sk      = draft.get("skills",{})
    ex      = draft.get("extras",{})

    doc = SimpleDocTemplate(buf, pagesize=A4,
          leftMargin=1.5*cm, rightMargin=1.5*cm,
          topMargin=1.5*cm,  bottomMargin=1.5*cm)

    def sty(name, **kw):
        return ParagraphStyle(name, **kw)

    name_sty    = sty("N", fontSize=22, textColor=color, fontName="Helvetica-Bold", spaceAfter=2, alignment=TA_CENTER)
    contact_sty = sty("C", fontSize=9,  textColor=gray,  spaceAfter=4, alignment=TA_CENTER)
    sec_sty     = sty("S", fontSize=11, textColor=color, fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=2)
    body_sty    = sty("B", fontSize=9,  textColor=colors.black, spaceAfter=2, leading=13)
    lbl_sty     = sty("L", fontSize=9,  textColor=gray,  fontName="Helvetica-Bold", spaceAfter=1)
    sub_sty     = sty("U", fontSize=8,  textColor=gray,  spaceAfter=1)

    story = []
    def section(title):
        story.append(Spacer(1,4))
        story.append(Paragraph(title.upper(), sec_sty))
        story.append(HRFlowable(width="100%", thickness=1, color=color, spaceAfter=3))

    # Header
    story.append(Paragraph(p.get("name","Your Name"), name_sty))
    contacts = []
    for v,lbl in [(p.get("phone"),"📞"),(p.get("email"),"✉"),(p.get("city"),"📍"),(p.get("linkedin"),"🔗")]:
        if v: contacts.append(f"{lbl} {v}")
    story.append(Paragraph("   |   ".join(contacts), contact_sty))
    story.append(HRFlowable(width="100%", thickness=2, color=color, spaceBefore=4, spaceAfter=6))

    # Objective
    if ex.get("objective"):
        section("Career Objective")
        story.append(Paragraph(ex["objective"], body_sty))

    # Personal
    section("Personal Details")
    rows = [(k,v) for k,v in [("Father","father"),("Mother","mother"),("DOB","dob"),("Gender","gender"),
            ("Nationality","nationality"),("NID","nid"),("Address","current_address")] if p.get(v)]
    if rows:
        tdata = [[Paragraph(f"<b>{k}</b>",sub_sty), Paragraph(p[v],body_sty)] for k,v in rows]
        t = Table(tdata, colWidths=[3.5*cm,13*cm])
        t.setStyle(TableStyle([("VALIGN",(0,0),(-1,-1),"TOP"),("LEFTPADDING",(0,0),(-1,-1),0),("BOTTOMPADDING",(0,0),(-1,-1),1)]))
        story.append(t)

    # Education
    section("Education")
    for lbl, data, flds in [
        ("SSC",  ed.get("ssc",{}),  [("Institute","institute"),("Board","board"),("Year","year"),("GPA","gpa"),("Group","group")]),
        ("HSC",  ed.get("hsc",{}),  [("Institute","institute"),("Board","board"),("Year","year"),("GPA","gpa"),("Group","group")]),
        ("BSc",  ed.get("bsc",{}),  [("University","university"),("Dept","dept"),("Year","year"),("CGPA","cgpa"),("Degree","degree")]),
    ]:
        if any(data.get(v) for _,v in flds):
            story.append(Paragraph(f"<b>{lbl}</b>", lbl_sty))
            row = "  •  ".join(f"{k}: {data[v]}" for k,v in flds if data.get(v))
            story.append(Paragraph(row, sub_sty))
            story.append(Spacer(1,3))
    if ed.get("masters_enabled") and ed.get("masters"):
        m = ed["masters"]
        story.append(Paragraph("<b>Master's Degree</b>", lbl_sty))
        row = "  •  ".join(f"{k}: {m.get(v,'')}" for k,v in [("University","university"),("Dept","dept"),("Year","year"),("CGPA","cgpa")] if m.get(v))
        story.append(Paragraph(row, sub_sty))

    # Experience
    if exps:
        section("Work Experience")
        for e in exps:
            end = "Present" if e.get("current") else e.get("end","")
            story.append(Paragraph(f"<b>{e.get('title','')} — {e.get('company','')}</b>", lbl_sty))
            story.append(Paragraph(f"{e.get('type','')}  |  {e.get('start','')} – {end}", sub_sty))
            if e.get("desc"): story.append(Paragraph(e["desc"], body_sty))
            story.append(Spacer(1,3))

    # Skills
    section("Skills")
    for lbl, key in [("Technical Skills","technical"),("Soft Skills","soft"),("Languages","languages")]:
        if sk.get(key):
            story.append(Paragraph(f"<b>{lbl}:</b> {sk[key]}", body_sty))

    # Extras
    for title, key in [("Hobbies & Interests","hobbies"),("Awards & Achievements","awards"),
                       ("Certifications","certifications"),("Extracurricular Activities","activities"),
                       ("References","references")]:
        if ex.get(key):
            section(title)
            story.append(Paragraph(ex[key].replace("\n","<br/>"), body_sty))

    doc.build(story)
    buf.seek(0)
    return buf

# ════════════════════════════════════════════════════════════════════════════
#  HTML TEMPLATES  (all inline)
# ════════════════════════════════════════════════════════════════════════════

CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:'Poppins',sans-serif;background:#F8FAFC;color:#1e293b;min-height:100vh;display:flex;flex-direction:column}
a{text-decoration:none;color:inherit}
.navbar{background:#fff;border-bottom:1px solid #e2e8f0;position:sticky;top:0;z-index:100;box-shadow:0 1px 4px rgba(0,0,0,.06)}
.nav-container{max-width:1100px;margin:auto;padding:.9rem 2rem;display:flex;align-items:center;justify-content:space-between}
.logo{font-size:1.4rem;font-weight:700}.logo span{color:#3B82F6}
.nav-links{display:flex;align-items:center;gap:1rem}
.nav-link{color:#475569;font-size:.9rem;font-weight:500}.nav-link:hover{color:#3B82F6}
.nav-user{font-size:.85rem;color:#64748b}
.btn{display:inline-block;padding:.5rem 1.2rem;border-radius:8px;border:none;cursor:pointer;font-family:'Poppins',sans-serif;font-size:.9rem;font-weight:500;transition:.2s}
.btn-blue{background:#3B82F6;color:#fff}.btn-blue:hover{background:#2563EB}
.btn-outline{background:#fff;color:#3B82F6;border:1.5px solid #3B82F6}.btn-outline:hover{background:#EFF6FF}
.btn-red{background:#EF4444;color:#fff}.btn-red:hover{background:#DC2626}
.btn-gray{background:#e2e8f0;color:#475569}
.btn-green{background:#22C55E;color:#fff}
.btn-full{width:100%;padding:.7rem;margin-top:.5rem}
.alert{padding:.85rem 1.2rem;margin:1rem auto;max-width:900px;border-radius:8px;display:flex;justify-content:space-between;align-items:center;font-size:.88rem}
.alert button{background:none;border:none;cursor:pointer}
.alert-success{background:#DCFCE7;color:#166534}
.alert-danger{background:#FEE2E2;color:#991B1B}
.alert-info{background:#DBEAFE;color:#1E40AF}
.alert-warning{background:#FEF3C7;color:#92400E}
main{flex:1}
/* Auth */
.auth-page{min-height:80vh;display:flex;align-items:center;justify-content:center;padding:2rem}
.auth-card{background:#fff;border-radius:16px;padding:2.5rem;width:100%;max-width:480px;border:1px solid #e2e8f0;box-shadow:0 4px 20px rgba(0,0,0,.06)}
.auth-card h2{margin-bottom:.3rem;font-size:1.4rem}
.auth-card .sub{color:#64748b;margin-bottom:1.6rem;font-size:.88rem}
/* Forms */
.fg{margin-bottom:1rem}
.fg label{display:block;font-size:.83rem;font-weight:500;margin-bottom:.35rem;color:#374151}
.fg input,.fg select,.fg textarea{width:100%;padding:.55rem .85rem;border:1.5px solid #e2e8f0;border-radius:8px;font-family:'Poppins',sans-serif;font-size:.88rem;transition:border .2s;background:#fff}
.fg input:focus,.fg select:focus,.fg textarea:focus{outline:none;border-color:#3B82F6}
.fg textarea{resize:vertical;min-height:75px}
.form-err{color:#EF4444;font-size:.76rem;margin-top:.25rem}
.form-row{display:grid;grid-template-columns:1fr 1fr;gap:1rem}
.form-link{font-size:.83rem;color:#3B82F6;margin-top:1rem;display:block;text-align:center}
/* OTP */
.otp-wrap{display:flex;gap:.5rem;justify-content:center;margin:1.2rem 0}
.otp-wrap input{width:46px;height:54px;text-align:center;font-size:1.3rem;font-weight:600;border:2px solid #e2e8f0;border-radius:10px;font-family:'Poppins',sans-serif}
.otp-wrap input:focus{border-color:#3B82F6;outline:none}
/* Dashboard */
.dashboard{max-width:1100px;margin:2rem auto;padding:0 1.5rem}
.banner{background:linear-gradient(135deg,#3B82F6,#2563EB);color:#fff;border-radius:14px;padding:1.8rem;margin-bottom:1.8rem;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:1rem}
.banner h2{font-size:1.3rem;margin-bottom:.3rem}
.banner p{opacity:.85;font-size:.88rem}
.metric-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem;margin-bottom:1.8rem}
.metric{background:#fff;border-radius:12px;padding:1.2rem;border:1px solid #e2e8f0;text-align:center}
.metric .num{font-size:1.8rem;font-weight:700;color:#3B82F6}
.metric .lbl{font-size:.76rem;color:#64748b;margin-top:.2rem}
.sec-title{font-size:1rem;font-weight:600;margin-bottom:.8rem}
.r-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(270px,1fr));gap:1rem}
.r-card{background:#fff;border-radius:12px;padding:1.2rem;border:1px solid #e2e8f0}
.r-card h4{margin-bottom:.25rem}
.r-card .date{font-size:.75rem;color:#94a3b8;margin-bottom:.9rem}
.r-card-actions{display:flex;gap:.45rem;flex-wrap:wrap}
.r-card-actions a,.r-card-actions button{font-size:.76rem;padding:.3rem .7rem;border-radius:6px}
.empty{text-align:center;padding:3rem;color:#94a3b8}
/* Builder */
.builder{max-width:840px;margin:2rem auto;padding:0 1.5rem}
.prog-wrap{margin-bottom:1.8rem}
.prog-labels{display:flex;justify-content:space-between;margin-bottom:.4rem}
.prog-labels span{font-size:.7rem;color:#94a3b8;flex:1;text-align:center}
.prog-labels span.active{color:#3B82F6;font-weight:600}
.prog-track{height:5px;background:#e2e8f0;border-radius:3px;overflow:hidden}
.prog-fill{height:100%;background:#3B82F6;border-radius:3px}
.b-card{background:#fff;border-radius:14px;padding:1.8rem;border:1px solid #e2e8f0;margin-bottom:1.2rem}
.b-card h2{font-size:1.2rem;margin-bottom:.3rem}
.b-card .hint{color:#64748b;font-size:.83rem;margin-bottom:1.3rem}
.edu-block,.exp-block{background:#F8FAFC;border-radius:10px;padding:1.1rem;border:1px solid #e2e8f0;margin-bottom:.9rem}
.edu-block h4,.exp-block h4{margin-bottom:.75rem;color:#3B82F6;font-size:.9rem}
.step-nav{display:flex;justify-content:space-between;margin-top:1.3rem}
.toggle-row{display:flex;align-items:center;gap:.7rem;margin-bottom:.8rem}
.toggle-row input[type=checkbox]{width:16px;height:16px;accent-color:#3B82F6}
/* Template picker */
.tmpl-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:.9rem;margin-bottom:1.2rem}
.tmpl-opt{border:2px solid #e2e8f0;border-radius:10px;padding:.7rem;cursor:pointer;text-align:center;transition:.2s}
.tmpl-opt input{display:none}
.tmpl-opt.sel{border-color:#3B82F6;background:#EFF6FF}
.tmpl-thumb{height:70px;border-radius:6px;margin-bottom:.4rem;background:#f1f5f9;display:flex;align-items:center;justify-content:center;font-size:1.6rem}
.swatches{display:flex;gap:.6rem;flex-wrap:wrap;margin-bottom:1.2rem}
.swatch{width:34px;height:34px;border-radius:50%;cursor:pointer;border:3px solid transparent;transition:.2s}
.swatch.sel{border-color:#1e293b;transform:scale(1.1)}
/* Preview */
.preview-wrap{max-width:860px;margin:2rem auto;padding:0 1.5rem}
.preview-actions{display:flex;gap:.9rem;margin-bottom:1.2rem;justify-content:flex-end;flex-wrap:wrap}
.resume-paper{background:#fff;border-radius:4px;box-shadow:0 4px 24px rgba(0,0,0,.12);padding:2.2rem;min-height:600px}
.r-name{font-size:1.7rem;font-weight:700;color:#3B82F6}
.r-contacts{display:flex;gap:.9rem;flex-wrap:wrap;font-size:.78rem;color:#475569;margin-top:.35rem}
.r-sec{margin-bottom:1.1rem}
.r-sec-title{font-size:.82rem;font-weight:700;text-transform:uppercase;letter-spacing:.07em;color:#3B82F6;border-bottom:1px solid #e2e8f0;padding-bottom:.25rem;margin-bottom:.5rem}
/* Modal */
.modal-bg{position:fixed;inset:0;background:rgba(0,0,0,.4);display:flex;align-items:center;justify-content:center;z-index:999}
.modal{background:#fff;border-radius:14px;padding:2rem;width:340px;text-align:center;box-shadow:0 8px 32px rgba(0,0,0,.15)}
.modal h3{margin-bottom:.5rem}
.modal p{color:#666;margin-bottom:1.3rem;font-size:.88rem}
.modal-btns{display:flex;gap:.8rem;justify-content:center}
/* Hero */
.hero{max-width:1100px;margin:4rem auto;padding:0 2rem;display:grid;grid-template-columns:1fr 1fr;gap:3rem;align-items:center}
.hero h1{font-size:2.4rem;font-weight:700;line-height:1.2;margin-bottom:1rem}
.hero h1 span{color:#3B82F6}
.hero p{color:#64748b;margin-bottom:1.6rem;line-height:1.7}
.hero-btns{display:flex;gap:1rem;flex-wrap:wrap}
.hero-img{background:linear-gradient(135deg,#EFF6FF,#DBEAFE);border-radius:16px;padding:2rem;text-align:center;font-size:4rem}
.feat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1.3rem;max-width:1100px;margin:1rem auto 4rem;padding:0 2rem}
.feat-card{background:#fff;border-radius:12px;padding:1.3rem;border:1px solid #e2e8f0;text-align:center}
.feat-card .ico{font-size:1.8rem;margin-bottom:.6rem}
.feat-card h3{margin-bottom:.4rem;font-size:.95rem}
.feat-card p{color:#64748b;font-size:.82rem;line-height:1.5}
footer{background:#fff;border-top:1px solid #e2e8f0;text-align:center;padding:.9rem;font-size:.75rem;color:#94a3b8}
@media(max-width:700px){.hero{grid-template-columns:1fr}.hero-img{display:none}.feat-grid,.metric-grid,.form-row{grid-template-columns:1fr}.tmpl-grid{grid-template-columns:1fr 1fr}}
"""

def BASE(title="", content=""):
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ResumeGen""" + title + """</title>
<link href="https://fonts.googleapis.com/css2?family=Poppins:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>""" + CSS + """</style>
</head>
<body>
<nav class="navbar">
  <div class="nav-container">
    <a href="/" class="logo">Resume<span>Gen</span></a>
    <div class="nav-links">
      {% if user %}
        <span class="nav-user">👤 {{ user.name }}</span>
        <a href="/dashboard" class="nav-link">Dashboard</a>
        <button class="btn btn-outline" onclick="document.getElementById('logout-modal').style.display='flex'">Logout</button>
      {% else %}
        <a href="/login" class="nav-link">Login</a>
        <a href="/register" class="btn btn-blue">Register</a>
      {% endif %}
    </div>
  </div>
</nav>
{% for cat,msg in get_flashed_messages(with_categories=true) %}
<div class="alert alert-{{ cat }}">{{ msg }}<button onclick="this.parentElement.remove()">✕</button></div>
{% endfor %}
<main>""" + content + """</main>
{% if user %}
<div id="logout-modal" class="modal-bg" style="display:none">
  <div class="modal">
    <h3>Logout?</h3><p>You will be redirected to the home page.</p>
    <div class="modal-btns">
      <form method="POST" action="/logout"><button class="btn btn-red">Yes, Logout</button></form>
      <button class="btn btn-gray" onclick="document.getElementById('logout-modal').style.display='none'">Cancel</button>
    </div>
  </div>
</div>
{% endif %}
<footer>© 2024 ResumeGen · All rights reserved</footer>
</body></html>"""

PROGRESS = """
<div class="prog-wrap">
  <div class="prog-labels">
    {% for l in ['Personal','Education','Experience','Skills','Extras','Template'] %}
    <span class="{{ 'active' if loop.index==step else '' }}">{{ l }}</span>
    {% endfor %}
  </div>
  <div class="prog-track"><div class="prog-fill" style="width:{{ (step/6*100)|round }}%"></div></div>
</div>"""

# ════════════════════════════════════════════════════════════════════════════
#  ROUTES — Auth
# ════════════════════════════════════════════════════════════════════════════

@app.route("/")
def index():
    return render_template_string(BASE(title=""" — Create a Resume That Gets You Hired""", content="""
<style>
:root{--lp-blue:#2563EB;--lp-blue-dark:#1E4FD6;--lp-blue-light:#EFF6FF;--lp-blue-lighter:#F5F9FF;--lp-text:#0F172A;--lp-sub:#64748B}
.lp-hero{background:linear-gradient(135deg,#2563EB 0%,#3B82F6 55%,#60A5FA 100%);padding:5rem 2rem 4rem;text-align:center;color:#fff}
.lp-hero-inner{max-width:780px;margin:0 auto}
.lp-badge{display:inline-block;background:rgba(255,255,255,.15);border:1px solid rgba(255,255,255,.35);padding:.4rem 1rem;border-radius:999px;font-size:.78rem;font-weight:500;margin-bottom:1.3rem;letter-spacing:.02em}
.lp-hero h1{font-size:2.7rem;font-weight:700;line-height:1.2;margin-bottom:1.1rem}
.lp-hero p{font-size:1.05rem;color:#EFF6FF;line-height:1.7;margin-bottom:2rem;max-width:600px;margin-left:auto;margin-right:auto}
.lp-hero-btns{display:flex;gap:1rem;justify-content:center;flex-wrap:wrap}
.lp-btn{display:inline-block;padding:.85rem 2rem;border-radius:9px;font-weight:600;font-size:.95rem;transition:.2s;border:none;cursor:pointer;font-family:'Poppins',sans-serif}
.lp-btn-white{background:#fff;color:var(--lp-blue)}.lp-btn-white:hover{background:#EFF6FF;transform:translateY(-1px)}
.lp-btn-ghost{background:transparent;color:#fff;border:1.5px solid rgba(255,255,255,.6)}.lp-btn-ghost:hover{background:rgba(255,255,255,.12)}

.lp-stats{background:#fff;border-bottom:1px solid #e2e8f0}
.lp-stats-inner{max-width:1000px;margin:0 auto;padding:1.6rem 2rem;display:grid;grid-template-columns:repeat(5,1fr);gap:1rem;text-align:center}
.lp-stat .num{font-size:1.5rem;font-weight:700;color:var(--lp-blue)}
.lp-stat .lbl{font-size:.75rem;color:var(--lp-sub);margin-top:.15rem}

.lp-section{max-width:1100px;margin:0 auto;padding:4.5rem 2rem}
.lp-section-head{text-align:center;max-width:600px;margin:0 auto 2.8rem}
.lp-section-head h2{font-size:1.9rem;font-weight:700;color:var(--lp-text);margin-bottom:.6rem}
.lp-section-head p{color:var(--lp-sub);font-size:.95rem;line-height:1.6}

.lp-feat-bg{background:var(--lp-blue-light)}
.lp-feat-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:1.3rem}
.lp-feat-card{background:#fff;border-radius:14px;padding:1.6rem;border:1px solid #DBEAFE;transition:.2s}
.lp-feat-card:hover{transform:translateY(-3px);box-shadow:0 10px 24px rgba(37,99,235,.1)}
.lp-feat-ico{width:46px;height:46px;border-radius:11px;background:var(--lp-blue-light);display:flex;align-items:center;justify-content:center;font-size:1.4rem;margin-bottom:1rem}
.lp-feat-card h3{font-size:1.02rem;font-weight:600;color:var(--lp-text);margin-bottom:.4rem}
.lp-feat-card p{color:var(--lp-sub);font-size:.85rem;line-height:1.6}

.lp-steps{display:grid;grid-template-columns:repeat(3,1fr);gap:1.5rem;counter-reset:step}
.lp-step{text-align:center;padding:0 1rem}
.lp-step-num{width:52px;height:52px;border-radius:50%;background:var(--lp-blue);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1.15rem;margin:0 auto 1rem}
.lp-step h3{font-size:1rem;font-weight:600;color:var(--lp-text);margin-bottom:.4rem}
.lp-step p{color:var(--lp-sub);font-size:.85rem;line-height:1.6}

.lp-preview{background:var(--lp-blue-lighter)}
.lp-preview-wrap{display:grid;grid-template-columns:1fr 1fr;gap:3rem;align-items:center}
.lp-preview-text h2{font-size:1.9rem;font-weight:700;color:var(--lp-text);margin-bottom:.9rem}
.lp-preview-text p{color:var(--lp-sub);font-size:.95rem;line-height:1.7;margin-bottom:1.3rem}
.lp-preview-list{list-style:none;padding:0;margin:0 0 1.5rem}
.lp-preview-list li{display:flex;align-items:center;gap:.6rem;color:var(--lp-text);font-size:.88rem;margin-bottom:.6rem}
.lp-check{color:var(--lp-blue);font-weight:700}
.lp-resume-card{background:#fff;border-radius:16px;box-shadow:0 20px 45px rgba(37,99,235,.15);padding:1.8rem;border:1px solid #DBEAFE}
.lp-resume-head{display:flex;align-items:center;gap:1rem;border-bottom:2px solid var(--lp-blue);padding-bottom:1rem;margin-bottom:1.1rem}
.lp-resume-avatar{width:52px;height:52px;border-radius:50%;background:var(--lp-blue);color:#fff;display:flex;align-items:center;justify-content:center;font-weight:700;font-size:1.1rem}
.lp-resume-head h4{font-size:1.05rem;color:var(--lp-text);margin-bottom:.15rem}
.lp-resume-head span{font-size:.78rem;color:var(--lp-sub)}
.lp-resume-block{margin-bottom:1rem}
.lp-resume-block .lp-rlbl{font-size:.72rem;font-weight:700;color:var(--lp-blue);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.45rem}
.lp-resume-line{height:8px;background:#EFF6FF;border-radius:4px;margin-bottom:.45rem}
.lp-resume-line.w1{width:100%}.lp-resume-line.w2{width:85%}.lp-resume-line.w3{width:60%}
.lp-resume-tags{display:flex;gap:.4rem;flex-wrap:wrap}
.lp-resume-tag{background:var(--lp-blue-light);color:var(--lp-blue);font-size:.72rem;font-weight:600;padding:.3rem .7rem;border-radius:999px}

.lp-cta{background:linear-gradient(135deg,#2563EB 0%,#3B82F6 100%);text-align:center;padding:4rem 2rem;border-radius:20px;max-width:1000px;margin:0 auto 4rem;color:#fff}
.lp-cta h2{font-size:1.9rem;font-weight:700;margin-bottom:.7rem}
.lp-cta p{color:#EFF6FF;font-size:.98rem;margin-bottom:1.8rem}

@media(max-width:760px){
  .lp-hero h1{font-size:2rem}
  .lp-stats-inner{grid-template-columns:repeat(3,1fr)}
  .lp-feat-grid,.lp-steps{grid-template-columns:1fr}
  .lp-preview-wrap{grid-template-columns:1fr}
}
</style>

<section class="lp-hero">
  <div class="lp-hero-inner">
    <span class="lp-badge">✨ Free Resume Builder for Students & Professionals</span>
    <h1>Create a Resume That Gets You Hired</h1>
    <p>Build a polished, ATS-optimized resume in minutes with a guided step-by-step form, custom templates, and instant PDF download.</p>
    <div class="lp-hero-btns">
      <a href="/register" class="lp-btn lp-btn-white">Get Started Free</a>
      <a href="/login" class="lp-btn lp-btn-ghost">Login</a>
    </div>
  </div>
</section>

<div class="lp-stats">
  <div class="lp-stats-inner">
    <div class="lp-stat"><div class="num">6</div><div class="lbl">Easy Steps</div></div>
    <div class="lp-stat"><div class="num">PDF</div><div class="lbl">Instant Download</div></div>
    <div class="lp-stat"><div class="num">ATS</div><div class="lbl">Score Checker</div></div>
    <div class="lp-stat"><div class="num">4</div><div class="lbl">Templates</div></div>
    <div class="lp-stat"><div class="num">6</div><div class="lbl">Color Themes</div></div>
  </div>
</div>

<div class="lp-feat-bg">
  <div class="lp-section">
    <div class="lp-section-head">
      <h2>Everything You Need in One Place</h2>
      <p>From your first draft to a downloadable PDF, ResumeGen keeps the whole process simple.</p>
    </div>
    <div class="lp-feat-grid">
      <div class="lp-feat-card"><div class="lp-feat-ico">⚡</div><h3>Easy to Use</h3><p>Guided step-by-step form. No design knowledge needed.</p></div>
      <div class="lp-feat-card"><div class="lp-feat-ico">📥</div><h3>PDF Download</h3><p>Get a print-ready professional PDF instantly.</p></div>
      <div class="lp-feat-card"><div class="lp-feat-ico">🎨</div><h3>Custom Themes</h3><p>Choose templates and colors that match your style.</p></div>
      <div class="lp-feat-card"><div class="lp-feat-ico">📊</div><h3>ATS Scoring</h3><p>Instant score with tips to beat applicant tracking systems.</p></div>
      <div class="lp-feat-card"><div class="lp-feat-ico">🔒</div><h3>Secure</h3><p>Email OTP verification keeps your account safe.</p></div>
      <div class="lp-feat-card"><div class="lp-feat-ico">💾</div><h3>Save & Edit</h3><p>Save multiple resumes and edit them anytime.</p></div>
    </div>
  </div>
</div>

<div class="lp-section">
  <div class="lp-section-head">
    <h2>Ready in 3 Simple Steps</h2>
    <p>No design skills required — just fill in your details and download.</p>
  </div>
  <div class="lp-steps">
    <div class="lp-step"><div class="lp-step-num">1</div><h3>Fill Your Details</h3><p>Enter your personal info, education, experience, and skills through our guided form.</p></div>
    <div class="lp-step"><div class="lp-step-num">2</div><h3>Pick a Template</h3><p>Choose from 4 templates and 6 color themes to match your style.</p></div>
    <div class="lp-step"><div class="lp-step-num">3</div><h3>Download Your PDF</h3><p>Preview, check your ATS score, and download a ready-to-send resume.</p></div>
  </div>
</div>

<div class="lp-preview">
  <div class="lp-section">
    <div class="lp-preview-wrap">
      <div class="lp-preview-text">
        <h2>See Your Resume Come to Life</h2>
        <p>Watch your details transform into a clean, professional resume as you build it — with a live preview at every step.</p>
        <ul class="lp-preview-list">
          <li><span class="lp-check">✓</span> Clean, recruiter-friendly layouts</li>
          <li><span class="lp-check">✓</span> Real-time ATS score feedback</li>
          <li><span class="lp-check">✓</span> Export to PDF in one click</li>
        </ul>
        <a href="/register" class="lp-btn" style="background:var(--lp-blue);color:#fff">Try It Now</a>
      </div>
      <div class="lp-resume-card">
        <div class="lp-resume-head">
          <div class="lp-resume-avatar">JD</div>
          <div><h4>Jamie Doe</h4><span>Software Engineer · jamie@gmail.com</span></div>
        </div>
        <div class="lp-resume-block">
          <div class="lp-rlbl">Experience</div>
          <div class="lp-resume-line w1"></div><div class="lp-resume-line w2"></div><div class="lp-resume-line w3"></div>
        </div>
        <div class="lp-resume-block">
          <div class="lp-rlbl">Skills</div>
          <div class="lp-resume-tags">
            <span class="lp-resume-tag">Python</span><span class="lp-resume-tag">Flask</span><span class="lp-resume-tag">SQL</span><span class="lp-resume-tag">React</span>
          </div>
        </div>
      </div>
    </div>
  </div>
</div>

<div class="lp-cta">
  <h2>Ready to Build Your Resume?</h2>
  <p>Join now and create a job-winning resume in minutes — completely free.</p>
  <a href="/register" class="lp-btn lp-btn-white">Get Started Free</a>
</div>
"""), user=current_user())


@app.route("/register", methods=["GET","POST"])
def register():
    errors = {}
    if request.method == "POST":
        name  = request.form.get("name","").strip()
        email = request.form.get("email","").strip()
        phone = request.form.get("phone","").strip()
        pwd   = request.form.get("password","")
        cpwd  = request.form.get("confirm_password","")
        if not name:                                    errors["name"]  = "Full name is required."
        if not re.match(r'^[\w\.-]+@gmail\.com$',email): errors["email"] = "Must be a valid @gmail.com address."
        if not re.match(r'^\d{11}$', phone):            errors["phone"] = "Phone must be exactly 11 digits."
        if len(pwd) < 6:                                errors["password"] = "Password must be at least 6 characters."
        if pwd != cpwd:                                 errors["confirm_password"] = "Passwords do not match."
        if not errors:
            c = db()
            if c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
                errors["email"] = "This email is already registered."
            else:
                otp = "".join(random.choices(string.digits, k=6))
                c.execute("INSERT INTO users (name,email,phone,password,otp) VALUES (?,?,?,?,?)",
                          (name, email, phone, generate_password_hash(pwd), otp))
                c.commit()
                uid = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
                c.close()
                session["pending_uid"] = uid
                flash(f"Demo OTP (no email server needed): {otp}", "info")
                return redirect("/verify")
            c.close()
    return render_template_string(BASE(title=""" — Register""", content="""
<div class="auth-page">
  <div class="auth-card">
    <h2>Create Your Account</h2>
    <p class="sub">Join ResumeGen and build your professional resume today.</p>
    <form method="POST">
      <div class="fg"><label>Full Name</label>
        <input type="text" name="name" value="{{ form.get('name','') }}" placeholder="Raya Tabasum">
        {% if err.get('name') %}<div class="form-err">{{ err.name }}</div>{% endif %}
      </div>
      <div class="fg"><label>Email Address</label>
        <input type="email" name="email" value="{{ form.get('email','') }}" placeholder="yourname@gmail.com">
        {% if err.get('email') %}<div class="form-err">{{ err.email }}</div>{% endif %}
      </div>
      <div class="fg"><label>Phone Number</label>
        <input type="text" name="phone" value="{{ form.get('phone','') }}" placeholder="01XXXXXXXXX (11 digits)">
        {% if err.get('phone') %}<div class="form-err">{{ err.phone }}</div>{% endif %}
      </div>
      <div class="form-row">
        <div class="fg"><label>Password</label>
          <input type="password" name="password" placeholder="Min 6 characters">
          {% if err.get('password') %}<div class="form-err">{{ err.password }}</div>{% endif %}
        </div>
        <div class="fg"><label>Confirm Password</label>
          <input type="password" name="confirm_password" placeholder="Repeat password">
          {% if err.get('confirm_password') %}<div class="form-err">{{ err.confirm_password }}</div>{% endif %}
        </div>
      </div>
      <button class="btn btn-blue btn-full">Register</button>
    </form>
    <span class="form-link">Already have an account? <a href="/login">Login</a></span>
  </div>
</div>
"""), user=current_user(), err=errors, form=request.form)


@app.route("/verify", methods=["GET","POST"])
def verify():
    if "pending_uid" not in session: return redirect("/login")
    error = None
    if request.method == "POST":
        entered = "".join(request.form.get(f"otp{i}","") for i in range(1,7))
        c = db()
        u = c.execute("SELECT * FROM users WHERE id=?", (session["pending_uid"],)).fetchone()
        if u and u["otp"] == entered:
            c.execute("UPDATE users SET verified=1, otp=NULL WHERE id=?", (u["id"],))
            c.commit(); c.close()
            session.pop("pending_uid"); session["uid"] = u["id"]
            flash("Email verified! Welcome to ResumeGen.", "success")
            return redirect("/dashboard")
        error = "Invalid OTP. Please try again."; c.close()
    return render_template_string(BASE(title=""" — Verify Email""", content="""
<div class="auth-page">
  <div class="auth-card" style="text-align:center">
    <div style="font-size:3rem;margin-bottom:1rem">✉️</div>
    <h2>Verify Your Email</h2>
    <p class="sub">Enter the 6-digit OTP shown in the blue message above.<br><small>(Demo: no real email is sent)</small></p>
    <form method="POST">
      <div class="otp-wrap">
        {% for i in range(1,7) %}
        <input type="text" name="otp{{ i }}" maxlength="1" id="o{{ i }}"
          oninput="this.value=this.value.replace(/[^0-9]/,'');if(this.value&&document.getElementById('o{{ i+1 }}'))document.getElementById('o{{ i+1 }}').focus()"
          onkeydown="if(event.key==='Backspace'&&!this.value&&document.getElementById('o{{ i-1 }}'))document.getElementById('o{{ i-1 }}').focus()">
        {% endfor %}
      </div>
      {% if error %}<div class="form-err" style="margin-bottom:1rem;text-align:center">{{ error }}</div>{% endif %}
      <button class="btn btn-blue btn-full">Verify OTP</button>
    </form>
    <span class="form-link"><a href="/resend-otp">Resend OTP</a></span>
  </div>
</div>
"""), user=current_user(), error=error)


@app.route("/resend-otp")
def resend_otp():
    if "pending_uid" not in session: return redirect("/login")
    otp = "".join(random.choices(string.digits, k=6))
    c = db(); c.execute("UPDATE users SET otp=? WHERE id=?", (otp, session["pending_uid"])); c.commit(); c.close()
    flash(f"New OTP (demo): {otp}", "info")
    return redirect("/verify")


@app.route("/login", methods=["GET","POST"])
def login():
    error = None
    if request.method == "POST":
        email = request.form.get("email","").strip()
        pwd   = request.form.get("password","")
        c = db(); u = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone(); c.close()
        if u and check_password_hash(u["password"], pwd):
            if not u["verified"]:
                session["pending_uid"] = u["id"]
                flash("Please verify your email first.", "warning")
                return redirect("/verify")
            session["uid"] = u["id"]; return redirect("/dashboard")
        error = "Invalid email or password."
    return render_template_string(BASE(title=""" — Login""", content="""
<div class="auth-page">
  <div class="auth-card">
    <h2>Welcome Back 👋</h2>
    <p class="sub">Login to manage and download your resumes.</p>
    <form method="POST">
      <div class="fg"><label>Email Address</label><input type="email" name="email" placeholder="yourname@gmail.com" required></div>
      <div class="fg"><label>Password</label><input type="password" name="password" placeholder="Your password" required></div>
      {% if error %}<div class="form-err" style="margin-bottom:.8rem">{{ error }}</div>{% endif %}
      <button class="btn btn-blue btn-full">Login</button>
    </form>
    <span class="form-link">Don't have an account? <a href="/register">Register</a></span>
  </div>
</div>
"""), user=current_user(), error=error)


@app.route("/logout", methods=["POST"])
def logout():
    session.clear(); return redirect("/")

# ════════════════════════════════════════════════════════════════════════════
#  ROUTES — Dashboard
# ════════════════════════════════════════════════════════════════════════════

@app.route("/dashboard")
def dashboard():
    u = current_user()
    if not u: return redirect("/login")
    c = db()
    resumes = c.execute("SELECT * FROM resumes WHERE user_id=? ORDER BY updated_at DESC", (u["id"],)).fetchall()
    c.close()
    return render_template_string(BASE(title=""" — Dashboard""", content="""
<div class="dashboard">
  <div class="banner">
    <div><h2>Hello, {{ user.name }}! 👋</h2><p>Build, edit and download your professional resumes.</p></div>
    <a href="/builder/new" class="btn btn-blue" style="white-space:nowrap">+ Build New Resume</a>
  </div>
  <div class="metric-grid">
    <div class="metric"><div class="num">{{ resumes|length }}</div><div class="lbl">Resumes Created</div></div>
    <div class="metric"><div class="num">4</div><div class="lbl">Templates</div></div>
    <div class="metric"><div class="num">6</div><div class="lbl">Color Themes</div></div>
    <div class="metric"><div class="num">PDF</div><div class="lbl">Download Ready</div></div>
  </div>
  <div class="sec-title">My Resumes</div>
  {% if resumes %}
  <div class="r-grid">
    {% for r in resumes %}
    <div class="r-card">
      <h4>{{ r.title or 'My Resume' }}</h4>
      <div class="date">Updated: {{ r.updated_at[:10] if r.updated_at else 'N/A' }}</div>
      <div class="r-card-actions">
        <a href="/builder/{{ r.id }}/edit" class="btn btn-outline">✏️ Edit</a>
        <a href="/download/{{ r.id }}" class="btn btn-blue">⬇ PDF</a>
        <form method="POST" action="/delete/{{ r.id }}" onsubmit="return confirm('Delete this resume?')">
          <button class="btn btn-red">🗑</button>
        </form>
      </div>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div class="empty"><div style="font-size:3rem">📄</div><p style="margin-top:.8rem">No resumes yet. Click <strong>Build New Resume</strong> to start!</p></div>
  {% endif %}
</div>
"""), user=u, resumes=resumes)

# ════════════════════════════════════════════════════════════════════════════
#  ROUTES — Builder
# ════════════════════════════════════════════════════════════════════════════

@app.route("/builder/new")
def builder_new():
    if not current_user(): return redirect("/login")
    session.pop("rid", None); session["draft"] = {}
    return redirect("/builder/step/1")


@app.route("/builder/<int:rid>/edit")
def builder_edit(rid):
    u = current_user()
    if not u: return redirect("/login")
    c = db()
    r = c.execute("SELECT * FROM resumes WHERE id=? AND user_id=?", (rid, u["id"])).fetchone()
    c.close()
    if not r: flash("Resume not found.", "danger"); return redirect("/dashboard")
    session["rid"] = rid
    session["draft"] = {
        "personal":   json.loads(r["personal"] or "{}"),
        "education":  json.loads(r["education"] or "{}"),
        "experience": json.loads(r["experience"] or "[]"),
        "skills":     json.loads(r["skills"] or "{}"),
        "extras":     json.loads(r["extras"] or "{}"),
        "template":   r["template"], "color": r["color"], "title": r["title"],
    }
    return redirect("/builder/step/1")


@app.route("/builder/step/<int:step>", methods=["GET","POST"])
def builder_step(step):
    u = current_user()
    if not u: return redirect("/login")
    draft = session.get("draft", {})

    if request.method == "POST":
        f = request.form
        if step == 1:
            draft["personal"] = {k: f.get(k,"") for k in [
                "name","father","mother","dob","gender","nationality","religion","nid",
                "phone","alt_phone","email","current_address","permanent_address",
                "city","country","linkedin","github","portfolio"]}
        elif step == 2:
            draft["education"] = {
                "ssc":    {k: f.get(f"ssc_{k}","")    for k in ["institute","board","year","gpa","group"]},
                "hsc":    {k: f.get(f"hsc_{k}","")    for k in ["institute","board","year","gpa","group"]},
                "bsc":    {k: f.get(f"bsc_{k}","")    for k in ["university","dept","year","cgpa","degree"]},
                "masters_enabled": f.get("masters_enabled") == "on",
                "masters":{k: f.get(f"masters_{k}","") for k in ["university","dept","year","cgpa","thesis"]},
            }
        elif step == 3:
            exps = []
            if f.get("no_experience") != "on":
                for title,company,typ,start,end,desc in zip(
                    f.getlist("job_title"), f.getlist("company"), f.getlist("emp_type"),
                    f.getlist("exp_start"), f.getlist("exp_end"), f.getlist("job_desc")):
                    if title:
                        exps.append({"title":title,"company":company,"type":typ,
                                     "start":start,"end":end,"current":False,"desc":desc})
            draft["experience"] = exps
        elif step == 4:
            draft["skills"] = {k: f.get(k,"") for k in ["technical","soft","languages"]}
        elif step == 5:
            draft["extras"] = {k: f.get(k,"") for k in
                ["objective","hobbies","activities","awards","certifications","publications","references"]}
        elif step == 6:
            draft.update({"template": f.get("template","classic"),
                          "color":    f.get("color","#3B82F6"),
                          "title":    f.get("title","My Resume")})

        session["draft"] = draft; session.modified = True
        return redirect(f"/builder/step/{step+1}" if step < 6 else "/preview")

    # ── GET — render the right step ─────────────────────────────────────────
    p = PROGRESS
    d = draft

    if step == 1:
        pd = d.get("personal", {})
        return render_template_string(BASE(title=""" — Step 1""", content="""
<div class="builder">
  """ + p + """
  <div class="b-card">
    <h2>Step 1 — Personal Details</h2>
    <p class="hint">This information appears at the top of your resume.</p>
    <form method="POST">
      <div class="form-row">
        <div class="fg"><label>Full Name *</label><input type="text" name="name" value="{{ pd.get('name','') }}" placeholder="Raya Tabasum" required></div>
        <div class="fg"><label>Email</label><input type="email" name="email" value="{{ pd.get('email','') }}" placeholder="yourname@gmail.com"></div>
      </div>
      <div class="form-row">
        <div class="fg"><label>Phone</label><input type="text" name="phone" value="{{ pd.get('phone','') }}" placeholder="01XXXXXXXXX"></div>
        <div class="fg"><label>Alternate Phone</label><input type="text" name="alt_phone" value="{{ pd.get('alt_phone','') }}"></div>
      </div>
      <div class="form-row">
        <div class="fg"><label>Father's Name</label><input type="text" name="father" value="{{ pd.get('father','') }}"></div>
        <div class="fg"><label>Mother's Name</label><input type="text" name="mother" value="{{ pd.get('mother','') }}"></div>
      </div>
      <div class="form-row">
        <div class="fg"><label>Date of Birth</label><input type="date" name="dob" value="{{ pd.get('dob','') }}"></div>
        <div class="fg"><label>Gender</label>
          <select name="gender">
            {% for g in ['','Male','Female','Other'] %}
            <option value="{{ g }}" {{ 'selected' if pd.get('gender','') == g }}>{{ g or 'Select' }}</option>
            {% endfor %}
          </select>
        </div>
      </div>
      <div class="form-row">
        <div class="fg"><label>Nationality</label><input type="text" name="nationality" value="{{ pd.get('nationality','Bangladeshi') }}"></div>
        <div class="fg"><label>Religion</label><input type="text" name="religion" value="{{ pd.get('religion','') }}"></div>
      </div>
      <div class="fg"><label>NID / Passport Number</label><input type="text" name="nid" value="{{ pd.get('nid','') }}"></div>
      <div class="fg"><label>Current Address</label><textarea name="current_address">{{ pd.get('current_address','') }}</textarea></div>
      <div class="fg"><label>Permanent Address</label><textarea name="permanent_address">{{ pd.get('permanent_address','') }}</textarea></div>
      <div class="form-row">
        <div class="fg"><label>City</label><input type="text" name="city" value="{{ pd.get('city','Dhaka') }}"></div>
        <div class="fg"><label>Country</label><input type="text" name="country" value="{{ pd.get('country','Bangladesh') }}"></div>
      </div>
      <div class="fg"><label>LinkedIn</label><input type="text" name="linkedin" value="{{ pd.get('linkedin','') }}" placeholder="linkedin.com/in/yourname"></div>
      <div class="fg"><label>GitHub</label><input type="text" name="github" value="{{ pd.get('github','') }}" placeholder="github.com/yourname"></div>
      <div class="fg"><label>Portfolio Website</label><input type="text" name="portfolio" value="{{ pd.get('portfolio','') }}"></div>
      <div class="step-nav">
        <a href="/dashboard" class="btn btn-gray">← Dashboard</a>
        <button class="btn btn-blue">Next →</button>
      </div>
    </form>
  </div>
</div>
"""), user=u, step=step, pd=pd)

    if step == 2:
        ed = d.get("education", {})
        return render_template_string(BASE(title=""" — Step 2""", content="""
<div class="builder">
  """ + p + """
  <div class="b-card">
    <h2>Step 2 — Education Details</h2>
    <p class="hint">Fill your academic background. Leave blank what doesn't apply.</p>
    <form method="POST">
      {% for key, title, fields in [
        ('ssc','SSC (Secondary School Certificate)',[('Institute','institute'),('Board','board'),('Year','year'),('GPA','gpa'),('Group','group')]),
        ('hsc','HSC (Higher Secondary Certificate)',[('Institute','institute'),('Board','board'),('Year','year'),('GPA','gpa'),('Group','group')]),
        ('bsc','BSc / Bachelor\'s Degree',[('University','university'),('Department','dept'),('Year','year'),('CGPA','cgpa'),('Degree Title','degree')])
      ] %}
      <div class="edu-block">
        <h4>🎓 {{ title }}</h4>
        <div class="form-row">
          <div class="fg"><label>{{ fields[0][0] }}</label><input type="text" name="{{ key }}_{{ fields[0][1] }}" value="{{ ed.get(key,{}).get(fields[0][1],'') }}"></div>
          <div class="fg"><label>{{ fields[1][0] }}</label><input type="text" name="{{ key }}_{{ fields[1][1] }}" value="{{ ed.get(key,{}).get(fields[1][1],'') }}" placeholder="Dhaka Board" style="{{ '' if key=='bsc' else '' }}"></div>
        </div>
        <div class="form-row">
          <div class="fg"><label>{{ fields[2][0] }}</label><input type="text" name="{{ key }}_{{ fields[2][1] }}" value="{{ ed.get(key,{}).get(fields[2][1],'') }}" placeholder="2022"></div>
          <div class="fg"><label>{{ fields[3][0] }}</label><input type="text" name="{{ key }}_{{ fields[3][1] }}" value="{{ ed.get(key,{}).get(fields[3][1],'') }}" placeholder="5.00 / 3.80"></div>
        </div>
        <div class="fg"><label>{{ fields[4][0] }}</label><input type="text" name="{{ key }}_{{ fields[4][1] }}" value="{{ ed.get(key,{}).get(fields[4][1],'') }}" placeholder="{{ 'B.Sc. in CSE' if key=='bsc' else 'Science / Commerce / Arts' }}"></div>
      </div>
      {% endfor %}
      <div class="edu-block">
        <div class="toggle-row">
          <input type="checkbox" name="masters_enabled" id="mt" {{ 'checked' if ed.get('masters_enabled') }}>
          <label for="mt" style="font-weight:600;color:#3B82F6;cursor:pointer">🎓 Master's Degree (Optional)</label>
        </div>
        <div id="mf" style="{{ '' if ed.get('masters_enabled') else 'display:none' }}">
          <div class="form-row">
            <div class="fg"><label>University</label><input type="text" name="masters_university" value="{{ ed.get('masters',{}).get('university','') }}"></div>
            <div class="fg"><label>Department</label><input type="text" name="masters_dept" value="{{ ed.get('masters',{}).get('dept','') }}"></div>
          </div>
          <div class="form-row">
            <div class="fg"><label>Year</label><input type="text" name="masters_year" value="{{ ed.get('masters',{}).get('year','') }}"></div>
            <div class="fg"><label>CGPA</label><input type="text" name="masters_cgpa" value="{{ ed.get('masters',{}).get('cgpa','') }}"></div>
          </div>
          <div class="fg"><label>Thesis Title (optional)</label><input type="text" name="masters_thesis" value="{{ ed.get('masters',{}).get('thesis','') }}"></div>
        </div>
      </div>
      <div class="step-nav">
        <a href="/builder/step/1" class="btn btn-gray">← Back</a>
        <button class="btn btn-blue">Next →</button>
      </div>
    </form>
  </div>
</div>
<script>document.getElementById('mt').addEventListener('change',function(){document.getElementById('mf').style.display=this.checked?'':'none'})</script>
"""), user=u, step=step, ed=ed)

    if step == 3:
        exps = d.get("experience", [])
        return render_template_string(BASE(title=""" — Step 3""", content="""
<div class="builder">
  """ + p + """
  <div class="b-card">
    <h2>Step 3 — Work Experience</h2>
    <p class="hint">Add your work history, or tick the checkbox if you have no experience.</p>
    <form method="POST">
      <div class="toggle-row"><input type="checkbox" name="no_experience" id="noexp" {{ 'checked' if not exps }}>
        <label for="noexp" style="cursor:pointer">I have no work experience yet</label>
      </div>
      <div id="exp-cont">
        {% set show = exps if exps else [{}] %}
        {% for exp in show %}
        <div class="exp-block">
          <div class="form-row">
            <div class="fg"><label>Job Title</label><input type="text" name="job_title" value="{{ exp.get('title','') }}" placeholder="Software Engineer"></div>
            <div class="fg"><label>Company Name</label><input type="text" name="company" value="{{ exp.get('company','') }}" placeholder="Tech Company Ltd."></div>
          </div>
          <div class="fg"><label>Employment Type</label>
            <select name="emp_type">{% for t in ['Full-time','Part-time','Internship','Freelance','Remote'] %}<option value="{{ t }}" {{ 'selected' if exp.get('type')==t }}>{{ t }}</option>{% endfor %}</select>
          </div>
          <div class="form-row">
            <div class="fg"><label>Start Date</label><input type="month" name="exp_start" value="{{ exp.get('start','') }}"></div>
            <div class="fg"><label>End Date</label><input type="month" name="exp_end" value="{{ exp.get('end','') }}"></div>
          </div>
          <div class="fg"><label>Job Description</label><textarea name="job_desc" placeholder="Describe your role and responsibilities...">{{ exp.get('desc','') }}</textarea></div>
        </div>
        {% endfor %}
      </div>
      <button type="button" class="btn btn-outline" style="margin-bottom:.8rem" onclick="addExp()">+ Add Another</button>
      <div class="step-nav">
        <a href="/builder/step/2" class="btn btn-gray">← Back</a>
        <button class="btn btn-blue">Next →</button>
      </div>
    </form>
  </div>
</div>
<script>
function addExp(){
  var c=document.getElementById('exp-cont'),b=c.children[0].cloneNode(true);
  b.querySelectorAll('input,textarea').forEach(function(e){e.value=''});c.appendChild(b);
}
document.getElementById('noexp').addEventListener('change',function(){
  document.getElementById('exp-cont').style.opacity=this.checked?.3:1;
  document.getElementById('exp-cont').style.pointerEvents=this.checked?'none':'';
});
</script>
"""), user=u, step=step, exps=exps)

    if step == 4:
        sk = d.get("skills", {})
        return render_template_string(BASE(title=""" — Step 4""", content="""
<div class="builder">
  """ + p + """
  <div class="b-card">
    <h2>Step 4 — Skills</h2>
    <p class="hint">Separate multiple items with commas.</p>
    <form method="POST">
      <div class="fg"><label>Technical Skills</label>
        <input type="text" name="technical" value="{{ sk.get('technical','') }}" placeholder="Python, Flask, HTML, CSS, JavaScript, SQL, Git"></div>
      <div class="fg"><label>Soft Skills</label>
        <input type="text" name="soft" value="{{ sk.get('soft','') }}" placeholder="Teamwork, Leadership, Communication, Problem Solving"></div>
      <div class="fg"><label>Languages Known (with proficiency)</label>
        <input type="text" name="languages" value="{{ sk.get('languages','') }}" placeholder="Bengali (Native), English (Fluent)"></div>
      <div class="step-nav">
        <a href="/builder/step/3" class="btn btn-gray">← Back</a>
        <button class="btn btn-blue">Next →</button>
      </div>
    </form>
  </div>
</div>
"""), user=u, step=step, sk=sk)

    if step == 5:
        ex = d.get("extras", {})
        return render_template_string(BASE(title=""" — Step 5""", content="""
<div class="builder">
  """ + p + """
  <div class="b-card">
    <h2>Step 5 — Additional Information</h2>
    <p class="hint">All fields are optional but make your resume stand out.</p>
    <form method="POST">
      <div class="fg"><label>Career Objective / Personal Summary</label>
        <textarea name="objective" rows="4" placeholder="A motivated CSE student seeking opportunities...">{{ ex.get('objective','') }}</textarea></div>
      <div class="fg"><label>Hobbies & Interests</label>
        <input type="text" name="hobbies" value="{{ ex.get('hobbies','') }}" placeholder="Reading, Traveling, Photography, Gaming"></div>
      <div class="fg"><label>Extracurricular Activities</label>
        <textarea name="activities" placeholder="Debate Club, Programming Club Member...">{{ ex.get('activities','') }}</textarea></div>
      <div class="fg"><label>Awards & Achievements</label>
        <textarea name="awards" placeholder="Dean's List 2023, First Place Hackathon 2024...">{{ ex.get('awards','') }}</textarea></div>
      <div class="fg"><label>Certifications (one per line: Name — Issuer — Year)</label>
        <textarea name="certifications" placeholder="Python for Everybody — Coursera — 2023">{{ ex.get('certifications','') }}</textarea></div>
      <div class="fg"><label>Publications (optional)</label>
        <textarea name="publications" placeholder="Research paper title, Journal, Year...">{{ ex.get('publications','') }}</textarea></div>
      <div class="fg"><label>References</label>
        <textarea name="references" placeholder="Prof. Dr. John Doe, Head of CSE, BUBT, john@bubt.edu&#10;Available upon request">{{ ex.get('references','') }}</textarea></div>
      <div class="step-nav">
        <a href="/builder/step/4" class="btn btn-gray">← Back</a>
        <button class="btn btn-blue">Next →</button>
      </div>
    </form>
  </div>
</div>
"""), user=u, step=step, ex=ex)

    if step == 6:
        return render_template_string(BASE(title=""" — Step 6""", content="""
<div class="builder">
  """ + p + """
  <div class="b-card">
    <h2>Step 6 — Customize Your Resume</h2>
    <p class="hint">Pick a template and color theme for your final resume.</p>
    <form method="POST">
      <div class="fg"><label>Resume Title (for your records)</label>
        <input type="text" name="title" value="{{ d.get('title','My Resume') }}" placeholder="My Resume"></div>
      <label style="font-weight:500;display:block;margin-bottom:.7rem">Choose Template</label>
      <div class="tmpl-grid" id="tmpl-grid">
        {% for val,ico,name in [('classic','📄','Classic'),('modern','🖥','Modern'),('minimal','⬜','Minimal'),('creative','🎨','Creative')] %}
        <div class="tmpl-opt {{ 'sel' if d.get('template','classic')==val else '' }}" onclick="selectTmpl('{{ val }}',this)">
          <input type="radio" name="template" value="{{ val }}" {{ 'checked' if d.get('template','classic')==val }}>
          <div class="tmpl-thumb">{{ ico }}</div>
          <div style="font-size:.83rem;font-weight:500">{{ name }}</div>
        </div>
        {% endfor %}
      </div>
      <label style="font-weight:500;display:block;margin-bottom:.7rem">Choose Color Theme</label>
      <div class="swatches">
        {% for hex,name in [('#3B82F6','Blue'),('#22C55E','Green'),('#EF4444','Red'),('#A855F7','Purple'),('#1e293b','Black'),('#F97316','Orange')] %}
        <div class="swatch {{ 'sel' if d.get('color','#3B82F6')==hex else '' }}"
             style="background:{{ hex }}" title="{{ name }}"
             onclick="selectColor('{{ hex }}',this)"></div>
        {% endfor %}
      </div>
      <input type="hidden" name="color" id="color-val" value="{{ d.get('color','#3B82F6') }}">
      <div class="step-nav">
        <a href="/builder/step/5" class="btn btn-gray">← Back</a>
        <button class="btn btn-blue">Preview Resume →</button>
      </div>
    </form>
  </div>
</div>
<script>
function selectTmpl(val,el){document.querySelectorAll('.tmpl-opt').forEach(function(e){e.classList.remove('sel');e.querySelector('input').checked=false});el.classList.add('sel');el.querySelector('input').checked=true}
function selectColor(hex,el){document.querySelectorAll('.swatch').forEach(function(e){e.classList.remove('sel')});el.classList.add('sel');document.getElementById('color-val').value=hex}
</script>
"""), user=u, step=step, d=d)

    return redirect("/preview")

# ════════════════════════════════════════════════════════════════════════════
#  ROUTES — Preview, Save, Download, Delete
# ════════════════════════════════════════════════════════════════════════════

@app.route("/preview")
def preview():
    u = current_user()
    if not u: return redirect("/login")
    d = session.get("draft", {})
    p  = d.get("personal", {})
    ed = d.get("education", {})
    exps = d.get("experience", [])
    sk = d.get("skills", {})
    ex = d.get("extras", {})
    color = d.get("color", "#3B82F6")
    return render_template_string(BASE(title=""" — Preview""", content="""
<div class="preview-wrap">
  <div class="preview-actions">
    <a href="/builder/step/1" class="btn btn-outline">✏️ Edit</a>
    <a href="/save" class="btn btn-outline">💾 Save</a>
    <a href="/ats" class="btn btn-green">🎯 ATS Score</a>
    <a href="/download-preview" class="btn btn-blue">⬇ Download PDF</a>
  </div>
  <div class="resume-paper">
    <div style="border-bottom:3px solid {{ color }};padding-bottom:.9rem;margin-bottom:1rem">
      <div class="r-name" style="color:{{ color }}">{{ p.get('name','Your Name') }}</div>
      <div class="r-contacts">
        {% if p.get('phone') %}<span>📞 {{ p.phone }}</span>{% endif %}
        {% if p.get('email') %}<span>✉ {{ p.email }}</span>{% endif %}
        {% if p.get('city') %}<span>📍 {{ p.city }}{% if p.get('country') %}, {{ p.country }}{% endif %}</span>{% endif %}
        {% if p.get('linkedin') %}<span>🔗 {{ p.linkedin }}</span>{% endif %}
        {% if p.get('github') %}<span>💻 {{ p.github }}</span>{% endif %}
      </div>
    </div>
    {% if ex.get('objective') %}
    <div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Career Objective</div>
      <p style="font-size:.85rem;line-height:1.6;color:#374151">{{ ex.objective }}</p></div>
    {% endif %}
    <div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Personal Details</div>
      <table style="font-size:.83rem;width:100%">
        {% for lbl,key in [("Father","father"),("Mother","mother"),("DOB","dob"),("Gender","gender"),("Nationality","nationality"),("NID","nid"),("Address","current_address")] %}
        {% if p.get(key) %}<tr><td style="font-weight:500;width:130px;padding:2px 0;color:#374151">{{ lbl }}</td><td style="color:#475569;padding:2px 0">{{ p[key] }}</td></tr>{% endif %}
        {% endfor %}
      </table>
    </div>
    <div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Education</div>
      {% for lbl,key,flds in [('SSC','ssc',[('Institute','institute'),('Board','board'),('Year','year'),('GPA','gpa')]),
        ('HSC','hsc',[('Institute','institute'),('Board','board'),('Year','year'),('GPA','gpa')]),
        ("BSc / Bachelor's",'bsc',[('University','university'),('Dept','dept'),('Year','year'),('CGPA','cgpa')])] %}
      {% if ed.get(key) and ed[key].values()|list|select|list %}
      <div style="margin-bottom:.6rem">
        <div style="font-weight:600;font-size:.86rem">{{ lbl }}</div>
        <div style="font-size:.8rem;color:#64748b">{% for k,v in flds %}{% if ed[key].get(v) %}{{ k }}: <strong>{{ ed[key][v] }}</strong>  {% endif %}{% endfor %}</div>
      </div>{% endif %}{% endfor %}
      {% if ed.get('masters_enabled') and ed.get('masters') %}
      <div style="margin-bottom:.6rem">
        <div style="font-weight:600;font-size:.86rem">Master's Degree</div>
        <div style="font-size:.8rem;color:#64748b">University: <strong>{{ ed.masters.get('university','') }}</strong>  Dept: {{ ed.masters.get('dept','') }}  Year: {{ ed.masters.get('year','') }}</div>
      </div>{% endif %}
    </div>
    {% if exps %}
    <div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Work Experience</div>
      {% for e in exps %}
      <div style="margin-bottom:.7rem">
        <div style="font-weight:600;font-size:.88rem">{{ e.title }} — {{ e.company }}</div>
        <div style="font-size:.78rem;color:#64748b">{{ e.type }} | {{ e.start }} – {{ 'Present' if e.current else e.end }}</div>
        {% if e.desc %}<div style="font-size:.82rem;color:#374151;margin-top:.2rem">{{ e.desc }}</div>{% endif %}
      </div>{% endfor %}
    </div>{% endif %}
    {% if sk %}
    <div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Skills</div>
      {% if sk.get('technical') %}<div style="font-size:.83rem;margin-bottom:.25rem"><strong>Technical:</strong> {{ sk.technical }}</div>{% endif %}
      {% if sk.get('soft') %}<div style="font-size:.83rem;margin-bottom:.25rem"><strong>Soft Skills:</strong> {{ sk.soft }}</div>{% endif %}
      {% if sk.get('languages') %}<div style="font-size:.83rem"><strong>Languages:</strong> {{ sk.languages }}</div>{% endif %}
    </div>{% endif %}
    {% if ex.get('hobbies') %}<div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Hobbies & Interests</div><div style="font-size:.83rem">{{ ex.hobbies }}</div></div>{% endif %}
    {% if ex.get('awards') %}<div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Awards & Achievements</div><div style="font-size:.83rem;white-space:pre-line">{{ ex.awards }}</div></div>{% endif %}
    {% if ex.get('certifications') %}<div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">Certifications</div><div style="font-size:.83rem;white-space:pre-line">{{ ex.certifications }}</div></div>{% endif %}
    {% if ex.get('references') %}<div class="r-sec"><div class="r-sec-title" style="color:{{ color }}">References</div><div style="font-size:.83rem;white-space:pre-line">{{ ex.references }}</div></div>{% endif %}
  </div>
  <div style="text-align:center;margin:1.2rem 0"><a href="/dashboard" class="btn btn-gray">← Dashboard</a></div>
</div>
"""), user=u, p=p, ed=ed, exps=exps, sk=sk, ex=ex, color=color)


@app.route("/save")
def save():
    u = current_user()
    if not u: return redirect("/login")
    d = session.get("draft", {})
    c = db()
    data = (json.dumps(d.get("personal",{})), json.dumps(d.get("education",{})),
            json.dumps(d.get("experience",[])), json.dumps(d.get("skills",{})),
            json.dumps(d.get("extras",{})), d.get("template","classic"),
            d.get("color","#3B82F6"), d.get("title","My Resume"), datetime.now().isoformat()[:10])
    rid = session.get("rid")
    if rid:
        c.execute("UPDATE resumes SET personal=?,education=?,experience=?,skills=?,extras=?,template=?,color=?,title=?,updated_at=? WHERE id=? AND user_id=?", (*data, rid, u["id"]))
    else:
        cur = c.execute("INSERT INTO resumes (personal,education,experience,skills,extras,template,color,title,updated_at,user_id) VALUES (?,?,?,?,?,?,?,?,?,?)", (*data, u["id"]))
        session["rid"] = cur.lastrowid
    c.commit(); c.close()
    flash("Resume saved!", "success")
    return redirect("/preview")


@app.route("/download-preview")
def download_preview():
    u = current_user()
    if not u: return redirect("/login")
    buf = make_pdf(session.get("draft", {}))
    name = session.get("draft", {}).get("title", "Resume").replace(" ","_")
    return send_file(buf, as_attachment=True, download_name=f"{name}.pdf", mimetype="application/pdf")


@app.route("/download/<int:rid>")
def download(rid):
    u = current_user()
    if not u: return redirect("/login")
    c = db(); r = c.execute("SELECT * FROM resumes WHERE id=? AND user_id=?", (rid, u["id"])).fetchone(); c.close()
    if not r: flash("Not found.", "danger"); return redirect("/dashboard")
    draft = {"personal": json.loads(r["personal"] or "{}"), "education": json.loads(r["education"] or "{}"),
             "experience": json.loads(r["experience"] or "[]"), "skills": json.loads(r["skills"] or "{}"),
             "extras": json.loads(r["extras"] or "{}"), "template": r["template"], "color": r["color"], "title": r["title"]}
    buf = make_pdf(draft)
    return send_file(buf, as_attachment=True, download_name=f"{r['title'].replace(' ','_')}.pdf", mimetype="application/pdf")


@app.route("/delete/<int:rid>", methods=["POST"])
def delete_resume(rid):
    u = current_user()
    if not u: return redirect("/login")
    c = db(); c.execute("DELETE FROM resumes WHERE id=? AND user_id=?", (rid, u["id"])); c.commit(); c.close()
    flash("Resume deleted.", "info"); return redirect("/dashboard")


# ════════════════════════════════════════════════════════════════════════════
#  ROUTES — ATS Scorer
# ════════════════════════════════════════════════════════════════════════════

@app.route("/ats")
def ats():
    u = current_user()
    if not u: return redirect("/login")
    draft = session.get("draft", {})
    if not draft.get("personal"):
        flash("Please fill your resume first before checking ATS score.", "warning")
        return redirect("/builder/step/1")
    ats_data = run_ats(draft)
    return render_template_string(BASE(title=""" — ATS Score""", content="""
<div style="max-width:820px;margin:2rem auto;padding:0 1.5rem">

  <!-- Header card -->
  <div style="background:#fff;border-radius:16px;padding:2rem;border:1px solid #e2e8f0;margin-bottom:1.5rem;text-align:center">
    <div style="font-size:.85rem;color:#64748b;margin-bottom:.5rem;font-weight:500;text-transform:uppercase;letter-spacing:.05em">ATS Compatibility Score</div>
    <div style="font-size:5rem;font-weight:700;color:{{ ats.grade_color }};line-height:1">{{ ats.score }}</div>
    <div style="font-size:1rem;color:#64748b;margin-bottom:.5rem">out of 100</div>
    <div style="display:inline-block;background:{{ ats.grade_color }};color:#fff;font-size:1.3rem;font-weight:700;padding:.3rem 1.2rem;border-radius:8px;margin-bottom:1rem">Grade: {{ ats.grade }}</div>

    <!-- Score bar -->
    <div style="background:#e2e8f0;border-radius:999px;height:12px;overflow:hidden;max-width:400px;margin:0 auto .5rem">
      <div style="height:100%;width:{{ ats.score }}%;background:{{ ats.grade_color }};border-radius:999px;transition:width 1s"></div>
    </div>
    <div style="font-size:.82rem;color:#94a3b8">
      {% if ats.score >= 80 %}Your resume is ATS-friendly and well optimized! 🎉
      {% elif ats.score >= 60 %}Good resume — a few improvements will make it stronger.
      {% elif ats.score >= 40 %}Fair resume — follow the suggestions below to improve.
      {% else %}Your resume needs significant improvements before applying.{% endif %}
    </div>
  </div>

  <!-- Category breakdown -->
  <div style="background:#fff;border-radius:16px;padding:1.8rem;border:1px solid #e2e8f0;margin-bottom:1.5rem">
    <div style="font-size:1rem;font-weight:600;margin-bottom:1.2rem">📊 Category Breakdown</div>
    {% for cat, pts, mx, status, tips in ats.results %}
    <div style="margin-bottom:1rem">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.3rem">
        <div style="font-size:.88rem;font-weight:500;color:#1e293b">{{ cat }}</div>
        <div style="display:flex;align-items:center;gap:.6rem">
          <span style="font-size:.75rem;color:#64748b">{{ status }}</span>
          <span style="font-size:.85rem;font-weight:600;color:{% if pts==mx %}#22C55E{% elif pts >= mx*0.6 %}#3B82F6{% elif pts > 0 %}#F59E0B{% else %}#EF4444{% endif %}">{{ pts }}/{{ mx }}</span>
        </div>
      </div>
      <div style="background:#e2e8f0;border-radius:999px;height:7px;overflow:hidden">
        <div style="height:100%;width:{{ (pts/mx*100)|round }}%;background:{% if pts==mx %}#22C55E{% elif pts >= mx*0.6 %}#3B82F6{% elif pts > 0 %}#F59E0B{% else %}#EF4444{% endif %};border-radius:999px"></div>
      </div>
    </div>
    {% endfor %}
  </div>

  <!-- Tips -->
  {% if ats.tips %}
  <div style="background:#fff;border-radius:16px;padding:1.8rem;border:1px solid #e2e8f0;margin-bottom:1.5rem">
    <div style="font-size:1rem;font-weight:600;margin-bottom:1rem">💡 Top Suggestions to Improve</div>
    {% for tip in ats.tips %}
    <div style="display:flex;align-items:flex-start;gap:.7rem;margin-bottom:.75rem;padding:.7rem;background:#FEF3C7;border-radius:8px;border-left:3px solid #F59E0B">
      <span style="font-size:1rem;flex-shrink:0">⚡</span>
      <span style="font-size:.85rem;color:#374151">{{ tip }}</span>
    </div>
    {% endfor %}
  </div>
  {% else %}
  <div style="background:#DCFCE7;border-radius:16px;padding:1.5rem;border:1px solid #86EFAC;margin-bottom:1.5rem;text-align:center">
    <div style="font-size:1.5rem;margin-bottom:.5rem">🎉</div>
    <div style="font-weight:600;color:#166534">Your resume is fully optimized!</div>
    <div style="font-size:.85rem;color:#166534;margin-top:.3rem">No major issues found. You're ready to apply!</div>
  </div>
  {% endif %}

  <!-- Per-category detailed tips -->
  <div style="background:#fff;border-radius:16px;padding:1.8rem;border:1px solid #e2e8f0;margin-bottom:1.5rem">
    <div style="font-size:1rem;font-weight:600;margin-bottom:1rem">🔍 Detailed Feedback</div>
    {% for cat, pts, mx, status, tips in ats.results %}
    {% if tips %}
    <div style="margin-bottom:1rem">
      <div style="font-size:.85rem;font-weight:600;color:#1e293b;margin-bottom:.4rem">{{ cat }}</div>
      {% for tip in tips %}
      <div style="font-size:.82rem;color:#475569;padding:.3rem 0 .3rem .9rem;border-left:2px solid #e2e8f0">→ {{ tip }}</div>
      {% endfor %}
    </div>
    {% endif %}
    {% endfor %}
  </div>

  <div style="display:flex;gap:1rem;justify-content:center;flex-wrap:wrap;margin-bottom:2rem">
    <a href="/preview" class="btn btn-outline">← Back to Preview</a>
    <a href="/builder/step/1" class="btn btn-blue">✏️ Improve Resume</a>
    <a href="/download-preview" class="btn btn-green">⬇ Download PDF</a>
  </div>
</div>
"""), user=u, ats=ats_data)


# ════════════════════════════════════════════════════════════════════════════
#  START
# ════════════════════════════════════════════════════════════════════════════
if __name__ == "__main__":
    init_db()
    print("\n" + "="*52)
    print("  ResumeGen is running!")
    print("  Open your browser and go to:")
    print("  http://127.0.0.1:5000")
    print("="*52 + "\n")
    app.run(debug=True)