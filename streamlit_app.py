import os
import json
from typing import Dict, Any, List
import requests
from pathlib import Path
import pandas as pd
import streamlit as st
import base64

# ------------------ CONFIG ------------------
API_BASE = st.secrets.get("API_BASE", os.environ.get("API_BASE", "http://127.0.0.1:8000"))
API_KEY  = st.secrets.get("API_KEY",  os.environ.get("API_KEY",  ""))  # optional
HEADERS  = {"Content-Type": "application/json", **({"X-API-Key": API_KEY} if API_KEY else {})}
# --------------------------------------------

# --- define this first ---
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
    span[title] svg {
      opacity: 0.8;
    }
    span[title]:hover svg {
      opacity: 1;
    }
    </style>
    """, unsafe_allow_html=True)

st.set_page_config(page_title="R-STEP Calculator", layout="wide")
apply_custom_style()  # <-- now safe

@st.cache_data(show_spinner=False)
def logo_img_tag(width=220) -> str:
    logo_path = Path(__file__).parent / "assets" / "5lakes_logo.jpg"
    if logo_path.exists():
        b64 = base64.b64encode(logo_path.read_bytes()).decode("utf-8")
        return f"<img src='data:image/jpeg;base64,{b64}' alt='5 Lakes Energy Logo' style='width:{width}px; border-radius:5px;'>"
    # fallback placeholder
    return f"<div style='width:{width}px;height:{int(width*0.45)}px;background:#eee;color:#666;display:flex;align-items:center;justify-content:center;border-radius:5px;'>Logo</div>"
    
def load_schema() -> Dict[str, Any]:
    r = requests.get(f"{API_BASE}/schema", timeout=30)
    r.raise_for_status()
    return r.json()

def number_step(row_type: str):
    t = (row_type or "string").lower()
    if t in ("number", "percentage"):
        return 0.01
    return None

def _to_float(x):
    if x is None or x == "":
        return None
    try:
        return float(x)
    except Exception:
        return None

def _stringify_options(ev):
    return [str(o) for o in (ev or [])]

def _get_help(row):
    raw = (
        row.get("HelpText")
        or row.get("Help")
        or row.get("Help Text")
        or row.get("Hint")
        or row.get("Tooltip")
        or row.get("Notes")
        or None
    )
    if isinstance(raw, str):
        raw = raw.strip()
    return raw or None

def render_field(row, key_prefix: str, current_value):
    """
    Show Description as the label, but use Name for the widget key.
    """
    t = (row.get("Type") or "string").lower()
    name_key = row["Name"]
    label_text = (row.get("Description") or name_key).strip()
    label = f"{label_text}{' *' if row.get('Required') else ''}"
    helptext = _get_help(row)
    ev = row.get("EnumValues")

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

def main():
    st.markdown(
        f"""
        <div style='display:flex; align-items:center; gap:20px; margin-bottom:1rem;'>
            {logo_img_tag(220)}
            <div>
                <h1 style='margin:0; color:#0073C2;'>R-STEP Calculator</h1>
                <p style='margin:0; color:#3CB043; font-weight:500;'>Renewable Siting Tool for Energy Planning</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    try:
        schema = load_schema()
    except Exception as e:
        st.error(f"Failed to load schema from {API_BASE}: {e}")
        st.stop()

    calculators: List[Dict[str, Any]] = schema.get("calculators", [])
    globals_rows: List[Dict[str, Any]] = schema.get("globals", {}).get("inputs", [])
    global_names = {r["Name"] for r in globals_rows}
    label_map = build_label_map(schema)

    with st.sidebar:
        st.subheader("Module")
        all_ids = [c["id"] for c in calculators]
        selected = st.multiselect("Select module(s)", options=all_ids, default=all_ids)
        st.divider()
        st.subheader("Instructions")
        st.write("Select which modules you'd like to use in the dropdown above.")
        st.write("Once selected, enter in the applicable global inputs and module-specific inputs. If you are unsure on what an input is, toggle the question mark icon to the right of the input. Once all inputs are entered, click the 'Calculate' button.")
        st.write("Results will be displayed on the right hand side of the screen. If you receive an error, please wait a few seconds and click the 'Calculate' button again")
        st.divider()
        st.subheader("Connection")
        st.write(f"API: `{API_BASE}`")
        st.caption("Auth: X-API-Key enabled" if API_KEY else "No API key set (public).")
        st.divider()
      
    left, right = st.columns([6, 6])  # tweak ratios to taste

    with left:
        # Globals
        st.header("Global Inputs")
        gcols = st.columns(2)
        globals_vals: Dict[str, Any] = {}
        for i, row in enumerate(globals_rows):
            with gcols[i % 2]:
                globals_vals[row["Name"]] = render_field(row, key_prefix="global", current_value=None)

        # Per-calculator inputs (hiding duplicates of globals)
        for c in calculators:
            if c["id"] not in selected:
                continue
            st.subheader(f"{c['id']} — Inputs")
            rows = [r for r in (c.get("inputs") or []) if r["Name"] not in global_names]
            if not rows:
                st.caption("No inputs for this calculator.")
            else:
                icols = st.columns(2)
                for i, row in enumerate(rows):
                    with icols[i % 2]:
                        val = st.session_state.get(f"calc:{c['id']}:{row['Name']}")
                        _ = render_field(row, key_prefix=f"calc:{c['id']}", current_value=val)

        # Build payload on the left
        overrides: Dict[str, Dict[str, Any]] = {}
        for c in calculators:
            if c["id"] not in selected:
                continue
            per = {}
            for row in (c.get("inputs") or []):
                nm = row["Name"]
                if nm in global_names:
                    continue
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

        st.divider()
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Calculate", type="primary", use_container_width=True):
                try:
                    r = requests.post(f"{API_BASE}/calculate", headers=HEADERS,
                                      data=json.dumps(payload), timeout=120)
                    if not r.ok:
                        st.error(f"API error {r.status_code}: {r.text}")
                    else:
                        data = r.json()
                        st.session_state["last_results"] = data.get("results", data)
                except Exception as e:
                    st.error(f"Request failed: {e}")
        with c2:
            with st.expander("Payload Preview", expanded=False):
                st.code(json.dumps(payload, indent=2))

    # --- Results on the right
    with right:
        st.header("Results")
        results = st.session_state.get("last_results")
        if not results:
            st.caption("No results yet.")
        else:
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
                    st.caption(header)
                    df = pd.DataFrame(v["rows"], columns=v["columns"])
                    df = df.applymap(format_number)
                    st.dataframe(df, use_container_width=True)

if __name__ == "__main__":
    main()
