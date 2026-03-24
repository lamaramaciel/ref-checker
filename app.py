import streamlit as st
import pandas as pd
import requests
import re
import time
import io
from agent import verify_cell

st.set_page_config(
    page_title="Reference Checker",
    page_icon="🔍",
    layout="wide"
)

st.title("🔍 Reference Checker")
st.caption("Automatically verifies whether spreadsheet data matches the provided reference links.")

# --- Sidebar: config ---
with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.text_input("Gemini API Key", type="password", help="Your Google Gemini API key")
    st.divider()
    st.markdown("**How to use:**")
    st.markdown("1. Enter your Gemini API Key")
    st.markdown("2. Upload a `.csv` spreadsheet")
    st.markdown("3. Choose which rows to verify")
    st.markdown("4. Click **Run Verification**")
    st.markdown("5. Download the report")
    st.divider()
    st.caption("Automatically detects `[References]` columns and verifies each data ↔ source pair.")

# --- Upload ---
uploaded = st.file_uploader("📂 Upload spreadsheet (.csv)", type=["csv"])

if not uploaded:
    st.info("Please upload a CSV file to get started.")
    st.stop()

if not api_key:
    st.warning("Please enter your Gemini API Key in the sidebar.")
    st.stop()

# --- Read CSV ---
try:
    df = pd.read_csv(uploaded)
except Exception as e:
    st.error(f"Error reading CSV: {e}")
    st.stop()

st.success(f"✅ Spreadsheet loaded: **{len(df)} rows** × **{len(df.columns)} columns**")

# --- Detect column pairs ---
ref_pairs = []
for col in df.columns:
    if "[References]" in col or "[Reference]" in col:
        data_col = col.replace(" [References]", "").replace(" [Reference]", "").strip()
        if data_col in df.columns:
            ref_pairs.append((data_col, col))
        else:
            for c in df.columns:
                if c.strip() == data_col:
                    ref_pairs.append((c, col))
                    break

if not ref_pairs:
    st.error("No `[References]` columns found. Please check your spreadsheet format.")
    st.stop()

st.write(f"**{len(ref_pairs)} pairs** detected (data column ↔ reference column):")
cols_preview = st.columns(3)
for i, (data_col, ref_col) in enumerate(ref_pairs):
    cols_preview[i % 3].markdown(f"- `{data_col}`")

st.divider()

# --- Row selection ---
st.subheader("Select rows to verify")

col1, col2 = st.columns(2)
with col1:
    start_row = st.number_input("Start row", min_value=1, max_value=len(df), value=1)
with col2:
    end_row = st.number_input("End row", min_value=1, max_value=len(df), value=min(3, len(df)))

selected_df = df.iloc[int(start_row)-1 : int(end_row)]
st.caption(f"{len(selected_df)} row(s) selected · {len(ref_pairs)} columns to verify = **{len(selected_df) * len(ref_pairs)} checks**")

# --- Preview ---
with st.expander("👁️ Preview selected rows"):
    st.dataframe(selected_df, use_container_width=True)

st.divider()

# --- Run button ---
if st.button("🚀 Run Verification", type="primary", use_container_width=True):

    results = []
    total = len(selected_df) * len(ref_pairs)
    progress = st.progress(0, text="Starting verification...")
    status_box = st.empty()
    count = 0

    for row_idx, row in selected_df.iterrows():
        org_name = str(row.get("Name", row_idx + 1))

        for data_col, ref_col in ref_pairs:
            count += 1
            declared_value = str(row.get(data_col, "")).strip()
            references_raw = str(row.get(ref_col, "")).strip()

            # clean [Ref] suffix from declared value
            declared_clean = re.sub(r'\s*\[Ref\d*\]', '', declared_value).strip()

            progress.progress(count / total, text=f"Checking {count}/{total}: **{org_name}** · `{data_col}`")
            status_box.info(f"🔎 `{org_name}` — `{data_col}`: `{declared_clean[:80]}`")

            # skip empty cells
            if not declared_clean or declared_clean in ("nan", "N/A", "/", ""):
                results.append({
                    "Organization": org_name,
                    "Column": data_col,
                    "Declared Value": declared_clean,
                    "Reference": references_raw[:100],
                    "Status": "⏭️ Skipped",
                    "Verdict": "Empty value or N/A",
                    "Source Excerpt": ""
                })
                continue

            if not references_raw or references_raw in ("nan", "N/A", "/", ""):
                results.append({
                    "Organization": org_name,
                    "Column": data_col,
                    "Declared Value": declared_clean,
                    "Reference": "",
                    "Status": "❓ No Reference",
                    "Verdict": "No reference provided",
                    "Source Excerpt": ""
                })
                continue

            # verify via Jina + Gemini
            result = verify_cell(
                declared_value=declared_clean,
                column_name=data_col,
                references_raw=references_raw,
                gemini_api_key=api_key
            )

            results.append({
                "Organization": org_name,
                "Column": data_col,
                "Declared Value": declared_clean,
                "Reference": references_raw[:120],
                "Status": result["status"],
                "Verdict": result["verdict"],
                "Source Excerpt": result["excerpt"]
            })

            time.sleep(0.3)

    progress.progress(1.0, text="✅ Verification complete!")
    status_box.empty()

    # --- Results ---
    st.divider()
    st.subheader("📊 Results")

    results_df = pd.DataFrame(results)

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", len(results_df))
    m2.metric("✅ Confirmed", len(results_df[results_df["Status"] == "✅ Confirmed"]))
    m3.metric("❌ Incorrect", len(results_df[results_df["Status"] == "❌ Incorrect"]))
    m4.metric("⚠️ Partial", len(results_df[results_df["Status"] == "⚠️ Partial"]))
    m5.metric("🔒 Inaccessible", len(results_df[results_df["Status"].isin(["🔒 Inaccessible", "❓ Not Found", "❓ No Reference"])]))

    filter_status = st.multiselect(
        "Filter by status",
        options=results_df["Status"].unique().tolist(),
        default=results_df["Status"].unique().tolist()
    )
    filtered = results_df[results_df["Status"].isin(filter_status)]
    st.dataframe(filtered, use_container_width=True, height=400)

    csv_out = results_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download full report (.csv)",
        data=csv_out,
        file_name="reference_check_report.csv",
        mime="text/csv",
        use_container_width=True
    )
