#!/usr/bin/env python3
"""
Market Hunter Tennis – Lunedì Edition (finestra 17-19 UTC)
"""

import os, json, csv, logging, requests, sys
from datetime import datetime, date, timedelta

RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

PRE_CRASH_THRESHOLD_PERCENT = 15
FULL_CRASH_THRESHOLD_PERCENT = 25
MAX_MINUTES_CRASH_WINDOW = 10
MIN_STARTING_ODD = 1.80
MAX_CRASH_ODD = 1.80
QUOTA_MINIMA_DOPO_CRASH = 1.30
HOURS_BEFORE_KICKOFF = 2

TARGET_WEEKDAY = 0   # lunedì

def is_monitoring_window():
    now = datetime.utcnow()
    if now.weekday() != TARGET_WEEKDAY:
        return False
    if not (12 <= now.hour <= 20):
        return False
    return True

def send_telegram(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except Exception as e:
        logging.error(f"Telegram error: {e}")

def load_json(filename, default=None):
    try:
        with open(filename) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}

def save_json(filename, data):
    with open(filename, "w") as f:
        json.dump(data, f, indent=2)

def fetch_events():
    url = "https://odds-feed.p.rapidapi.com/api/v1/events"
    headers = {
        "x-rapidapi-key": RAPIDAPI_KEY,
        "x-rapidapi-host": "odds-feed.p.rapidapi.com"
    }
    params = {
        "sport_id": 2,          # Tennis
        "status": "SCHEDULED",
        "page": 0
    }
    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        if resp.status_code != 200:
            logging.error(f"HTTP {resp.status_code}: {resp.text}")
            return []
        data = resp.json()
        events = data.get("data", [])
        filtered = []
        for ev in events:
            tournament_name = ev.get("tournament", {}).get("name", "")
            if "itf" in tournament_name.lower() or "challenger" in tournament_name.lower():
                home_team = ev["team_home"]["name"]
                away_team = ev["team_away"]["name"]
                commence_time = ev.get("start_at")
                odd_home = ev.get("main_outcome_0")
                odd_away = ev.get("main_outcome_1")
                if odd_home is None or odd_away is None:
                    continue
                volume_home = ev.get("main_volume_1", 0)
                volume_away = ev.get("main_volume_2", 0)
                filtered.append({
                    "fixture_id": ev["id"],
                    "home": home_team,
                    "away": away_team,
                    "league": tournament_name,
                    "commence_time": commence_time,
                    "odd_home": float(odd_home),
                    "odd_away": float(odd_away),
                    "volume_home": volume_home if volume_home else 0,
                    "volume_away": volume_away if volume_away else 0,
                })
        return filtered
    except Exception as e:
        logging.error(f"API call failed: {e}")
        return []

def check_crashes(state, current_matches, now):
    alerts = []
    new_state = {}
    threshold_time = now - timedelta(minutes=MAX_MINUTES_CRASH_WINDOW)

    for m in current_matches:
        fid = str(m["fixture_id"])
        prev = state.get(fid, {})
        new_state[fid] = {
            "home": m["home"],
            "away": m["away"],
            "league": m["league"],
            "odd_home": m["odd_home"],
            "odd_away": m["odd_away"],
            "volume_home": m["volume_home"],
            "volume_away": m["volume_away"],
            "timestamp": now.isoformat(),
            "pre_alert_sent": prev.get("pre_alert_sent", False),
            "alert_sent": prev.get("alert_sent", False)
        }

        if fid not in state:
            continue

        try:
            prev_time = datetime.fromisoformat(prev["timestamp"])
        except:
            continue
        if (now - prev_time) > timedelta(minutes=MAX_MINUTES_CRASH_WINDOW):
            continue

        old_home = prev["odd_home"]
        old_away = prev["odd_away"]

        # Lato Home
        if old_home > MIN_STARTING_ODD and m["odd_home"] < MAX_CRASH_ODD:
            drop = (old_home - m["odd_home"]) / old_home
            if drop >= FULL_CRASH_THRESHOLD_PERCENT / 100.0:
                if m["odd_home"] >= QUOTA_MINIMA_DOPO_CRASH:
                    alerts.append({
                        "fixture_id": fid,
                        "home": m["home"],
                        "away": m["away"],
                        "league": m["league"],
                        "side": "Home",
                        "old_odd": old_home,
                        "new_odd": m["odd_home"],
                        "drop": round(drop * 100, 2),
                        "predicted": m["home"],
                        "time": now.strftime("%H:%M:%S"),
                        "alert_type": "definitive",
                        "volume_home": m["volume_home"],
                        "volume_away": m["volume_away"]
                    })
            elif drop >= PRE_CRASH_THRESHOLD_PERCENT / 100.0:
                if not prev.get("pre_alert_sent"):
                    if m["odd_home"] >= QUOTA_MINIMA_DOPO_CRASH:
                        alerts.append({
                            "fixture_id": fid,
                            "home": m["home"],
                            "away": m["away"],
                            "league": m["league"],
                            "side": "Home",
                            "old_odd": old_home,
                            "new_odd": m["odd_home"],
                            "drop": round(drop * 100, 2),
                            "predicted": m["home"],
                            "time": now.strftime("%H:%M:%S"),
                            "alert_type": "pre_alert",
                            "volume_home": m["volume_home"],
                            "volume_away": m["volume_away"]
                        })
                        new_state[fid]["pre_alert_sent"] = True

        # Lato Away
        if old_away > MIN_STARTING_ODD and m["odd_away"] < MAX_CRASH_ODD:
            drop = (old_away - m["odd_away"]) / old_away
            if drop >= FULL_CRASH_THRESHOLD_PERCENT / 100.0:
                if m["odd_away"] >= QUOTA_MINIMA_DOPO_CRASH:
                    alerts.append({
                        "fixture_id": fid,
                        "home": m["home"],
                        "away": m["away"],
                        "league": m["league"],
                        "side": "Away",
                        "old_odd": old_away,
                        "new_odd": m["odd_away"],
                        "drop": round(drop * 100, 2),
                        "predicted": m["away"],
                        "time": now.strftime("%H:%M:%S"),
                        "alert_type": "definitive",
                        "volume_home": m["volume_home"],
                        "volume_away": m["volume_away"]
                    })
            elif drop >= PRE_CRASH_THRESHOLD_PERCENT / 100.0:
                if not prev.get("pre_alert_sent"):
                    if m["odd_away"] >= QUOTA_MINIMA_DOPO_CRASH:
                        alerts.append({
                            "fixture_id": fid,
                            "home": m["home"],
                            "away": m["away"],
                            "league": m["league"],
                            "side": "Away",
                            "old_odd": old_away,
                            "new_odd": m["odd_away"],
                            "drop": round(drop * 100, 2),
                            "predicted": m["away"],
                            "time": now.strftime("%H:%M:%S"),
                            "alert_type": "pre_alert",
                            "volume_home": m["volume_home"],
                            "volume_away": m["volume_away"]
                        })
                        new_state[fid]["pre_alert_sent"] = True

    return alerts, new_state

def save_bet(bets, alert):
    fid = alert["fixture_id"]
    for b in bets:
        if b["fixture_id"] == fid and b["side"] == alert["side"]:
            return bets
    bets.append({
        "fixture_id": fid,
        "home_team": alert["home"],
        "away_team": alert["away"],
        "predicted_winner": alert["predicted"],
        "odd_at_crash": alert["new_odd"],
        "crash_percent": alert["drop"],
        "timestamp": datetime.now().isoformat(),
        "result": "pending"
    })
    return bets

def log_bet_to_csv(alert, filename="bets_log.csv"):
    file_exists = os.path.isfile(filename)
    with open(filename, "a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "fixture_id", "Data", "Ora", "Tipo", "Torneo",
                "Giocatore 1", "Giocatore 2", "Pronostico",
                "Quota prima", "Quota dopo", "Calo %",
                "Volume 1", "Volume 2", "Risultato reale", "Esito"
            ])
        writer.writerow([
            alert.get("fixture_id", ""),
            datetime.now().strftime("%Y-%m-%d"),
            alert["time"],
            alert["alert_type"],
            alert["league"],
            alert["home"],
            alert["away"],
            alert["predicted"],
            f'{alert["old_odd"]:.2f}',
            f'{alert["new_odd"]:.2f}',
            alert["drop"],
            alert.get("volume_home", ""),
            alert.get("volume_away", ""),
            "",
            ""
        ])

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")

    if not is_monitoring_window():
        logging.info("Fuori dalla finestra di monitoraggio. Esco.")
        sys.exit(0)

    logging.info("Market Hunter Tennis (Lunedì) started")

    state = load_json("state.json")
    bets = load_json("bets.json", [])

    matches = fetch_events()
    logging.info(f"Trovate {len(matches)} partite nei tornei target")

    now = datetime.now()
    alerts, new_state = check_crashes(state, matches, now)

    for alert in alerts:
        if alert.get("alert_type") == "pre_alert":
            message = (
                f"⚠️ *MOVIMENTO SOSPETTO*\n"
                f"🎾 {alert['league']}\n"
                f"⚔️ {alert['home']} vs {alert['away']}\n"
                f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
                f"📊 Volumi: {alert.get('volume_home', 'N/D')} / {alert.get('volume_away', 'N/D')}\n"
                f"⏱️ Rilevato alle {alert['time']}\n"
                f"🔮 Possibile crollo su *{alert['predicted']}*"
            )
        else:
            message = (
                f"🚨 *CRASH TENNIS*\n"
                f"🎾 {alert['league']}\n"
                f"⚔️ {alert['home']} vs {alert['away']}\n"
                f"📉 Quota {alert['predicted']}: {alert['old_odd']:.2f} → {alert['new_odd']:.2f} (-{alert['drop']}%)\n"
                f"📊 Volumi: {alert.get('volume_home', 'N/D')} / {alert.get('volume_away', 'N/D')}\n"
                f"⏱️ Rilevato alle {alert['time']}\n"
                f"🔮 Pronostico: *{alert['predicted']}* vincitore"
            )
        send_telegram(message)
        bets = save_bet(bets, alert)
        log_bet_to_csv(alert)

    save_json("state.json", new_state)
    save_json("bets.json", bets)

    solved = [b for b in bets if b["result"] != "pending"]
    if solved:
        won = sum(1 for b in solved if b["result"] == "won")
        logging.info(f"RIEPILOGO: {won}/{len(solved)} vinti ({100*won/len(solved):.1f}%)")
    else:
        logging.info("Nessun bet risolto ancora.")

    logging.info(f"Inviate {len(alerts)} notifiche. Stato salvato.")
