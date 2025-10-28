import os
import json
import time
import threading
import uuid
from typing import Dict, Any, List
from pathlib import Path

import requests
import pandas as pd
import streamlit as st

# ------------------ CONFIG ------------------
API_BASE = st.secrets.get("API_BASE", os.environ.get("API_BASE", "http://127.0.0.1:8000"))
API_KEY  = st.secrets.get("API_KEY",  os.environ.get("API_KEY",  ""))  # optional
HEADERS  = {"Content-Type": "application/json", **({"X-API-Key": API_KEY} if API_KEY else {})}
# --------------------------------------------

# ---------- Styling ----------
def apply_custom_style():
    st.markdown("""
    <style>
    html, body, [class*="css"]  {
        font-family: 'Segoe UI', sans-serif;
        color: #003366;
    }
    h1, h2, h3 {
        color: #0073C2;
        font-weight: 700;
    }
    div.stButton > button:first-child {
        background-color: #3CB043;
        color: white;
        border-radius: 8px;
        padding: 0.5em 1.5em;
        font-weight: 600;
        border: none;
    }
    div.stButton > button:first-child:hover {
        background-color: #2C8A35;
    }
    .stDataFrame table { border: 1px solid #0073C2; }
    section[data-testid="stSidebar"] { background-color: #F2FAF2; }
    hr { border: 1px solid #0073C2; }
    </style>
    """, unsafe_allow_html=True)

def overlay_css():
    st.markdown("""
    <style>
    .calc-overlay {
        position: fixed;
        right: 18px;
        bottom: 18px;
        z-index: 9999;
        background: rgba(0, 51, 102, 0.92); /* dark blue */
        color: #fff;
        padding: 12px 16px;
        border-radius: 10px;
        box-shadow: 0 8px 20px rgba(0,0,0,0.2);
        font-weight: 600;
        font-size: 14px;
    }
    .calc-overlay small { display:block; opacity: 0.85; font-weight: 400; }
    </style>
    """, unsafe_allow_html=True)

st.set_page_config(page_title="R-STEP Calculator", layout="wide")
apply_custom_style()
overlay_css()

# ---------- Data helpers ----------
@st.cache_data(show_spinner=False)
def load_schema() -> Dict[str, Any]:
    r = requests.get(f"{API_BASE}/schema", timeout=30)
    r.raise_for_status()
    return r.json()

def _to_float(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None

def _stringify_options(ev):
    return [str(o) for o in (ev or [])]

def render_field(row, key_prefix: str, current_value):
    """
    Show Description as the label, but use Name for the widget key.
    """
    t = (row.get("Type") or "string").lower()
    name_key = row["Name"]
    label_text = (row.get("Description") or name_key).strip()
    label = f"{label_text}{' *' if row.get('Required') else ''}"
    helptext = row.get("Help")
    ev = row.get("EnumValues")

    # enums -> selectbox
    if isinstance(ev, list) and len(ev) > 0:
        options = _stringify_options(ev)
        cur = "" if current_value is None else str(current_value)
        try:
            idx = options.index(cur)
        except ValueError:
            idx = 0
        return st.selectbox(
            label, options=options, index=idx if 0 <= idx < len(options) else 0,
            key=f"{key_prefix}:{name_key}", help=helptext,
        )

    # numbers / percentages -> number_input (float-safe)
    if t in ("number", "percentage"):
        step = 0.01 if t == "percentage" else 1.0
        val = _to_float(current_value)
        if val is None:
            val = _to_float(row.get("Default")) or 0.0
        minv = _to_float(row.get("Min"))
        maxv = _to_float(row.get("Max"))
        return st.number_input(
            label, value=float(val), step=float(step),
            min_value=minv, max_value=maxv,
            key=f"{key_prefix}:{name_key}", help=helptext,
        )

    # strings
    val = "" if current_value is None else str(current_value)
    return st.text_input(label, value=val, key=f"{key_prefix}:{name_key}", help=helptext)

def format_number(x):
    if x is None or x == "":
        return ""
    if isinstance(x, (int, float)):
        return f"{x:,.0f}"
    try:
        return f"{float(x):,.0f}"
    except Exception:
        return x

def build_label_map(schema) -> Dict[str, Dict[str, str]]:
    """Map calcId -> { outputName -> Label (fallback to Name) }"""
    mapping: Dict[str, Dict[str, str]] = {}
    for c in schema.get("calculators", []):
        by_name = {}
        for r in c.get("outputs", []):
            nm = (r.get("Name") or "").strip()
            if not nm:
                continue
            lbl = (r.get("Label") or "").strip() or nm
            by_name[nm] = lbl
        mapping[c["id"]] = by_name
    return mapping

# ---------- Background request worker ----------
def _calc_thread(payload, headers, base):
    """Runs in a background thread; writes results or error into session_state."""
    try:
        r = requests.post(f"{base}/calculate", headers=headers,
                          data=json.dumps(payload), timeout=300)
        if not r.ok:
            st.session_state["last_error"] = f"API error {r.status_code}: {r.text}"
            st.session_state["last_results"] = None
        else:
            data = r.json()
            st.session_state["last_results"] = data.get("results", data)
            st.session_state["last_error"] = None
    except Exception as e:
        st.session_state["last_error"] = f"Request failed: {e}"
        st.session_state["last_results"] = None
    finally:
        st.session_state["inflight"] = False
        st.session_state["finished_ts"] = time.time()

# ---------- App ----------
def main():
    # Session defaults
    st.session_state.setdefault("inflight", False)
    st.session_state.setdefault("started_ts", None)
    st.session_state.setdefault("finished_ts", None)
    st.session_state.setdefault("req_id", None)
    st.session_state.setdefault("last_results", None)
    st.session_state.setdefault("last_error", None)

    # (Simple) periodic re-run while inflight to update overlay timer
    if st.session_state.get("inflight"):
        time.sleep(1)
        st.experimental_rerun()

    # Header with logo
    logo_path = Path("assets/5lakes_logo.jpg")
    cols_top = st.columns([1, 3])
    with cols_top[0]:
        if logo_path.exists():
            st.image(str(logo_path), width=220)
        else:
            st.write(" ")
    with cols_top[1]:
        st.title("R-STEP Calculator")

    # Load schema (cached)
    try:
        schema = load_schema()
    except Exception as e:
        st.error(f"Failed to load schema from {API_BASE}: {e}")
        st.stop()

    calculators: List[Dict[str, Any]] = schema.get("calculators", [])
    globals_rows: List[Dict[str, Any]] = schema.get("globals", {}).get("inputs", [])
    global_names = {r["Name"] for r in globals_rows}
    label_map = build_label_map(schema)

    # Sidebar: connection + calc selection
    with st.sidebar:
        if st.button("↻ Refresh schema"):
            load_schema.clear()   # clears the @st.cache_data
            st.experimental_rerun()
        st.subheader("Connection")
        st.write(f"API: `{API_BASE}`")
        st.caption("Auth: X-API-Key enabled" if API_KEY else "No API key set (public).")
        # Optional: queue status
        try:
            qs = requests.get(f"{API_BASE}/queue/status", timeout=5).json()
            st.caption(f"Queue: {qs.get('queued', 0)} waiting")
        except Exception:
            st.caption("Queue: n/a")

        st.divider()
        st.subheader("Calculators")
        all_ids = [c["id"] for c in calculators]
        selected = st.multiselect("Select calculators", options=all_ids, default=all_ids)

    # Two-column layout: inputs left, actions+results right
    left, right = st.columns([1, 1])

    # Globals (left)
    with left:
        st.header("Global Inputs")
        cols = st.columns(2)
        globals_vals: Dict[str, Any] = {}
        for i, row in enumerate(globals_rows):
            with cols[i % 2]:
                globals_vals[row["Name"]] = render_field(row, key_prefix="global", current_value=None)

        # Per-calculator inputs (hiding duplicates of globals)
        for c in calculators:
            if c["id"] not in selected:
                continue
            st.subheader(f"{c['id']} — Inputs")
            rows = [r for r in (c.get("inputs") or []) if r["Name"] not in global_names]
            if not rows:
                st.caption("No inputs for this calculator.")
                continue
            cols_in = st.columns(2)
            for i, row in enumerate(rows):
                with cols_in[i % 2]:
                    val = st.session_state.get(f"calc:{c['id']}:{row['Name']}")
                    _ = render_field(row, key_prefix=f"calc:{c['id']}", current_value=val)

    # Build payload
    overrides: Dict[str, Dict[str, Any]] = {}
    for c in calculators:
        if c["id"] not in selected:
            continue
        per = {}
        for row in (c.get("inputs") or []):
            nm = row["Name"]
            if nm in global_names:
                continue  # globals will drive these
            key = f"calc:{c['id']}:{nm}"
            if key in st.session_state:
                per[nm] = st.session_state[key]
        if per:
            overrides[c["id"]] = per

    payload = {
        "selected_calculators": selected,
        "globals": globals_vals,
        "overrides": overrides
    }

    # Helper to start threaded request
    def start_calculation(payload):
        if st.session_state.get("inflight"):
            return  # already running
        st.session_state["inflight"] = True
        st.session_state["started_ts"] = time.time()
        st.session_state["finished_ts"] = None
        st.session_state["req_id"] = str(uuid.uuid4())
        st.session_state["last_results"] = None
        st.session_state["last_error"] = None
        t = threading.Thread(
            target=_calc_thread,
            args=(payload, HEADERS, API_BASE),
            daemon=True
        )
        t.start()

    # Actions + request preview (right)
    with right:
        st.header("Actions")
        if st.button("Calculate", type="primary", disabled=st.session_state.get("inflight", False)):
            start_calculation(payload)

        with st.expander("Payload Preview", expanded=False):
            st.code(json.dumps(payload, indent=2))

        # Floating overlay while inflight (seconds + queue)
        if st.session_state.get("inflight"):
            # Try live queue size
            queue_caption = ""
            try:
                qs = requests.get(f"{API_BASE}/queue/status", timeout=5).json()
                queue_caption = f" • Queue: {qs.get('queued', 0)} waiting"
            except Exception:
                pass

            elapsed = int(time.time() - (st.session_state.get("started_ts") or time.time()))
            st.markdown(
                f"""
                <div class="calc-overlay">
                    Calculating… {elapsed}s elapsed{queue_caption}
                    <small>Your job is running; the timer updates about once per second.</small>
                </div>
                """,
                unsafe_allow_html=True
            )

        st.divider()
        st.header("Results")

        if st.session_state.get("last_error"):
            st.error(st.session_state["last_error"])

        results = st.session_state.get("last_results")
        if not results:
            st.caption("No results yet.")
            return

        # Render per calculator (scalars first, tables collapsed)
        for cid, block in results.items():
            st.subheader(f"{cid}")
            scalars, arrays = [], []
            for name, val in (block or {}).items():
                if val is None or not isinstance(val, dict) or "columns" not in val or "rows" not in val:
                    label = label_map.get(cid, {}).get(name, name)
                    scalars.append({"Metric": label, "Value": val})
                else:
                    arrays.append((name, val))

            if scalars:
                df = pd.DataFrame(scalars)
                df["Value"] = df["Value"].map(format_number)
                st.dataframe(df, use_container_width=True)

            for name, v in arrays:
                header = label_map.get(cid, {}).get(name, v.get("label") or name)
                with st.expander(header, expanded=False):
                    df = pd.DataFrame(v["rows"], columns=v["columns"])
                    df = df.applymap(format_number)
                    st.dataframe(df, use_container_width=True)

        if st.session_state.get("finished_ts") and st.session_state.get("started_ts"):
            total = int(st.session_state["finished_ts"] - st.session_state["started_ts"])
            st.caption(f"Completed in {total}s • Request ID: {st.session_state.get('req_id')}")

if __name__ == "__main__":
    main()
