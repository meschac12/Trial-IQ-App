"""
Trial-IQ v2.1
Multi-scout trial evaluation for grassroots and academy football.

How scouts actually work at trials: they scan everyone, write most players
off in seconds and focus on a few. So rating has two speeds:
1. Quick verdict for everyone: Yes / Maybe / No (one tap)
2. Full scores and tags only for the players worth a closer look

Results then show a ranked shortlist, players nobody watched, players the
scouts disagree on, late bloomers, a relative age check, and a short
feedback note for every player who was not picked.
"""

import json
import os
import random
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import date, datetime

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Trial-IQ", page_icon="⚽", layout="wide")

# ---------------------------------------------------------------------------
# SETTINGS
# ---------------------------------------------------------------------------

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.environ.get("TRIALIQ_DB", os.path.join(DATA_DIR, "trialiq.db"))

ATTRS = ["Technical", "Tactical", "Physical", "Mental"]
SCALE = [1, 2, 3, 4, 5]
SCALE_HELP = "1 = well below the level, 3 = at the level, 5 = stand-out"
VERDICTS = ["Yes", "Maybe", "No"]
VERDICT_VALUE = {"Yes": 4.0, "Maybe": 3.0, "No": 1.5}
POSITIONS = ["GK", "DEF", "MID", "FWD", "Not sure"]
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# UK school year runs September to August.
QUARTER_LABEL = {1: "Q1 Sep-Nov", 2: "Q2 Dec-Feb", 3: "Q3 Mar-May", 4: "Q4 Jun-Aug"}

# One-tap tags. Each one carries the wording used in the player's feedback note.
GOOD_TAGS = {
    "Good first touch": "a really good first touch",
    "Passing range": "the range and quality of your passing",
    "Reads the game": "how well you read the game",
    "Composed on the ball": "how calm you were on the ball",
    "Brave on the ball": "being brave and always wanting the ball",
    "Quick": "your pace",
    "Strong in duels": "how strong you were in duels",
    "Works hard": "your work rate",
    "Talks and organises": "how you talked to and organised your teammates",
}
WORK_ON_TAGS = {
    "Loses it under pressure": "keeping the ball when you are under pressure",
    "Quiet in games": "getting on the ball more and showing for it",
    "Positioning": "positioning, and knowing when to support and when to hold",
    "First touch": "your first touch, especially on the move",
    "Decision making": "making quicker decisions on the ball",
    "Stamina": "stamina, so you can keep going for the whole game",
    "Defending 1v1": "defending one against one",
}
# Tags that point to skill rather than size, used by the late bloomer flag.
SKILL_TAGS = {"Good first touch", "Passing range", "Reads the game",
              "Composed on the ball", "Brave on the ball"}

FEEDBACK_BY_ATTR = {
    "Technical": ("your touch and passing stood out",
                  "first touch and passing when under pressure"),
    "Tactical": ("you read the game well and found good positions",
                 "positioning, and knowing when to support and when to hold"),
    "Physical": ("your work rate and energy",
                 "speed, strength and stamina, which will keep coming with growth and training"),
    "Mental": ("your attitude, focus and how you bounced back after mistakes",
               "staying involved and confident after a mistake"),
}


def birth_quarter(month):
    """Quarter of the UK school year (1 = oldest in the year group)."""
    if month is None or pd.isna(month):
        return None
    month = int(month)
    if month in (9, 10, 11):
        return 1
    if month in (12, 1, 2):
        return 2
    if month in (3, 4, 5):
        return 3
    return 4


def split_tags(value):
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return []
    return [t for t in str(value).split("|") if t]


# ---------------------------------------------------------------------------
# STORAGE (SQLite: safe when several scouts save at the same time)
# ---------------------------------------------------------------------------

def db():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=10, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    with closing(db()) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                session_date TEXT,
                age_group TEXT,
                venue TEXT,
                created_at TEXT
            );
            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                bib INTEGER NOT NULL,
                name TEXT NOT NULL,
                position TEXT,
                birth_month INTEGER,
                UNIQUE(session_id, bib)
            );
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                bib INTEGER NOT NULL,
                scout TEXT NOT NULL,
                technical INTEGER, tactical INTEGER, physical INTEGER, mental INTEGER,
                notes TEXT,
                updated_at TEXT,
                UNIQUE(session_id, bib, scout)
            );
            """
        )
        # Columns added in v2.1 (safe to run on an older database)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(ratings)")}
        if "verdict" not in existing:
            conn.execute("ALTER TABLE ratings ADD COLUMN verdict TEXT")
        if "tags" not in existing:
            conn.execute("ALTER TABLE ratings ADD COLUMN tags TEXT")


def query(sql, params=()):
    with closing(db()) as conn:
        return pd.read_sql_query(sql, conn, params=params)


def execute(sql, params=()):
    with closing(db()) as conn, conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def now():
    return datetime.now().isoformat(timespec="seconds")


def get_sessions():
    return query("SELECT * FROM sessions ORDER BY session_date DESC, id DESC")


def create_session(name, session_date, age_group, venue):
    return execute(
        "INSERT INTO sessions (name, session_date, age_group, venue, created_at) VALUES (?,?,?,?,?)",
        (name, str(session_date), age_group, venue, now()),
    )


def delete_session(session_id):
    execute("DELETE FROM ratings WHERE session_id = ?", (session_id,))
    execute("DELETE FROM players WHERE session_id = ?", (session_id,))
    execute("DELETE FROM sessions WHERE id = ?", (session_id,))


def get_players(session_id):
    return query("SELECT * FROM players WHERE session_id = ? ORDER BY bib", (session_id,))


def upsert_player(session_id, bib, name, position, birth_month):
    execute(
        """INSERT INTO players (session_id, bib, name, position, birth_month)
           VALUES (?,?,?,?,?)
           ON CONFLICT(session_id, bib) DO UPDATE SET
             name = excluded.name, position = excluded.position, birth_month = excluded.birth_month""",
        (session_id, int(bib), name.strip(), position, birth_month),
    )


def delete_player(session_id, bib):
    execute("DELETE FROM ratings WHERE session_id = ? AND bib = ?", (session_id, int(bib)))
    execute("DELETE FROM players WHERE session_id = ? AND bib = ?", (session_id, int(bib)))


def get_ratings(session_id):
    return query("SELECT * FROM ratings WHERE session_id = ?", (session_id,))


def set_verdict(session_id, bib, scout, verdict):
    execute(
        """INSERT INTO ratings (session_id, bib, scout, verdict, updated_at) VALUES (?,?,?,?,?)
           ON CONFLICT(session_id, bib, scout) DO UPDATE SET
             verdict = excluded.verdict, updated_at = excluded.updated_at""",
        (session_id, int(bib), scout.strip(), verdict, now()),
    )


def save_detail(session_id, bib, scout, verdict, scores, tags, notes):
    execute(
        """INSERT INTO ratings (session_id, bib, scout, verdict, technical, tactical, physical, mental,
                               tags, notes, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(session_id, bib, scout) DO UPDATE SET
             verdict = excluded.verdict,
             technical = excluded.technical, tactical = excluded.tactical,
             physical = excluded.physical, mental = excluded.mental,
             tags = excluded.tags, notes = excluded.notes, updated_at = excluded.updated_at""",
        (session_id, int(bib), scout.strip(), verdict,
         scores.get("Technical"), scores.get("Tactical"), scores.get("Physical"), scores.get("Mental"),
         "|".join(tags), notes, now()),
    )


def export_all():
    return {
        "app": "Trial-IQ",
        "version": "2.1",
        "exported_at": now(),
        "sessions": get_sessions().to_dict(orient="records"),
        "players": query("SELECT * FROM players").to_dict(orient="records"),
        "ratings": query("SELECT * FROM ratings").to_dict(orient="records"),
    }


def restore_all(backup):
    with closing(db()) as conn, conn:
        conn.execute("DELETE FROM ratings")
        conn.execute("DELETE FROM players")
        conn.execute("DELETE FROM sessions")
        for table in ("sessions", "players", "ratings"):
            for row in backup.get(table, []):
                cols = list(row.keys())
                values = [None if (isinstance(v, float) and pd.isna(v)) else v for v in row.values()]
                conn.execute(
                    f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                    values,
                )


# ---------------------------------------------------------------------------
# DEMO TRIAL
# ---------------------------------------------------------------------------

DEMO_PLAYERS = [
    # bib, name, position, birth month, [tech, tact, phys, mental]
    (1,  "Tariq",      "GK",  9,  [3, 3, 4, 3]),
    (2,  "Jayden",     "DEF", 10, [3, 3, 5, 4]),
    (3,  "Kwame",      "DEF", 7,  [5, 5, 1, 4]),   # late bloomer, one scout writes him off for size
    (4,  "Rashid",     "DEF", 9,  [3, 3, 5, 3]),
    (5,  "Theo",       "MID", 1,  [4, 4, 4, 4]),
    (6,  "Emeka",      "MID", 8,  [5, 5, 2, 4]),   # late bloomer
    (7,  "Cian",       "MID", 2,  [3, 3, 3, 3]),
    (8,  "Darius",     "MID", 9,  [4, 4, 5, 3]),   # scouts split
    (9,  "Samir",      "FWD", 6,  [4, 4, 2, 4]),   # late bloomer
    (10, "Oluwaseun",  "FWD", 12, [2, 2, 3, 3]),
    (11, "Zak",        "DEF", 10, [2, 3, 4, 3]),
    (12, "Tobi",       "MID", 11, [4, 4, 3, 5]),
    (13, "Ibrahim",    "FWD", 5,  [3, 4, 2, 3]),
    (14, "Luca",       "MID", 2,  [5, 4, 4, 4]),
    (15, "Kolade",     "DEF", 4,  [2, 1, 3, 2]),
    (16, "Femi",       "FWD", 9,  [4, 3, 4, 4]),
    (17, "Aaron",      "MID", 11, [3, 2, 4, 3]),
    (18, "Marcel",     "DEF", 1,  [2, 2, 4, 2]),
    (19, "Idris",      "MID", 8,  [4, 4, 2, 4]),   # never watched
    (20, "Reece",      "FWD", 10, [3, 3, 5, 3]),
    (21, "Chidi",      "GK",  3,  [3, 3, 3, 4]),
    (22, "Elijah",     "DEF", 12, [3, 3, 2, 3]),   # never watched
    (23, "Devonte",    "MID", 1,  [2, 3, 3, 2]),
    (24, "Nnamdi",     "FWD", 6,  [3, 2, 3, 3]),   # never watched
]
DEMO_SCOUTS = ["Coach Sam", "Coach Ali", "Coach Jo"]


def demo_tags(base, rng):
    tech, tact, phys, mental = base
    good, work = [], []
    if tech >= 4:
        good.append(rng.choice(["Good first touch", "Passing range", "Composed on the ball"]))
    if tact >= 4:
        good.append("Reads the game")
    if phys >= 4:
        good.append(rng.choice(["Quick", "Strong in duels"]))
    if mental >= 4:
        good.append(rng.choice(["Works hard", "Talks and organises"]))
    if tech <= 2:
        work.append(rng.choice(["Loses it under pressure", "First touch"]))
    if tact <= 2:
        work.append(rng.choice(["Positioning", "Decision making"]))
    if mental <= 2:
        work.append("Quiet in games")
    if phys <= 2 and not work:
        work.append("Defending 1v1")
    return good[:2] + work[:1]


def load_demo():
    """A realistic demo trial: 24 players, 3 scouts, some players never watched."""
    sid = create_session("Demo: U12 open trial", date.today(), "U12", "Demo ground")
    rng = random.Random(11)
    never_watched = {19, 22, 24}
    only_one_scout = {7, 17, 21, 23}
    for bib, name, pos, month, base in DEMO_PLAYERS:
        upsert_player(sid, bib, name, pos, month)
        if bib in never_watched:
            continue
        scouts = [rng.choice(DEMO_SCOUTS)] if bib in only_one_scout else DEMO_SCOUTS
        for scout in scouts:
            scores = {a: max(1, min(5, v + rng.choice([-1, 0, 0, 0, 1]))) for a, v in zip(ATTRS, base)}
            overall = sum(scores.values()) / 4
            verdict = "Yes" if overall >= 3.9 else ("Maybe" if overall >= 3 else "No")
            notes = ""
            if name == "Kwame" and scout == "Coach Jo":
                verdict, notes = "No", "Too small for this group"
            if name == "Darius" and scout == "Coach Jo":
                scores["Technical"], verdict, notes = 1, "No", "Lost the ball a lot in game 2"
            tags = demo_tags(base, rng)
            if verdict == "No":
                # Quick verdict only, the way scouts really work, sometimes with a tag
                save_detail(sid, bib, scout, verdict, {}, tags if rng.random() < 0.6 else [], notes)
            else:
                save_detail(sid, bib, scout, verdict, scores, tags, notes)
    return sid


# ---------------------------------------------------------------------------
# ANALYSIS
# ---------------------------------------------------------------------------

def summarise(players, ratings):
    """One row per player with verdicts, scores, coverage, flags and a decision."""
    rows = []
    for _, p in players.iterrows():
        r = ratings[ratings["bib"] == p["bib"]] if not ratings.empty else ratings
        seen = r[r["verdict"].notna() | r["technical"].notna()] if not r.empty else r
        quarter = birth_quarter(p["birth_month"])
        verdicts = Counter(seen["verdict"].dropna()) if not seen.empty else Counter()
        all_tags = Counter(t for v in (seen["tags"] if not seen.empty else []) for t in split_tags(v))

        scored = seen.dropna(subset=["technical", "tactical", "physical", "mental"]) if not seen.empty else seen
        avgs = None
        split = []
        if len(scored):
            s = scored[[a.lower() for a in ATTRS]].astype(float)
            s.columns = ATTRS
            avgs = s.mean()
            if len(scored) >= 2:
                spread = s.max() - s.min()
                split = [a for a in ATTRS if spread[a] >= 3]
        if verdicts["Yes"] and verdicts["No"]:
            split = ["Verdict"] + split

        n_seen = len(seen)
        overall = round(avgs.mean(), 2) if avgs is not None else None
        verdict_score = (sum(VERDICT_VALUE[v] * c for v, c in verdicts.items()) / sum(verdicts.values())
                         if verdicts else None)

        # Late bloomer: born late in the school year, good on the ball, not yet as physical
        skill_tag_count = sum(c for t, c in all_tags.items() if t in SKILL_TAGS)
        late = False
        if quarter in (3, 4):
            if avgs is not None:
                skill = (avgs["Technical"] + avgs["Tactical"]) / 2
                late = skill >= 3.75 and avgs["Physical"] <= skill - 1
            if not late and skill_tag_count >= 2:
                late = True

        if n_seen == 0:
            decision = "Not watched yet"
        elif split and "Verdict" in split:
            decision = "Scouts split, watch again"
        elif overall is not None and overall >= 4 and len(scored) >= 2 and not split:
            decision = "Strong candidate"
        elif overall is not None and overall >= 4:
            decision = "Strong, get a 2nd opinion" if len(scored) < 2 else "High potential, scouts split"
        elif verdicts["Yes"] and overall is None:
            decision = "Shortlist, needs full scores"
        elif (overall is not None and overall >= 3) or verdicts["Maybe"]:
            decision = "Monitor"
        else:
            decision = "Unlikely this time"

        rank_score = overall if overall is not None else (verdict_score - 0.25 if verdict_score else -1)

        rows.append({
            "Bib": int(p["bib"]),
            "Player": p["name"],
            "Position": p["position"],
            "Born": QUARTER_LABEL.get(quarter, "Not known"),
            "Seen by": n_seen,
            "Yes": verdicts["Yes"], "Maybe": verdicts["Maybe"], "No": verdicts["No"],
            **{a: (round(avgs[a], 1) if avgs is not None else None) for a in ATTRS},
            "Overall": overall,
            "Tags": ", ".join(t for t, _ in all_tags.most_common(3)),
            "Split on": ", ".join(split),
            "Late bloomer": "Yes" if late else "",
            "Decision": decision,
            "_rank": rank_score,
        })

    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.sort_values(["_rank", "Seen by"], ascending=[False, False]).drop(columns="_rank")
        df.insert(0, "Rank", range(1, len(df) + 1))
    return df


def feedback_message(row, player_ratings, session_name, session_date, sign_off):
    """Kind, specific note built from tags first, then scores, never from private notes."""
    tags = Counter(t for v in player_ratings["tags"] for t in split_tags(v)) if not player_ratings.empty else Counter()
    good = [t for t, _ in tags.most_common() if t in GOOD_TAGS]
    work = [t for t, _ in tags.most_common() if t in WORK_ON_TAGS]

    well_text = GOOD_TAGS[good[0]] if good else None
    work_text = WORK_ON_TAGS[work[0]] if work else None

    if row["Overall"] is not None and not pd.isna(row["Overall"]):
        scores = {a: row[a] for a in ATTRS}
        best = max(scores, key=scores.get)
        worst = min(scores, key=scores.get)
        if best == worst:
            worst = [a for a in ATTRS if a != best][0]
        well_text = well_text or FEEDBACK_BY_ATTR[best][0]
        work_text = work_text or FEEDBACK_BY_ATTR[worst][1]

    well_text = well_text or "the effort you put in all session"
    work_text = work_text or "getting on the ball more so the coaches can see what you can do"

    lines = [
        f"Hi, thank you for coming to {session_name} on {session_date}.",
        f"Here is some short feedback for {row['Player']} (bib {row['Bib']}).",
        "",
        f"What went well: {well_text}.",
        f"Something to work on: {work_text}.",
        "",
        "Keep playing and keep enjoying it. We hope to see you again.",
    ]
    if sign_off.strip():
        lines.append(sign_off.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ACCESS
# ---------------------------------------------------------------------------

def get_secret(key):
    try:
        return st.secrets.get(key)
    except Exception:
        return None


def passcode_ok():
    code = get_secret("passcode")
    if not code or st.session_state.get("unlocked"):
        return True
    st.markdown("## ⚽ Trial-IQ")
    entered = st.text_input("Club passcode", type="password")
    if entered:
        if entered == str(code):
            st.session_state["unlocked"] = True
            st.rerun()
        else:
            st.error("That passcode is not right.")
    return False


# ---------------------------------------------------------------------------
# APP
# ---------------------------------------------------------------------------

init_db()
if not passcode_ok():
    st.stop()

st.markdown("## ⚽ Trial-IQ")
st.caption("Rate fast at the side of the pitch. Make sure every player is seen. Decide fairly after.")

# ----- Sidebar: session + scout ------------------------------------------------
with st.sidebar:
    st.markdown("### Trial session")
    sessions = get_sessions()
    ids = sessions["id"].tolist()
    default_index = ids.index(st.session_state["pick_session"]) if st.session_state.get("pick_session") in ids else 0

    session_id = None
    if not sessions.empty:
        labels = {int(r.id): f"{r.name} ({r.session_date})" for r in sessions.itertuples()}
        session_id = st.selectbox("Choose session", options=list(labels.keys()),
                                  format_func=lambda i: labels[i], index=default_index)

    with st.expander("New trial session", expanded=sessions.empty):
        with st.form("new_session", clear_on_submit=True):
            s_name = st.text_input("Name", placeholder="U12 open trial")
            s_date = st.date_input("Date", value=date.today())
            s_age = st.text_input("Age group", placeholder="U12")
            s_venue = st.text_input("Venue")
            if st.form_submit_button("Create session"):
                if not s_name.strip():
                    st.warning("Give the session a name.")
                else:
                    st.session_state["pick_session"] = create_session(s_name.strip(), s_date, s_age, s_venue)
                    st.rerun()

    if st.button("Load demo trial"):
        st.session_state["pick_session"] = load_demo()
        st.rerun()

    st.markdown("### You")
    st.text_input("Your scout name", key="scout", placeholder="Coach Sam")

    if not get_secret("passcode"):
        st.caption("No passcode is set, so anyone with the link can open this app.")

if session_id is None:
    st.info("Create a trial session in the sidebar to start, or load the demo trial to see how it works.")
    st.stop()

session = sessions[sessions["id"] == session_id].iloc[0]
players = get_players(session_id)
ratings = get_ratings(session_id)
scout = st.session_state.get("scout", "").strip()

tab_squad, tab_quick, tab_detail, tab_results, tab_feedback, tab_backup = st.tabs(
    ["1. Squad", "2. Quick verdicts", "3. Closer look", "4. Results", "5. Feedback", "Backup"]
)

# ----- 1. Squad -------------------------------------------------------------
with tab_squad:
    st.markdown(f"#### Squad for {session['name']}")
    st.caption(
        "Keep it light: first names and bib numbers. Birth month is enough for the relative age "
        "check, so never store full dates of birth. For under-18s, make sure the club has parents' consent."
    )

    with st.form("add_player", clear_on_submit=True):
        c1, c2, c3, c4 = st.columns([1, 2, 1.2, 1.2])
        bib = c1.number_input("Bib", min_value=1, max_value=999, step=1,
                              value=int(players["bib"].max()) + 1 if not players.empty else 1)
        name = c2.text_input("Name")
        position = c3.selectbox("Position", POSITIONS)
        month = c4.selectbox("Birth month", ["Not known"] + MONTHS)
        if st.form_submit_button("Add player"):
            if not name.strip():
                st.warning("Add a name.")
            else:
                bm = None if month == "Not known" else MONTHS.index(month) + 1
                upsert_player(session_id, bib, name, position, bm)
                st.rerun()

    with st.expander("Paste a whole squad at once"):
        st.caption("One player per line: bib, name, position, birth month (1 to 12). Example: 7, Jaden, MID, 8")
        pasted = st.text_area("Squad list", height=150, label_visibility="collapsed")
        if st.button("Add these players"):
            added, problems = 0, []
            for line in pasted.strip().splitlines():
                parts = [x.strip() for x in line.split(",")]
                try:
                    b, n = int(parts[0]), parts[1]
                    pos = parts[2].upper() if len(parts) > 2 and parts[2].upper() in POSITIONS else "Not sure"
                    bm = int(parts[3]) if len(parts) > 3 and parts[3] else None
                    if bm is not None and not 1 <= bm <= 12:
                        bm = None
                    upsert_player(session_id, b, n, pos, bm)
                    added += 1
                except (ValueError, IndexError):
                    problems.append(line)
            if problems:
                st.warning(f"Could not read: {problems}")
            st.success(f"Added {added} players.")
            st.rerun()

    if players.empty:
        st.info("No players yet.")
    else:
        show = players.copy()
        show["Birth month"] = show["birth_month"].apply(lambda m: MONTHS[int(m) - 1] if pd.notna(m) else "")
        show["Born"] = show["birth_month"].apply(lambda m: QUARTER_LABEL.get(birth_quarter(m), ""))
        st.dataframe(
            show[["bib", "name", "position", "Birth month", "Born"]].rename(
                columns={"bib": "Bib", "name": "Name", "position": "Position"}),
            hide_index=True, width="stretch",
        )
        c1, c2 = st.columns([1, 3])
        remove = c1.selectbox("Remove bib", players["bib"].tolist(), key="remove_bib")
        if c2.button("Remove player and their ratings"):
            delete_player(session_id, remove)
            st.rerun()


def seen_counts():
    if ratings.empty:
        return {}
    seen = ratings[ratings["verdict"].notna() | ratings["technical"].notna()]
    return seen.groupby("bib")["scout"].nunique().to_dict()


# ----- 2. Quick verdicts ----------------------------------------------------
with tab_quick:
    if not scout:
        st.warning("Type your scout name in the sidebar first.")
    elif players.empty:
        st.info("Add the squad first.")
    else:
        mine = ratings[ratings["scout"] == scout] if not ratings.empty else ratings
        my_verdicts = {int(r.bib): r.verdict for r in mine.itertuples() if isinstance(r.verdict, str)}
        counts = seen_counts()
        unseen = [int(b) for b in players["bib"] if counts.get(int(b), 0) == 0]

        st.progress(len(my_verdicts) / len(players),
                    text=f"You've given a verdict on {len(my_verdicts)} of {len(players)}")
        if unseen:
            st.warning("No scout has watched: " + ", ".join(f"#{b}" for b in unseen) + ". Have a look at them next.")
        st.caption("One tap per player: would you want to see more of them? You only need full scores for your Yes and Maybe players.")

        show_filter = st.radio("Show", ["Everyone", "Not seen by me yet", "Not seen by anyone"],
                               horizontal=True, key="quick_filter")

        def on_verdict(bib_, key_):
            set_verdict(session_id, bib_, scout, st.session_state.get(key_))

        for p in players.itertuples():
            b = int(p.bib)
            if show_filter == "Not seen by me yet" and b in my_verdicts:
                continue
            if show_filter == "Not seen by anyone" and b not in unseen:
                continue
            key = f"v_{session_id}_{scout}_{b}"
            if key not in st.session_state:
                st.session_state[key] = my_verdicts.get(b)
            c1, c2 = st.columns([2, 3])
            c1.markdown(f"**#{b} {p.name}** · {p.position}  \n"
                        f"<small>Seen by {counts.get(b, 0)} scout(s)</small>", unsafe_allow_html=True)
            with c2:
                st.segmented_control(f"Verdict for #{b}", VERDICTS, key=key, on_change=on_verdict,
                                     args=(b, key), label_visibility="collapsed")

# ----- 3. Closer look -------------------------------------------------------
with tab_detail:
    if not scout:
        st.warning("Type your scout name in the sidebar first.")
    elif players.empty:
        st.info("Add the squad first.")
    else:
        mine = ratings[ratings["scout"] == scout] if not ratings.empty else ratings
        my_verdicts = {int(r.bib): r.verdict for r in mine.itertuples() if isinstance(r.verdict, str)}
        scored_bibs = {int(r.bib) for r in mine.itertuples() if pd.notna(r.technical)}

        which = st.radio("Players", ["My Yes and Maybe", "Everyone"], horizontal=True, key="detail_filter")
        bibs = [int(b) for b in players["bib"]
                if which == "Everyone" or my_verdicts.get(int(b)) in ("Yes", "Maybe")]
        if not bibs:
            st.info("Give some Yes or Maybe verdicts first, or switch to Everyone.")
        else:
            todo = [b for b in bibs if b not in scored_bibs]
            st.caption(f"{len(todo)} of these still need full scores.")
            if st.session_state.get("next_detail") in bibs:
                st.session_state["detail_pick"] = st.session_state.pop("next_detail")
            if st.session_state.get("detail_pick") not in bibs:
                st.session_state["detail_pick"] = todo[0] if todo else bibs[0]
            info = {int(r.bib): r for r in players.itertuples()}
            pick = st.selectbox(
                "Player", bibs, key="detail_pick",
                format_func=lambda b: f"#{b}  {info[b].name}  ({info[b].position})"
                                      f"{'  · ' + my_verdicts[b] if b in my_verdicts else ''}"
                                      f"{'  ✓' if b in scored_bibs else ''}",
            )
            prev = mine[mine["bib"] == pick].iloc[0] if not mine[mine["bib"] == pick].empty else None
            base = f"{session_id}_{scout}_{pick}"

            def init(key, value):
                if key not in st.session_state:
                    st.session_state[key] = value

            init(f"dv_{base}", my_verdicts.get(pick))
            for a in ATTRS:
                v = prev[a.lower()] if prev is not None else None
                init(f"{a}_{base}", int(v) if v is not None and pd.notna(v) else None)
            prev_tags = split_tags(prev["tags"]) if prev is not None else []
            init(f"good_{base}", [t for t in prev_tags if t in GOOD_TAGS])
            init(f"work_{base}", [t for t in prev_tags if t in WORK_ON_TAGS])
            init(f"note_{base}", prev["notes"] if prev is not None and isinstance(prev["notes"], str) else "")

            verdict = st.segmented_control("Verdict", VERDICTS, key=f"dv_{base}")
            st.caption(SCALE_HELP)
            scores = {}
            cols = st.columns(2)
            for i, a in enumerate(ATTRS):
                with cols[i % 2]:
                    scores[a] = st.segmented_control(a, SCALE, key=f"{a}_{base}")
            good = st.pills("What went well", list(GOOD_TAGS), selection_mode="multi", key=f"good_{base}")
            work = st.pills("Needs work", list(WORK_ON_TAGS), selection_mode="multi", key=f"work_{base}")
            notes = st.text_input("Private note (never shown to players)", key=f"note_{base}")

            if st.button("Save", type="primary"):
                missing = [a for a in ATTRS if scores[a] is None]
                if missing and len(missing) < 4:
                    st.warning(f"Tap a score for: {', '.join(missing)}, or clear them all to save tags only.")
                else:
                    save_detail(session_id, pick, scout, verdict, {} if missing else scores,
                                list(good) + list(work), notes)
                    remaining = [b for b in todo if b != pick]
                    if remaining:
                        st.session_state["next_detail"] = remaining[0]
                    st.toast(f"Saved #{pick} {info[pick].name}")
                    st.rerun()

# ----- 4. Results -----------------------------------------------------------
with tab_results:
    summary = summarise(players, ratings)
    if summary.empty:
        st.info("No players yet.")
    else:
        seen_rows = summary[summary["Seen by"] > 0]
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Players watched", f"{len(seen_rows)} / {len(summary)}")
        c2.metric("Seen by 2+ scouts", int((summary["Seen by"] >= 2).sum()))
        c3.metric("Strong candidates", int((summary["Decision"] == "Strong candidate").sum()))
        c4.metric("Watch again", int((summary["Split on"] != "").sum()))
        c5.metric("Late bloomers", int((summary["Late bloomer"] == "Yes").sum()))

        # Coverage first: the player nobody watched is the biggest risk at a trial
        st.markdown("#### 🔦 Players nobody watched")
        nobody = summary[summary["Seen by"] == 0]
        once = summary[summary["Seen by"] == 1]
        if nobody.empty:
            st.success("Every player has been watched by at least one scout.")
        else:
            st.error("Not watched by any scout: " + ", ".join(f"#{r.Bib} {r.Player}" for r in nobody.itertuples())
                     + ". Put a scout on them in the next game.")
        if not once.empty:
            st.info("Only one scout so far: " + ", ".join(f"#{r.Bib} {r.Player}" for r in once.itertuples()))

        st.markdown("#### Ranked shortlist")
        st.dataframe(summary, hide_index=True, width="stretch")
        st.download_button("Download results (CSV)", summary.to_csv(index=False).encode(),
                           file_name=f"trial-iq-results-{session['session_date']}.csv", mime="text/csv")

        st.markdown("#### 👀 Watch again: scouts disagree")
        splits = summary[summary["Split on"] != ""]
        if splits.empty:
            st.success("Scouts broadly agree on everyone.")
        else:
            for _, row in splits.iterrows():
                r = ratings[ratings["bib"] == row["Bib"]]
                parts = []
                for a in row["Split on"].split(", "):
                    if a == "Verdict":
                        parts.append("Verdict (" + ", ".join(f"{s} {v}" for s, v in zip(r["scout"], r["verdict"]) if isinstance(v, str)) + ")")
                    else:
                        parts.append(f"{a} (" + ", ".join(f"{s} {int(v)}" for s, v in zip(r["scout"], r[a.lower()]) if pd.notna(v)) + ")")
                st.warning(f"**#{row['Bib']} {row['Player']}**: " + "; ".join(parts))

        st.markdown("#### 🌱 Late bloomer watch")
        st.caption("Born late in the school year (Mar to Aug) and good on the ball, but not yet as physical. "
                   "These players often get dropped for size, not ability.")
        late = summary[summary["Late bloomer"] == "Yes"]
        if late.empty:
            st.info("No late bloomers flagged in this group.")
        else:
            for _, row in late.iterrows():
                detail = (f"Technical {row['Technical']}, Tactical {row['Tactical']}, Physical {row['Physical']}"
                          if pd.notna(row["Technical"]) else f"Tags: {row['Tags']}")
                st.success(f"**#{row['Bib']} {row['Player']}** ({row['Born']}): {detail}. "
                           f"Currently: {row['Decision']}. Judge on skill, not size.")

        known = seen_rows[seen_rows["Born"] != "Not known"]
        if len(known) >= 4:
            st.markdown("#### 📅 Relative age check")
            top_n = max(1, round(len(known) / 3))
            top = known.head(top_n)
            order = list(QUARTER_LABEL.values())
            chart = pd.DataFrame({
                "Whole group": known["Born"].value_counts().reindex(order, fill_value=0),
                f"Top {top_n}": top["Born"].value_counts().reindex(order, fill_value=0),
            })
            st.bar_chart(chart, stack=False)
            older = top["Born"].isin([QUARTER_LABEL[1], QUARTER_LABEL[2]]).sum()
            msg = f"{older} of your top {top_n} were born Sep to Feb (the older half of the year group)."
            if older / len(top) >= 0.7:
                st.warning(msg + " That is a strong lean towards older players. Check you are not picking on size.")
            else:
                st.info(msg)

        st.markdown("#### One player in detail")
        names = summary.set_index("Bib")["Player"].to_dict()
        pick_detail = st.selectbox("Player", summary["Bib"].tolist(),
                                   format_func=lambda b: f"#{b} {names[b]}", key="detail_results")
        r = ratings[ratings["bib"] == pick_detail]
        if r.empty:
            st.info("Not watched yet.")
        else:
            detail = r[["scout", "verdict", "technical", "tactical", "physical", "mental", "tags", "notes"]].copy()
            detail["tags"] = detail["tags"].apply(lambda v: ", ".join(split_tags(v)))
            detail.columns = ["Scout", "Verdict"] + ATTRS + ["Tags", "Private note"]
            st.dataframe(detail, hide_index=True, width="stretch")

# ----- 5. Feedback ----------------------------------------------------------
with tab_feedback:
    st.markdown("#### Feedback for players who were not picked")
    st.caption("Most trials send nothing back. A short, kind note makes the club look good and helps the player. "
               "It is built from the scouts' tags and scores. Private notes are never included. "
               "Read each one before you send it.")
    summary = summarise(players, ratings)
    sign_off = st.text_input("Sign off", placeholder="Coach Sam, Addington Juniors")
    watched = summary[summary["Seen by"] > 0] if not summary.empty else summary
    if watched.empty:
        st.info("No players watched yet.")
    else:
        names = watched.set_index("Bib")["Player"].to_dict()
        default_out = watched[watched["Decision"] != "Strong candidate"]["Bib"].tolist()
        chosen = st.multiselect("Players to write feedback for", watched["Bib"].tolist(),
                                default=default_out, format_func=lambda b: f"#{b} {names[b]}")
        messages = []
        for b in chosen:
            row = watched.set_index("Bib").loc[b].copy()
            row["Bib"] = b
            msg = feedback_message(row, ratings[ratings["bib"] == b], session["name"],
                                   session["session_date"], sign_off)
            messages.append({"Bib": b, "Player": row["Player"], "Message": msg})
            with st.expander(f"#{b} {row['Player']}"):
                st.text_area("Message", msg, height=200, key=f"fb_{b}", label_visibility="collapsed")
        if messages:
            st.download_button("Download all feedback (CSV)", pd.DataFrame(messages).to_csv(index=False).encode(),
                               file_name=f"trial-iq-feedback-{session['session_date']}.csv", mime="text/csv")

# ----- Backup ---------------------------------------------------------------
with tab_backup:
    st.markdown("#### Backup")
    st.caption("Free hosting can wipe saved data when the app restarts or sleeps. "
               "Download a backup at the end of every trial.")
    st.download_button("Download full backup", json.dumps(export_all(), indent=1, default=str).encode(),
                       file_name=f"trial-iq-backup-{date.today()}.json", mime="application/json", type="primary")

    st.markdown("#### Restore")
    upload = st.file_uploader("Backup file (.json)", type="json")
    sure = st.checkbox("I understand this replaces everything currently in the app")
    if upload is not None and st.button("Restore backup", disabled=not sure):
        try:
            restore_all(json.load(upload))
            init_db()
            st.success("Backup restored.")
            st.rerun()
        except Exception as e:
            st.error(f"Could not restore that file: {e}")

    st.markdown("#### Delete this session")
    sure_delete = st.checkbox(f"Delete '{session['name']}' and all its ratings")
    if st.button("Delete session", disabled=not sure_delete):
        delete_session(session_id)
        st.session_state.pop("pick_session", None)
        st.rerun()
