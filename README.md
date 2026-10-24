# Trial-IQ

Multi-scout trial evaluation for grassroots and academy football.

**Live app:** [Trial-IQ on Streamlit](https://trial-iq-app-b2ttpyfzogkf9h5xfwqn68.streamlit.app/)

---

## The problem

At a grassroots football trial, a head coach watches 24 players for 90 minutes and picks a shortlist. That is how most clubs do it.

Three things go wrong consistently:

**Players go completely unseen.** A busy trial has too many players for one person to watch. Some kids go home without a single coach having watched them properly.

**Scouts can't agree — and nobody knows it.** When multiple coaches are watching, one might give a player a 5 and another a 1. Without a system to aggregate those ratings, the disagreement never surfaces. The final decision usually reflects whoever spoke last or loudest.

**The relative age effect goes unchecked.** In English youth football, the school year runs September to August. A boy born in September is almost a full year older than one born the following August. At under-12 level, that gap is enormous physically. Coaches routinely pick older, bigger players over younger, technically superior ones — without realising that is what they are doing. Research consistently shows that players born in Q1 (September to November) are dramatically overrepresented in academy and elite youth programmes.

Trial-IQ is built to fix all three of these.

---

## How it works

### Two-speed rating

The app is designed around how scouts actually watch a trial, not how forms say they should.

In reality, a scout scans everyone, writes most players off in a few seconds, and focuses closely on a handful. Trial-IQ mirrors that:

1. **Quick verdict for every player** — Yes, Maybe, or No. One tap. This takes seconds per player and ensures every player is rated by at least someone.
2. **Full scores and tags only for the Yes and Maybe players** — Technical, Tactical, Physical and Mental on a 1 to 5 scale, plus one-tap observation tags.

This makes the app fast enough to use at the side of a pitch on a phone, which is where scouts actually are.

### What the results show

- **Ranked shortlist** — each player's average score across all scouts, how much the scouts agree, and a suggested decision (Strong candidate, Monitor, Scouts split — watch again, etc.)
- **Players nobody watched** — flagged prominently at the top of the results tab, because a missed player is the biggest failure a trial can have
- **Watch again** — players where two scouts gave significantly different scores or opposite verdicts, surfaced so the club can get a third opinion before deciding
- **Late bloomer watch** — players born in Q3 or Q4 (March to August) who score well on Technical and Tactical but lower on Physical. These players are often overlooked because they are smaller, not because they are less able.
- **Relative age check** — a bar chart comparing the birth quarter spread of the whole group against the top third of the shortlist. If your top picks are all Q1 and Q2 players, the chart makes that bias visible.
- **Feedback notes** — a short, kind message for every player who was not picked, generated from the scouts' tags and scores. Private scout notes are never included. Most clubs send nothing after a trial. A short note is good for players and good for the club's reputation.

---

## Technical design

### SQLite with concurrent write protection

Multiple scouts use the app at the same time — each one saving their ratings independently during the trial. The database uses SQLite with `timeout=10` and upsert logic (`INSERT OR CONFLICT DO UPDATE`) to handle concurrent writes safely without losing data.

The schema separates sessions, players, and ratings into three tables with foreign key constraints and cascade deletes, so cleaning up a session removes all associated data cleanly.

### Scoring and decision logic

The app does not use a single weighted average to make decisions. The `summarise()` function combines:

- Average scores across scouts where full ratings exist
- A verdict score (Yes = 4.0, Maybe = 3.0, No = 1.5) where only quick verdicts exist
- A spread check: if any attribute shows a 3+ point difference between scouts, it is flagged as a split
- A verdict conflict check: if one scout said Yes and another said No, that surfaces as "Scouts split, watch again" regardless of the average

This means a player with an average of 3.5 from two scouts where one gave 5 and the other gave 2 is treated differently from a player who got 3.5 from two scouts who agreed. Both get the same number but only one gets flagged.

### Late bloomer algorithm

The late bloomer flag combines birth quarter, average scores, and observation tags:

- Player must be born in Q3 (March to May) or Q4 (June to August) of the UK school year
- Either: average Technical + Tactical score of 3.75 or higher, with Physical scoring at least 1 point below that average
- Or: at least 2 skill-based tags (Good first touch, Passing range, Reads the game, Composed on the ball, Brave on the ball) even without full scores

This means the flag works even when only quick verdicts and tags have been given, not just when a scout has completed a full 1 to 5 breakdown.

### Feedback generation

The feedback note builder works in priority order:

1. Observation tags from scouts (most specific — mentions the actual thing that stood out)
2. Score-based text derived from the player's best and worst attributes (if no tags)
3. Generic fallback text (if neither scores nor tags exist)

Private scout notes are explicitly excluded. The note is always addressed to the player by first name and bib number, and never mentions the decision.

---

## Tech stack

| Tool | Purpose |
|------|---------|
| Python | Core language |
| Streamlit | App interface — used at the side of the pitch on mobile |
| Pandas | Data aggregation and shortlist ranking |
| SQLite | Persistent storage with concurrent write support |

---

## How to run locally

```bash
git clone https://github.com/meschac12/Trial-IQ-App
cd Trial-IQ-App
pip install -r requirements.txt
streamlit run app.py
```

Click **Load demo trial** in the sidebar to see a full worked example with 24 players, 3 scouts, late bloomers, a scout disagreement, and players who were never watched.

To lock the app with a club passcode, add the following in Streamlit Cloud under App settings, then Secrets:

```
passcode = "your-code-here"
```

---

## Project background

I built this to solve a real problem I had watched happen in grassroots football. The relative age effect is well documented in sports science research and remains largely unaddressed at grassroots level. Most clubs have no system at all — a head coach with a notepad at best.

The app was designed to be realistic about how scouts actually behave (fast judgements, limited time, variable coverage) rather than how a form might assume they work. The two-speed rating system and the "players nobody watched" flag both came from thinking about what actually goes wrong at trials, not what a data collection form looks like.

---

*Built by Meschac Goredji — MSc Computer Science, University of Kent (Merit)*
