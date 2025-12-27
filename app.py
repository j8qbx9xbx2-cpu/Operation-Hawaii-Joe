import os
from datetime import date, datetime, timedelta

from flask import Flask, render_template, request, redirect, url_for
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func

app = Flask(__name__)

db_url = os.getenv("DATABASE_URL")


if db_url and db_url.startswith("postgres://"):
    db_url = db_url.replace("postgres://", "postgresql://", 1)


if db_url and db_url.startswith("postgresql://"):
    db_url = db_url.replace("postgresql://", "postgresql+psycopg://", 1)


app.config["SQLALCHEMY_DATABASE_URI"] = db_url or "sqlite:///fittrack.db"

app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


class DayLog(db.Model):
    __tablename__ = "day_logs"
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, unique=True, nullable=False, index=True)

    weight_am = db.Column(db.Float, nullable=True)
    waist_in = db.Column(db.Float, nullable=True)

    calories_total = db.Column(db.Integer, nullable=True)
    protein_g_total = db.Column(db.Integer, nullable=True)

    walk_done = db.Column(db.Boolean, default=False)
    lift_done = db.Column(db.Boolean, default=False)
    if_done = db.Column(db.Boolean, default=False)  # IF window 11a-7p adhered

    walking_miles = db.Column(db.Float, nullable=True)
    active_calories = db.Column(db.Integer, nullable=True)  # optional Apple Fitness ref
    rings_closed = db.Column(db.Boolean, default=False)

    cal_target = db.Column(db.Integer, default=2000)
    prot_target = db.Column(db.Integer, default=190)

    notes = db.Column(db.Text, nullable=True)



class Settings(db.Model):
    __tablename__ = "settings"
    id = db.Column(db.Integer, primary_key=True)
    start_weight = db.Column(db.Float, default=225.0)
    goal_weight = db.Column(db.Float, default=190.0)
    goal_date = db.Column(db.Date, nullable=True)  # default set in init

class SavedMeal(db.Model):
    __tablename__ = "saved_meals"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    calories = db.Column(db.Integer, nullable=True)
    protein_g = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Meal(db.Model):
    __tablename__ = "meals"
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, nullable=False, index=True)
    time = db.Column(db.String(10), nullable=True)  # e.g. "11:30"
    name = db.Column(db.String(120), nullable=False)
    calories = db.Column(db.Integer, nullable=True)
    protein_g = db.Column(db.Integer, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class WorkoutLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    day = db.Column(db.Date, index=True, nullable=False)
    workout_type = db.Column(db.String(80), nullable=False)
    minutes = db.Column(db.Integer, nullable=False, default=0)
    calories = db.Column(db.Integer, nullable=False, default=0)
    notes = db.Column(db.String(250), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


with app.app_context():
    db.create_all()

def get_or_create_day(d: date) -> DayLog:
    log = DayLog.query.filter_by(day=d).first()
    if not log:
        log = DayLog(day=d, cal_target=2000, prot_target=190)
        db.session.add(log)
        db.session.commit()
    return log


def recalc_totals(d: date):
    log = get_or_create_day(d)
    cals, prot = db.session.query(
        func.coalesce(func.sum(Meal.calories), 0),
        func.coalesce(func.sum(Meal.protein_g), 0),
    ).filter(Meal.day == d).first()
    log.calories_total = int(cals or 0)
    log.protein_g_total = int(prot or 0)
    db.session.commit()


def compliance_score(log: DayLog) -> int:
    # 0-5: calories <= target, protein >= target, walk, lift, rings closed
    score = 0
    score += 1 if (log.calories_total is not None and log.cal_target is not None and log.calories_total <= log.cal_target) else 0
    score += 1 if (log.protein_g_total is not None and log.prot_target is not None and log.protein_g_total >= log.prot_target) else 0
    score += 1 if log.walk_done else 0
    score += 1 if log.lift_done else 0
    score += 1 if log.rings_closed else 0
    return score


def get_settings() -> Settings:
    s = Settings.query.first()
    if not s:
        s = Settings(start_weight=225.0, goal_weight=190.0)
        # default goal date: June 1 this year; if past, next year
        today = date.today()
        gd = date(today.year, 6, 1)
        if gd < today:
            gd = date(today.year + 1, 6, 1)
        s.goal_date = gd
        db.session.add(s)
        db.session.commit()
    return s


@app.route("/")
def dashboard():
    today = date.today()
    log = get_or_create_day(today)
    recalc_totals(today)
    log = DayLog.query.filter_by(day=today).first()
    meals = Meal.query.filter_by(day=today).order_by(Meal.created_at.desc()).all()

    cal_delta = (log.calories_total or 0) - (log.cal_target or 0)
    prot_delta = (log.protein_g_total or 0) - (log.prot_target or 0)

    start = today - timedelta(days=30)
    last_logs = DayLog.query.filter(DayLog.day >= start).order_by(DayLog.day.asc()).all()
    labels = [l.day.strftime("%b %d") for l in last_logs]
    weights = [l.weight_am for l in last_logs]
    compliance = [compliance_score(l) for l in last_logs]

    # goal line (simple 225 -> 190 over the 30d window; editable later)
    goal_start_wt = 225.0
    goal_end_wt = 190.0
    n = max(len(labels), 2)
    goal_weights = [round(goal_start_wt + (i/(n-1))*(goal_end_wt-goal_start_wt), 2) for i in range(n)]

    
    s = get_settings()
    # Pace calculation (based on today vs goal date)
    days_total = max((s.goal_date - today).days, 1)
    days_done = 0
    # Determine start weight for pace: prefer earliest logged weight, else settings.start_weight
    first = DayLog.query.filter(DayLog.weight_am.isnot(None)).order_by(DayLog.day.asc()).first()
    start_wt = first.weight_am if first and first.weight_am is not None else (s.start_weight or 225.0)
    goal_wt = s.goal_weight or 190.0
    # Current weight: today if available else latest
    latest = DayLog.query.filter(DayLog.weight_am.isnot(None)).order_by(DayLog.day.desc()).first()
    current_wt = latest.weight_am if latest and latest.weight_am is not None else start_wt

    start_day = first.day if first else today
    days_done = max((today - start_day).days, 0)
    expected = start_wt + (min(days_done, days_total) / days_total) * (goal_wt - start_wt)
    # Pace percent: 100 = exactly on pace, >100 ahead, <100 behind
    denom = (expected - start_wt) if expected != start_wt else -1.0
    # use weight loss direction
    target_loss_so_far = (start_wt - expected)
    actual_loss_so_far = (start_wt - current_wt)
    pace_pct = 0
    if target_loss_so_far > 0:
        pace_pct = round((actual_loss_so_far / target_loss_so_far) * 100, 1)
    else:
        pace_pct = 100.0

    quick_add = [
        {"label": "Protein Shake", "name": "Protein shake", "calories": 200, "protein_g": 30},
        {"label": "Greek Yogurt Bowl", "name": "Greek yogurt + berries", "calories": 300, "protein_g": 35},
        {"label": "Chicken Bowl", "name": "Chicken + veggies + rice (½ cup)", "calories": 500, "protein_g": 50},
        {"label": "Tuna Pack", "name": "Tuna pack + apple", "calories": 250, "protein_g": 30},
    ]

    return render_template(
            "dashboard.html",
            today=today,
            log=log,
            meals=meals,
            cal_delta=cal_delta,
            prot_delta=prot_delta,
            score=compliance_score(log),
            labels=labels,
            weights=weights,
            compliance=compliance,
            goal_weights=goal_weights,
            pace_pct=pace_pct,
            expected_weight=round(expected,1),
            current_weight=round(current_wt,1),
            goal_date=s.goal_date,
            start_weight=round(start_wt,1),
            goal_weight=round(goal_wt,1),
            quick_add=quick_add,
        )


@app.route("/day/<dstr>")
def day_view(dstr):
    d = datetime.strptime(dstr, "%Y-%m-%d").date()
    log = get_or_create_day(d)
    recalc_totals(d)
    log = DayLog.query.filter_by(day=d).first()
    meals = Meal.query.filter_by(day=d).order_by(Meal.created_at.desc()).all()
    return render_template("day.html", day=d, log=log, meals=meals, score=compliance_score(log))


@app.route("/day/update", methods=["POST"])
def day_update():
    d = datetime.strptime(request.form["day"], "%Y-%m-%d").date()
    log = get_or_create_day(d)

    def to_float(v):
        v = (v or "").strip()
        return float(v) if v else None

    def to_int(v):
        v = (v or "").strip()
        return int(v) if v else None

    log.weight_am = to_float(request.form.get("weight_am"))
    log.waist_in = to_float(request.form.get("waist_in"))
    log.walking_miles = to_float(request.form.get("walking_miles"))
    log.active_calories = to_int(request.form.get("active_calories"))

    log.walk_done = request.form.get("walk_done") == "on"
    log.lift_done = request.form.get("lift_done") == "on"
    log.if_done = request.form.get("if_done") == "on"
    log.rings_closed = request.form.get("rings_closed") == "on"

    log.cal_target = to_int(request.form.get("cal_target")) or log.cal_target
    log.prot_target = to_int(request.form.get("prot_target")) or log.prot_target

    log.notes = request.form.get("notes") or None

    db.session.commit()
    recalc_totals(d)
    return redirect(url_for("day_view", dstr=d.isoformat()))


@app.route("/meal/add", methods=["POST"])
def meal_add():
    d = datetime.strptime(request.form["day"], "%Y-%m-%d").date()
    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("day_view", dstr=d.isoformat()))


@app.route("/meal/quick_add", methods=["POST"])
def meal_quick_add():
    d = datetime.strptime(request.form["day"], "%Y-%m-%d").date()

    def to_int(v):
        v = (v or "").strip()
        return int(v) if v else None

    m = Meal(
        day=d,
        time=(request.form.get("time") or "").strip() or None,
        name=(request.form.get("name") or "Quick add").strip(),
        calories=to_int(request.form.get("calories")),
        protein_g=to_int(request.form.get("protein_g")),
    )
    db.session.add(m)
    db.session.commit()
    recalc_totals(d)
    return redirect(url_for("day_view", dstr=d.isoformat()))

    def to_int(v):
        v = (v or "").strip()
        return int(v) if v else None

    m = Meal(
        day=d,
        time=(request.form.get("time") or "").strip() or None,
        name=name,
        calories=to_int(request.form.get("calories")),
        protein_g=to_int(request.form.get("protein_g")),
    )
    db.session.add(m)
    db.session.commit()
    recalc_totals(d)
    return redirect(url_for("day_view", dstr=d.isoformat()))


@app.route("/meal/delete/<int:mid>", methods=["POST"])
def meal_delete(mid):
    m = Meal.query.get_or_404(mid)
    d = m.day
    db.session.delete(m)
    db.session.commit()
    recalc_totals(d)
    return redirect(url_for("day_view", dstr=d.isoformat()))



@app.route("/weekly")
def weekly():
    today = date.today()
    start = today - timedelta(days=90)
    logs = DayLog.query.filter(DayLog.day >= start).order_by(DayLog.day.asc()).all()

    # build weekly buckets (Mon-Sun)
    buckets = {}
    for l in logs:
        wk_start = l.day - timedelta(days=l.day.weekday())
        buckets.setdefault(wk_start, []).append(l)

    weeks = []
    for wk in sorted(buckets.keys(), reverse=True)[:16]:
        items = buckets[wk]
        wts = [x.weight_am for x in items if x.weight_am is not None]
        waists = [x.waist_in for x in items if x.waist_in is not None]
        cals = [x.calories_total for x in items if x.calories_total is not None]
        prots = [x.protein_g_total for x in items if x.protein_g_total is not None]
        comp = [compliance_score(x) for x in items]
        miles = [x.walking_miles for x in items if x.walking_miles is not None]
        active = [x.active_calories for x in items if x.active_calories is not None]
        rings = [1 for x in items if x.rings_closed]
        weeks.append({
            "week_start": wk,
            "avg_weight": round(sum(wts)/len(wts), 1) if wts else None,
            "avg_waist": round(sum(waists)/len(waists), 1) if waists else None,
            "avg_cals": round(sum(cals)/len(cals)) if cals else None,
            "avg_prot": round(sum(prots)/len(prots)) if prots else None,
            "avg_miles": round(sum(miles)/len(miles), 2) if miles else None,
            "avg_active": round(sum(active)/len(active)) if active else None,
            "rings_pct": round((sum(rings)/len(items))*100, 0) if items else None,
            "avg_comp": round(sum(comp)/len(comp), 2) if comp else None,
        })

    # waist trend chart (last 60 days)
    start2 = today - timedelta(days=60)
    logs2 = DayLog.query.filter(DayLog.day >= start2).order_by(DayLog.day.asc()).all()
    labels = [l.day.strftime("%b %d") for l in logs2]
    waist = [l.waist_in for l in logs2]
    miles = [l.walking_miles for l in logs2]

    return render_template("weekly.html", weeks=weeks, labels=labels, waist=waist, miles=miles)



@app.route("/settings")
def settings():
    s = get_settings()
    return render_template("settings.html", s=s)

@app.route("/settings/update", methods=["POST"])
def settings_update():
    s = get_settings()
    def to_float(v):
        v = (v or "").strip()
        return float(v) if v else None
    def to_date(v):
        v = (v or "").strip()
        return datetime.strptime(v, "%Y-%m-%d").date() if v else None

    s.start_weight = to_float(request.form.get("start_weight")) or s.start_weight
    s.goal_weight = to_float(request.form.get("goal_weight")) or s.goal_weight
    gd = to_date(request.form.get("goal_date"))
    if gd:
        s.goal_date = gd
    db.session.commit()
    return redirect(url_for("settings"))




@app.route("/saved")
def saved_meals():
    meals = SavedMeal.query.order_by(SavedMeal.created_at.desc()).all()
    return render_template("saved.html", meals=meals, today=date.today())

@app.route("/saved/add", methods=["POST"])
def saved_add():
    name = (request.form.get("name") or "").strip()
    if not name:
        return redirect(url_for("saved_meals"))

    def to_int(v):
        v = (v or "").strip()
        return int(v) if v else None

    sm = SavedMeal(
        name=name,
        calories=to_int(request.form.get("calories")),
        protein_g=to_int(request.form.get("protein_g")),
    )
    db.session.add(sm)
    db.session.commit()
    return redirect(url_for("saved_meals"))

@app.route("/saved/delete/<int:sid>", methods=["POST"])
def saved_delete(sid):
    sm = SavedMeal.query.get_or_404(sid)
    db.session.delete(sm)
    db.session.commit()
    return redirect(url_for("saved_meals"))

@app.route("/saved/log/<int:sid>", methods=["POST"])
def saved_log(sid):
    sm = SavedMeal.query.get_or_404(sid)
    d = datetime.strptime(request.form.get("day") or date.today().isoformat(), "%Y-%m-%d").date()
    m = Meal(
        day=d,
        time=(request.form.get("time") or "").strip() or None,
        name=sm.name,
        calories=sm.calories,
        protein_g=sm.protein_g,
    )
    db.session.add(m)
    db.session.commit()
    recalc_totals(d)
    return redirect(url_for("day_view", dstr=d.isoformat()))


@app.route("/meals")
def meal_suggestions():
    today = date.today()
    log = get_or_create_day(today)
    recalc_totals(today)
    log = DayLog.query.filter_by(day=today).first()

    # Remaining targets (simple)
    cal_rem = (log.cal_target or 0) - (log.calories_total or 0)
    prot_rem = (log.prot_target or 0) - (log.protein_g_total or 0)

    # Meal ideas: (name, calories, protein)
    ideas = [
        ("Whey shake + water", 180, 30),
        ("0% Greek yogurt + berries", 280, 35),
        ("Cottage cheese bowl", 260, 28),
        ("Chicken salad (no croutons)", 450, 50),
        ("Turkey lettuce wrap + side salad", 420, 40),
        ("Tuna packet + apple", 250, 30),
        ("Salmon + veggies", 520, 45),
        ("Egg-white scramble + veggies", 350, 35),
        ("Lean steak + veggies", 600, 55),
        ("Protein oatmeal (½ cup oats + whey)", 420, 35),
    ]

    # Filter to those that fit remaining calories (allow a little over if very low remaining)
    if cal_rem is None:
        filtered = ideas
    else:
        threshold = max(cal_rem, 250)  # if you're low, still show small options
        filtered = [i for i in ideas if i[1] <= threshold]

    # Sort by protein density, then calories
    filtered.sort(key=lambda x: (-(x[2]/max(x[1],1)), x[1]))

    # "Build a plate" suggestions by remaining calories
    plate = []
    if cal_rem >= 650:
        plate = [
            ("Big protein plate", "8–10 oz chicken/salmon/lean beef + 2 cups veggies + ½ cup rice/potato"),
            ("Restaurant order", "double protein + veggies; sauces on side; skip bread/chips"),
        ]
    elif cal_rem >= 400:
        plate = [
            ("Medium plate", "6–8 oz protein + veggies; optional fruit"),
            ("Snack-proof", "finish with Greek yogurt or shake if protein is short"),
        ]
    else:
        plate = [
            ("Close-out snack", "shake OR Greek yogurt OR tuna packet"),
            ("If hungry", "add veggies/salad (very low calorie)"),
        ]

    # Time window reminder
    window = "11:00 am – 7:00 pm"
    return render_template(
        "meals.html",
        log=log,
        cal_rem=cal_rem,
        prot_rem=prot_rem,
        ideas=filtered[:12],
        plate=plate,
        window=window,
        today=today,
    )


@app.route("/guides")
def guides():
    restaurant = [
        ("Steakhouse", "Sirloin, veggies, salad", "Bread, mashed potatoes, dessert"),
        ("Mexican", "Fajita bowl, no tortilla", "Chips, tacos, margaritas"),
        ("Italian", "Grilled chicken, vegetables", "Pasta, bread, creamy sauces"),
        ("Fast Food", "Grilled chicken salad", "Burgers, fries"),
        ("Asian", "Steamed protein + veggies", "Fried rice, noodles, sugary sauces"),
    ]
    vacation_rules = [
        "Protein at every meal (40g+)",
        "Walk daily (30–60 min)",
        "One indulgence per day max",
        "Zero liquid calories",
        "Resume IF 11am–7pm immediately after trip",
    ]
    maintenance = [
        ("Calories", "2300–2400/day"),
        ("Protein", "≥170g/day"),
        ("IF", "Optional, ~5 days/week"),
        ("Lifting", "20 min, 4–5x/week"),
        ("Walking", "Daily"),
        ("Rebound Rule", "If +4 lbs over baseline, tighten 5 days"),
    ]
    return render_template("guides.html", restaurant=restaurant, vacation_rules=vacation_rules, maintenance=maintenance)


@app.route("/reset")
def reset_page():
    return render_template("reset.html")


@app.route("/reset/activate", methods=["POST"])
def reset_activate():
    today = date.today()
    banner = "EMERGENCY RESET (14 days): 1650 cals / 200g protein / carbs ≤75g / strict IF 11-7 / no alcohol."
    for i in range(14):
        d = today + timedelta(days=i)
        log = get_or_create_day(d)
        log.cal_target = 1650
        log.prot_target = 200
        log.notes = (banner + ("\n\n" + log.notes if log.notes else ""))[:4000]
    db.session.commit()
    return redirect(url_for("dashboard"))



@app.route("/manifest.json")
def manifest():
    return app.send_static_file("manifest.json")

@app.route("/sw.js")
def sw():
    resp = app.send_static_file("sw.js")
    resp.headers["Content-Type"] = "application/javascript"
    return resp


@app.route("/export.csv")
def export_csv():
    rows = DayLog.query.order_by(DayLog.day.asc()).all()
    lines = ["date,weight_am,waist_in,calories_total,protein_g_total,cal_target,prot_target,walk_done,lift_done,if_done,notes"]
    for r in rows:
        def b(x): return "1" if x else "0"
        notes = (r.notes or "").replace("\n", "\\n").replace(",", ";")
        lines.append(
            f"{r.day.isoformat()},{r.weight_am or ''},{r.waist_in or ''},{r.calories_total or ''},{r.protein_g_total or ''},"
            f"{r.cal_target or ''},{r.prot_target or ''},{b(r.walk_done)},{b(r.lift_done)},{b(r.if_done)},{notes}"
        )
    return app.response_class("\n".join(lines), mimetype="text/csv")

@app.route("/workouts", methods=["GET", "POST"])
def workouts():
    today = date.today()

    if request.method == "POST":
        wtype = (request.form.get("workout_type") or "").strip()
        minutes = int(request.form.get("minutes") or 0)
        calories = int(request.form.get("calories") or 0)
        notes = (request.form.get("notes") or "").strip()[:250]

        if wtype:
            w = WorkoutLog(day=today, workout_type=wtype, minutes=minutes, calories=calories, notes=notes)
            db.session.add(w)
            db.session.commit()

        return redirect(url_for("workouts"))

    items = WorkoutLog.query.filter_by(day=today).order_by(WorkoutLog.created_at.desc()).all()
    total_minutes = sum(x.minutes for x in items)
    total_calories = sum(x.calories for x in items)

    return render_template(
        "workouts.html",
        today=today,
        items=items,
        total_minutes=total_minutes,
        total_calories=total_calories,
    )



if __name__ == "__main__":
    app.run(debug=True)
