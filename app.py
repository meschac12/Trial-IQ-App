import streamlit as st
import pandas as pd
import os

st.set_page_config(page_title="Trial-IQ", layout="wide")

# ---------------------------
# HEADER
# ---------------------------

st.markdown("## ⚽ Trial-IQ")
st.markdown("### Multi-Scout Evaluation & Decision Support")
st.markdown("---")

file = "datatrial.csv"

# ---------------------------
# INPUT SECTION
# ---------------------------

st.markdown("### 📝 Enter Evaluation")

player_name = st.text_input("Player Name")
scout_name = st.text_input("Scout Name")

col1, col2 = st.columns(2)

with col1:
    technical = st.slider("Technical", 1, 10)
    tactical = st.slider("Tactical", 1, 10)

with col2:
    physical = st.slider("Physical", 1, 10)
    mental = st.slider("Mental", 1, 10)

notes = st.text_area("Notes")

# ---------------------------
# SAVE DATA
# ---------------------------

if st.button("Submit Evaluation"):

    if player_name == "" or scout_name == "":
        st.warning("Please fill in Player Name and Scout Name")
    else:
        new_data = pd.DataFrame([{
            "Player": player_name,
            "Scout": scout_name,
            "Technical": technical,
            "Tactical": tactical,
            "Physical": physical,
            "Mental": mental,
            "Notes": notes
        }])

        if os.path.exists(file):
            existing = pd.read_csv(file)
            updated = pd.concat([existing, new_data], ignore_index=True)
        else:
            updated = new_data

        updated.to_csv(file, index=False)

        st.success("Evaluation submitted!")

# ---------------------------
# LOAD DATA
# ---------------------------

if os.path.exists(file):

    df = pd.read_csv(file)

    st.markdown("### 📊 All Evaluations")
    st.dataframe(df)

    # ---------------------------
    # INSIGHTS SECTION
    # ---------------------------

    st.markdown("### 🧠 Player Insights")

    players = df["Player"].unique()

    for player in players:

        st.markdown(f"## {player}")

        player_data = df[df["Player"] == player]
        scores = player_data[["Technical", "Tactical", "Physical", "Mental"]]

        avg_scores = scores.mean()
        avg_total = avg_scores.mean()

        variance_per_attribute = scores.std()
        overall_variance = variance_per_attribute.mean()

        # Agreement Logic
        if overall_variance < 1:
            agreement = "High"
        elif overall_variance < 2:
            agreement = "Medium"
        else:
            agreement = "Low"

        # Decision Logic
        if avg_total >= 7 and agreement == "High":
            decision = "✅ Strong Progression Candidate"
        elif avg_total >= 7:
            decision = "⚠️ High Potential - Needs Discussion"
        elif avg_total >= 5:
            decision = "🟡 Monitor"
        else:
            decision = "❌ Unlikely to Progress"

        # ---------------------------
        # DISPLAY METRICS (Cleaner UI)
        # ---------------------------

        col1, col2, col3 = st.columns(3)

        col1.metric("Average Score", f"{avg_total:.2f}")
        col2.metric("Agreement", agreement)
        col3.metric("Variance", f"{overall_variance:.2f}")

        # Recommendation
        st.markdown(f"### 📌 Recommendation: {decision}")

        # Highlight disagreement areas
        problem_areas = variance_per_attribute[variance_per_attribute > 1]

        if not problem_areas.empty:
            st.error(f"Disagreement in: {list(problem_areas.index)}")

        st.markdown("---")