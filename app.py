import streamlit as st
import pandas as pd
import re
import time
from agent import verify_cell

st.set_page_config(page_title="Reference Checker", page_icon="🔍", layout="wide")

st.title("🔍 Reference Checker")
st.caption("Automatically verifies whether spreadsheet data matches the provided reference links.")

# --- Sidebar ---
with st.sidebar:
    st.header("⚙️ Settings")
    api_key = st.text_input("Gemini API Key", type="password")
    st.divider()
    st.markdown("**How to use:**")
    st.markdown("1. Enter your Gemini API Key")
    st.markdown("2. Upload a `.csv` spreadsheet")
    st.markdown("3. Choose which rows to verify")
    st.markdown("4. Click **Run Verification**")
    st.markdown("5. Click any row to see full details")
    st.markdown("6. Download the report")
    st.divider()
    st.caption("Auto-detects `[References]` columns and verifies each data ↔ source pair.")
    st.divider()
    st.markdown("**⚠️ Rate limits (free tier):**")
    st.markdown("- 10 requests / minute")
    st.markdown("- 250 requests / day")

# --- Session state init ---
for key in ["results", "running", "last_file", "queue", "total"]:
    if key not in st.session_state:
        st.session_state[key] = None if key in ["last_file"] else ([] if key in ["results", "queue"] else False if key == "running" else 0)

# --- Upload ---
uploaded = st.file_uploader("📂 Upload spreadsheet (.csv)", type=["csv"])

if not uploaded:
    st.info("Please upload a CSV file to get started.")
    st.stop()

if not api_key:
    st.warning("Please enter your Gemini API Key in the sidebar.")
    st.stop()

# Reset if new file
if st.session_state.last_file != uploaded.name:
    st.session_state.results = []
    st.session_state.queue = []
    st.session_state.running = False
    st.session_state.total = 0
    st.session_state.last_file = uploaded.name

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

st.write(f"**{len(ref_pairs)} pairs** detected (data ↔ reference):")
cols_preview = st.columns(3)
for i, (data_col, _) in enumerate(ref_pairs):
    cols_preview[i % 3].markdown(f"- `{data_col}`")

st.divider()

# --- Row selection ---
st.subheader("Select rows to verify")
run_all = st.checkbox("✅ Run all rows", value=False)

if run_all:
    start_row, end_row = 1, len(df)
    total_checks = len(df) * len(ref_pairs)
    st.info(f"All **{len(df)} rows** · {len(ref_pairs)} columns = **{total_checks} checks**")
    if total_checks > 250:
        st.warning(f"⚠️ {total_checks} checks exceeds the free tier daily limit of 250.")
else:
    col1, col2 = st.columns(2)
    with col1:
        start_row = st.number_input("Start row", min_value=1, max_value=len(df), value=1)
    with col2:
        end_row = st.number_input("End row", min_value=1, max_value=len(df), value=min(3, len(df)))
    st.caption(f"{end_row - start_row + 1} row(s) · {len(ref_pairs)} columns = **{(end_row - start_row + 1) * len(ref_pairs)} checks**")

selected_df = df.iloc[int(start_row)-1 : int(end_row)]

with st.expander("👁️ Preview selected rows"):
    st.dataframe(selected_df, use_container_width=True)

st.divider()

# --- Build queue of pending checks ---
def build_queue(selected_df, ref_pairs):
    queue = []
    for row_idx, row in selected_df.iterrows():
        org_name = str(row.get("Name", row_idx + 1))
        for data_col, ref_col in ref_pairs:
            queue.append({
                "org": org_name,
                "data_col": data_col,
                "ref_col": ref_col,
                "row": row
            })
    return queue

# --- Buttons ---
col_btn1, col_btn2 = st.columns(2)

with col_btn1:
    start_btn = st.button("🚀 Run Verification", type="primary", use_container_width=True)

with col_btn2:
    reset_btn = st.button("🔄 Reset Results", use_container_width=True)

if reset_btn:
    st.session_state.results = []
    st.session_state.queue = []
    st.session_state.running = False
    st.session_state.total = 0
    st.rerun()

if start_btn:
    # Build full queue, skip already done
    full_queue = build_queue(selected_df, ref_pairs)
    done_keys = {(r["Organization"], r["Column"]) for r in st.session_state.results}
    pending = [item for item in full_queue if (item["org"], item["data_col"]) not in done_keys]
    st.session_state.queue = pending
    st.session_state.total = len(full_queue)
    st.session_state.running = True

# --- Process queue (one rerun at a time keeps connection alive) ---
if st.session_state.running and st.session_state.queue:
    done = len(st.session_state.results)
    total = st.session_state.total
    remaining = len(st.session_state.queue)

    progress_val = done / total if total > 0 else 0
    st.progress(progress_val, text=f"Checking {done}/{total} — {remaining} remaining. **Keep this tab open!**")

    # Process next item
    item = st.session_state.queue[0]
    row = item["row"]
    org_name = item["org"]
    data_col = item["data_col"]
    ref_col = item["ref_col"]

    st.info(f"🔎 `{org_name}` — `{data_col}`")

    declared_value = str(row.get(data_col, "")).strip()
    references_raw = str(row.get(ref_col, "")).strip()
    declared_clean = re.sub(r'\s*\[Ref\d*\]', '', declared_value).strip()

    if not declared_clean or declared_clean in ("nan", "N/A", "/", ""):
        result_row = {
            "Organization": org_name, "Column": data_col,
            "Declared Value": declared_clean, "Reference": references_raw,
            "Status": "⏭️ Skipped", "Verdict": "Empty value or N/A", "Source Excerpt": ""
        }
    elif not references_raw or references_raw in ("nan", "N/A", "/", ""):
        result_row = {
            "Organization": org_name, "Column": data_col,
            "Declared Value": declared_clean, "Reference": "",
            "Status": "❓ No Reference", "Verdict": "No reference provided", "Source Excerpt": ""
        }
    else:
        result = verify_cell(
            declared_value=declared_clean,
            column_name=data_col,
            references_raw=references_raw,
            gemini_api_key=api_key
        )
        result_row = {
            "Organization": org_name, "Column": data_col,
            "Declared Value": declared_clean, "Reference": references_raw,
            "Status": result["status"], "Verdict": result["verdict"],
            "Source Excerpt": result["excerpt"]
        }

    # Save result and remove from queue
    st.session_state.results.append(result_row)
    st.session_state.queue.pop(0)

    time.sleep(0.3)

    # Continue or finish
    if st.session_state.queue:
        st.rerun()
    else:
        st.session_state.running = False
        st.rerun()

elif st.session_state.running and not st.session_state.queue:
    st.session_state.running = False

# --- Show progress bar when idle but partial ---
if not st.session_state.running and st.session_state.results and st.session_state.total > 0:
    done = len(st.session_state.results)
    total = st.session_state.total
    if done < total:
        st.warning(f"⚠️ Interrupted at {done}/{total}. Click **Run Verification** to continue from where you left off.")
        st.progress(done / total)

# --- Results ---
if st.session_state.results:
    results_df = pd.DataFrame(st.session_state.results)
    st.divider()
    st.subheader("📊 Results")

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", len(results_df))
    m2.metric("✅ Confirmed", len(results_df[results_df["Status"] == "✅ Confirmed"]))
    m3.metric("❌ Incorrect", len(results_df[results_df["Status"] == "❌ Incorrect"]))
    m4.metric("⚠️ Partial", len(results_df[results_df["Status"] == "⚠️ Partial"]))
    m5.metric("🔒 Issues", len(results_df[results_df["Status"].isin(["🔒 Inaccessible", "❓ Not Found", "❓ No Reference"])]))

    filter_status = st.multiselect(
        "Filter by status",
        options=results_df["Status"].unique().tolist(),
        default=results_df["Status"].unique().tolist()
    )
    filtered = results_df[results_df["Status"].isin(filter_status)].reset_index(drop=True)

    st.divider()

    # Row detail view
    color_map = {
        "✅ Confirmed": "🟢", "❌ Incorrect": "🔴", "⚠️ Partial": "🟡",
        "❓ Not Found": "⚪", "🔒 Inaccessible": "🔵",
        "⏭️ Skipped": "⚫", "❓ No Reference": "⚪",
    }

    for i, row in filtered.iterrows():
        icon = color_map.get(row["Status"], "⚪")
        label = f"{icon} **{row['Organization']}** · `{row['Column']}` · {row['Status']}"

        with st.expander(label, expanded=False):
            c1, c2 = st.columns(2)
            with c1:
                st.markdown("**Declared Value**")
                st.text_area("", value=row["Declared Value"], height=120, disabled=True, key=f"dv_{i}")
                st.markdown("**Reference URL(s)**")
                urls = row["Reference"].split("|")
                for url in urls:
                    url = url.strip()
                    if url.startswith("http"):
                        st.markdown(f"[🔗 {url[:80]}...]({url})" if len(url) > 80 else f"[🔗 {url}]({url})")
                    elif url:
                        st.text(url)
            with c2:
                st.markdown("**Status**")
                st.markdown(f"### {row['Status']}")
                st.markdown("**Verdict**")
                st.info(row["Verdict"] if row["Verdict"] else "—")
                st.markdown("**Source Excerpt**")
                st.success(row["Source Excerpt"] if row["Source Excerpt"] else "No excerpt available")

    st.divider()
    csv_out = results_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Download full report (.csv)",
        data=csv_out,
        file_name="reference_check_report.csv",
        mime="text/csv",
        use_container_width=True
    )
