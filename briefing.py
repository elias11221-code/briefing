"""Builds Elias's Morning Briefing.

Runs every morning on GitHub Actions:
  1. pulls sleep, resting HR, Body Battery, stress and activities from Garmin Connect
  2. pulls wind / wave / current forecast for Vilanova i la Geltrú from Open-Meteo
  3. computes a recovery score vs. the personal baseline
  4. writes docs/data.enc.json, encrypted with DASHBOARD_PASSPHRASE (AES-GCM)

Never invents numbers: anything that fails becomes null and shows "Unavailable".
Nothing personal is printed to the (public) Actions log.
"""
import base64, json, os, statistics, sys, datetime as dt
from zoneinfo import ZoneInfo

import requests

TZ = ZoneInfo("Europe/Berlin")
LAT, LON = 41.21, 1.73          # Club Nàutic Vilanova
BASELINE_DAYS = 28
HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------- Garmin ----
def garmin_client():
    from garminconnect import Garmin
    tokens = os.environ.get("GARMIN_TOKENS", "").strip()
    if not tokens:
        raise RuntimeError("GARMIN_TOKENS secret missing")
    store = os.path.expanduser("~/.garminconnect")
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, "garmin_tokens.json"), "w") as f:
        f.write(tokens)
    g = Garmin()
    g.login(store)
    return g


def fetch_garmin(g, today):
    """Returns raw per-day dicts. Each call is wrapped so one failing day never kills the run."""
    days = [today - dt.timedelta(days=i) for i in range(BASELINE_DAYS + 1)]
    stats, sleep = {}, {}
    for d in days:
        ds = d.isoformat()
        try:
            s = g.get_stats(ds) or {}
            stats[ds] = {
                "rhr": s.get("restingHeartRate"),
                "stress": s.get("averageStressLevel"),
                "bb_high": s.get("bodyBatteryHighestValue"),
                "bb_charged": s.get("bodyBatteryChargedValue"),
                "bb_drained": s.get("bodyBatteryDrainedValue"),
                "bb_now": s.get("bodyBatteryMostRecentValue"),
            }
        except Exception:
            stats[ds] = {}
        try:
            sl = (g.get_sleep_data(ds) or {}).get("dailySleepDTO") or {}
            sleep[ds] = {
                "sec": sl.get("sleepTimeSeconds"),
                "start": sl.get("sleepStartTimestampGMT"),
                "end": sl.get("sleepEndTimestampGMT"),
                "awake": sl.get("awakeSleepSeconds"),
            }
        except Exception:
            sleep[ds] = {}
    try:
        acts = g.get_activities_by_date((today - dt.timedelta(days=7)).isoformat(),
                                        (today - dt.timedelta(days=1)).isoformat()) or []
        activities = [{"type": (a.get("activityType") or {}).get("typeKey"),
                       "sec": a.get("duration") or 0,
                       "m": a.get("distance") or 0} for a in acts]
    except Exception:
        activities = None
    return {"stats": stats, "sleep": sleep, "activities": activities}


# --------------------------------------------------------------- weather ----
def fetch_conditions(today):
    out = {"wind": None, "marine": None}
    try:
        r = requests.get("https://api.open-meteo.com/v1/forecast", timeout=30, params={
            "latitude": LAT, "longitude": LON, "timezone": "Europe/Berlin", "wind_speed_unit": "kn",
            "hourly": "wind_speed_10m,wind_gusts_10m,wind_direction_10m,temperature_2m,precipitation_probability",
            "start_date": today.isoformat(), "end_date": today.isoformat()})
        r.raise_for_status(); out["wind"] = r.json()["hourly"]
    except Exception:
        pass
    try:
        r = requests.get("https://marine-api.open-meteo.com/v1/marine", timeout=30, params={
            "latitude": 41.20, "longitude": 1.74, "timezone": "Europe/Berlin",
            "hourly": "wave_height,wave_period,wave_direction,sea_surface_temperature,ocean_current_velocity,ocean_current_direction",
            "start_date": today.isoformat(), "end_date": today.isoformat()})
        r.raise_for_status(); out["marine"] = r.json()["hourly"]
    except Exception:
        pass
    return out


# ----------------------------------------------------------------- logic ----
def clamp(x): return max(0, min(100, x))
def med(vals):
    vals = [v for v in vals if v is not None]
    return statistics.median(vals) if vals else None
def hm(h): return f"{int(h)}h {round((h - int(h)) * 60):02d}m"
def local_hhmm(ms): return dt.datetime.fromtimestamp(ms / 1000, TZ).strftime("%H:%M") if ms else None
COMPASS = "N NNE NE ENE E ESE SE SSE S SSW SW WSW W WNW NW NNW".split()
def compass(d): return COMPASS[round(d / 22.5) % 16]


def build(raw, cond, today, now, schedule):
    ds = today.isoformat()
    yday = (today - dt.timedelta(days=1)).isoformat()
    prev = [(today - dt.timedelta(days=i)).isoformat() for i in range(1, BASELINE_DAYS + 1)]
    st, sl = raw["stats"] if raw else {}, raw["sleep"] if raw else {}
    hours = lambda d: (sl.get(d, {}).get("sec") or 0) / 3600 or None

    s_today = hours(ds); s_base = med([hours(d) for d in prev])
    rhr = st.get(ds, {}).get("rhr"); rhr_base = med([st.get(d, {}).get("rhr") for d in prev])
    bb = st.get(ds, {}).get("bb_high"); bb_base = med([st.get(d, {}).get("bb_high") for d in prev])
    stress = st.get(yday, {}).get("stress"); stress_base = med([st.get(d, {}).get("stress") for d in prev[1:]])

    comps = []
    def add(key, label, val, base, score_fn, w, better, unit, disp, bdisp):
        if val is None or base is None: return
        comps.append({"key": key, "label": label, "value": val, "baseline": base, "score": round(clamp(score_fn())),
                      "weight": w, "better": better, "unit": unit, "display": disp, "baseline_display": bdisp})
    add("sleep", "Sleep", s_today, s_base, lambda: 50 + 30 * (s_today - s_base), 35, "higher", "h",
        s_today and hm(s_today), s_base and hm(s_base))
    add("rhr", "Resting HR", rhr, rhr_base, lambda: 50 - 10 * (rhr - rhr_base), 30, "lower", "bpm",
        str(rhr), f"{rhr_base:g}" if rhr_base else None)
    add("bb", "Body Battery peak", bb, bb_base, lambda: 50 + 1.5 * (bb - bb_base), 25, "higher", "",
        str(bb), f"{bb_base:g}" if bb_base else None)
    add("stress", "Stress (yesterday)", stress, stress_base, lambda: 50 - 2 * (stress - stress_base), 10, "lower", "",
        str(stress), f"{stress_base:g}" if stress_base else None)
    wsum = sum(c["weight"] for c in comps)
    score = round(sum(c["score"] * c["weight"] for c in comps) / wsum) if wsum else None
    n_base = sum(1 for d in prev if st.get(d, {}).get("rhr") is not None)

    last7 = [(today - dt.timedelta(days=i)) for i in range(6, -1, -1)]
    trend = {"labels": [d.strftime("%a") for d in last7],
             "sleep": [round(hours(d.isoformat()), 2) if hours(d.isoformat()) else None for d in last7],
             "rhr": [st.get(d.isoformat(), {}).get("rhr") for d in last7]}

    acts = raw["activities"] if raw else None
    load7 = None
    if acts is not None:
        sail = [a for a in acts if (a["type"] or "").startswith("sailing")]
        load7 = {"sailing_sessions": len(sail), "sailing_hours": round(sum(a["sec"] for a in sail) / 3600, 1),
                 "other_sessions": len(acts) - len(sail),
                 "run_km": round(sum(a["m"] for a in acts if a["type"] == "running") / 1000, 1)}

    # ---- conditions
    wd = today.strftime("%A")
    win = schedule["sail_windows"].get(wd)
    lo, hi = (win or ["10:00", "18:00"])
    h_from = f"{int(lo[:2]) - 1:02d}:00"; h_to = f"{int(hi[:2]) + 1:02d}:00"
    hrs = []
    W, M = cond.get("wind"), cond.get("marine")
    if W:
        for i, t in enumerate(W["time"]):
            hh = t[11:16]
            if h_from <= hh <= h_to:
                row = {"t": hh, "kn": W["wind_speed_10m"][i], "gust": W["wind_gusts_10m"][i],
                       "dir": W["wind_direction_10m"][i], "temp": W["temperature_2m"][i],
                       "rain": W["precipitation_probability"][i]}
                if M and t in M["time"]:
                    j = M["time"].index(t)
                    row.update(wave=M["wave_height"][j], period=M["wave_period"][j], wdir=M["wave_direction"][j],
                               sst=M["sea_surface_temperature"][j], cur=M["ocean_current_velocity"][j],
                               cdir=M["ocean_current_direction"][j])
                hrs.append(row)
    sess = [h for h in hrs if lo <= h["t"] < hi and h["kn"] is not None]
    def avg(k):
        v = [h.get(k) for h in sess if h.get(k) is not None]; return sum(v) / len(v) if v else None
    kn, gust, wave = avg("kn"), max([h["gust"] for h in sess if h.get("gust") is not None], default=None), avg("wave")
    waves = (f"{wave:.1f} m · {avg('period'):.0f} s from {compass(avg('wdir'))} · sea {avg('sst'):.0f}°C"
             if wave is not None else None)
    cur = avg("cur")
    current = (f"{cur / 1.852:.1f} kn setting {compass(avg('cdir'))} (tidal range here is only a few cm)"
               if cur is not None else None)
    if kn is None: summary = "No forecast for the session window"
    else:
        feel = ("drifting, very light" if kn < 5 else "light" if kn < 9 else "moderate" if kn < 14
                else "fresh" if kn < 19 else "strong")
        summary = f"{feel.capitalize()} breeze" + (f", gust factor {gust/kn:.1f}x" if gust and kn else "")

    # ---- today's call (rule-based)
    tone = None if score is None else "green" if score >= 67 else "yellow" if score >= 34 else "red"
    verdict = {"green": "Go hard", "yellow": "Train as planned", "red": "Keep it controlled", None: "No recovery data"}[tone]
    bits = []
    if score is None:
        bits.append("Garmin data could not be loaded this morning, so there is no recovery score.")
    else:
        for c in comps:
            d = c["value"] - c["baseline"]
            good = d > 0 if c["better"] == "higher" else d < 0
            if c["key"] == "rhr" and abs(d) >= 2: bits.append(f"Resting HR {abs(d):.0f} bpm {'under' if d < 0 else 'above'} baseline.")
            if c["key"] == "sleep" and abs(d) >= 0.5: bits.append(f"Sleep {abs(d)*60:.0f} min {'longer' if d > 0 else 'shorter'} than usual.")
            if c["key"] == "bb" and abs(d) >= 8: bits.append(f"Body Battery peak {'high' if good else 'low'} at {c['value']}.")
        if not bits: bits.append("All recovery markers are close to your normal.")
    if win is None:
        bits.append("No sailing today — use it to recharge.")
    elif kn is not None:
        if kn < 6: bits.append("Very light air: patience, weight placement and flat-water boat speed.")
        elif kn < 12: bits.append("Good conditions for technique work — boat handling, tacks, starts.")
        elif kn < 18: bits.append("Solid breeze: hiking and power sailing, keep an eye on fatigue late in the session.")
        else: bits.append("Strong wind: prioritise safety and control; shorten the session if recovery is low.")
    temp = avg("temp")
    if s_today is not None and s_base is not None and s_today < s_base - 0.5:
        tip = "Short night: aim to be in bed 45 min earlier tonight."
    elif temp is not None and temp >= 25:
        tip = f"{temp:.0f} °C: drink ~0.5–0.75 L per hour on the water and use sunscreen."
    else:
        tip = "Hydrate before rigging and keep a snack in the boat."

    day_sched = [{"time": e[0], "end": e[1], "title": e[2], "kind": e[3], "main": len(e) > 4} for e in schedule.get(wd, [])]
    bb_today = st.get(ds, {})
    return {
        "date": ds, "weekday": wd, "generated": now.isoformat(timespec="minutes"),
        "recovery": {"score": score, "baseline_days": n_base, "components": comps},
        "sleep": {"hours": s_today, "display": s_today and hm(s_today),
                  "bed": local_hhmm(sl.get(ds, {}).get("start")), "wake": local_hhmm(sl.get(ds, {}).get("end")),
                  "need_hours": s_base,
                  "note": "Deep-sleep staging from this watch is unreliable, so stages are not scored."},
        "body_battery": {"peak": bb_today.get("bb_high"), "now": bb_today.get("bb_now"),
                         "charged": bb_today.get("bb_charged"), "drained": bb_today.get("bb_drained")},
        "rhr": {"today": rhr, "baseline": rhr_base},
        "trend7": trend, "load7": load7, "schedule": day_sched,
        "sailing": {"spot": "Club Nàutic Vilanova", "window": f"{lo}–{hi}" if win else "No sailing today",
                    "win": win, "summary": summary, "hours": hrs, "waves": waves, "current": current,
                    "unavailable_note": "" if (waves and current) else "Missing values were not available from the forecast.",
                    "sources": ["Open-Meteo forecast & marine"]},
        "call": {"verdict": verdict, "tone": tone, "text": " ".join(bits), "tip": tip},
    }


# ---------------------------------------------------------------- crypto ----
def encrypt(obj, passphrase):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    from cryptography.hazmat.primitives import hashes
    salt, iv = os.urandom(16), os.urandom(12)
    key = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=250_000).derive(passphrase.encode())
    ct = AESGCM(key).encrypt(iv, json.dumps(obj, ensure_ascii=False).encode(), None)
    b = lambda x: base64.b64encode(x).decode()
    return {"v": 1, "iter": 250_000, "salt": b(salt), "iv": b(iv), "ct": b(ct)}


def main():
    passphrase = os.environ.get("DASHBOARD_PASSPHRASE", "")
    if len(passphrase) < 8:
        sys.exit("DASHBOARD_PASSPHRASE secret missing or shorter than 8 characters")
    now = dt.datetime.now(TZ); today = now.date()
    schedule = json.load(open(os.path.join(HERE, "schedule.json"), encoding="utf-8"))
    try:
        raw = fetch_garmin(garmin_client(), today); print("Garmin: ok")
    except Exception as e:
        raw = None; print("Garmin: FAILED (" + type(e).__name__ + ")")
    cond = fetch_conditions(today)
    print("Wind:", "ok" if cond["wind"] else "failed", "| Marine:", "ok" if cond["marine"] else "failed")
    data = build(raw, cond, today, now, schedule)
    with open(os.path.join(HERE, "docs", "data.enc.json"), "w") as f:
        json.dump(encrypt(data, passphrase), f)
    print("Wrote docs/data.enc.json")


if __name__ == "__main__":
    main()
