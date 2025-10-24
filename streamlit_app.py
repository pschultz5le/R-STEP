import os
import json
from typing import Dict, Any, List
import requests
import pandas as pd
import streamlit as st

# ------------------ CONFIG ------------------
API_BASE = st.secrets.get("API_BASE", os.environ.get("API_BASE", "http://127.0.0.1:8000"))
API_KEY  = st.secrets.get("API_KEY",  os.environ.get("API_KEY",  ""))  # optional
HEADERS  = {"Content-Type": "application/json", **({"X-API-Key": API_KEY} if API_KEY else {})}
# --------------------------------------------

st.set_page_config(page_title="R-STEP Calculator", layout="wide")

@st.cache_data(show_spinner=False)
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

def render_field(row, key_prefix: str, current_value):
    """Auto-widget from schema row; returns new value or None."""
    t = (row.get("Type") or "string").lower()
    name = row.get("Description")
    label = f"{name}{' *' if row.get('Required') else ''}"
    helptext = row.get("Description") or None
    ev = row.get("EnumValues")

    # enums -> dropdown
    if isinstance(ev, list) and len(ev) > 0:
        options = ev
        # try to preserve current selection if present
        idx = 0
        if current_value in options:
            idx = options.index(current_value)
        return st.selectbox(
            label,
            options=options,
            index=idx if 0 <= idx < len(options) else 0,
            key=f"{key_prefix}:{name}",
            help=helptext,
        )

    # numbers / percentages -> number_input (all float types)
    if t in ("number", "percentage"):
        # use float everywhere to avoid StreamlitMixedNumericTypesError
        if t == "percentage":
            step = 0.1
        else:
            step = 1
        val = _to_float(current_value)        
        if val is None:
            # optional: try Default, else 0.0
            val = _to_float(row.get("Default"))
            if val is None:
                val = 0.0
        minv = _to_float(row.get("Min"))
        maxv = _to_float(row.get("Max"))

        return st.number_input(
            label,
            value=float(val),
            step=float(step),
            min_value=minv,   # ok if None
            max_value=maxv,   # ok if None
            key=f"{key_prefix}:{name}",
            help=helptext,
        )

    # strings
    val = "" if current_value is None else str(current_value)
    return st.text_input(label, value=val, key=f"{key_prefix}:{name}", help=helptext)

def format_number(x):
    """Format numbers with thousands separators; leave others as-is."""
    if x is None or x == "":
        return ""
    if isinstance(x, (int, float)):
        return f"{x:,.0f}"
    try:
        num = float(x)
        return f"{num:,.0f}"
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

def main():
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

    with st.sidebar:
        st.subheader("Connection")
        st.write(f"API: `{API_BASE}`")
        if API_KEY:
            st.write("Auth: using X-API-Key")
        else:
            st.caption("No API key set (public).")
        st.divider()
        st.subheader("Calculators")
        all_ids = [c["id"] for c in calculators]
        selected = st.multiselect("Select calculators", options=all_ids, default=all_ids)

    # Globals
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
        cols = st.columns(2)
        for i, row in enumerate(rows):
            with cols[i % 2]:
                val = st.session_state.get(f"calc:{c['id']}:{row['Name']}")
                _ = render_field(row, key_prefix=f"calc:{c['id']}", current_value=val)
                # Do NOT write to st.session_state here — the widget manages its own state.


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

    st.divider()
    c1, c2 = st.columns([1,1])
    with c1:
        if st.button("Calculate", type="primary"):
            try:
                r = requests.post(f"{API_BASE}/calculate", headers=HEADERS,
                                  data=json.dumps(payload), timeout=120)
                # Show raw response if not OK
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

    st.divider()

    # Results
    results = st.session_state.get("last_results")
    st.header("Results")
    if not results:
        st.caption("No results yet.")
        return

    # Render per calculator
    for cid, block in results.items():
        st.subheader(f"{cid}")
        scalars = []
        arrays = []

        for name, val in (block or {}).items():
            # scalar if not a dict-with-columns
            if val is None or not isinstance(val, dict) or "columns" not in val or "rows" not in val:
                label = label_map.get(cid, {}).get(name, name)
                scalars.append({"Metric": label, "Value": val})
            else:
                arrays.append((name, val))

        # scalar table
        if scalars:
            df = pd.DataFrame(scalars)
            # Format numbers with commas (no decimals); align right on Value
            df["Value"] = df["Value"].map(format_number)
            st.dataframe(df, use_container_width=True)

        # array tables
        for name, v in arrays:
            header = label_map.get(cid, {}).get(name, v.get("label") or name)
            st.caption(header)
            cols = v["columns"]
            rows = v["rows"]
            df = pd.DataFrame(rows, columns=cols)
            df = df.applymap(format_number)
            st.dataframe(df, use_container_width=True)

if __name__ == "__main__":
    main()
