import os
import json
from typing import Dict, Any, List
from pathlib import Path
import requests
import pandas as pd
import streamlit as st
import base64

# ------------------ CONFIG ------------------
API_BASE = st.secrets.get("API_BASE", os.environ.get("API_BASE", "http://127.0.0.1:8000"))
API_KEY  = st.secrets.get("API_KEY",  os.environ.get("API_KEY",  ""))  # optional
HEADERS  = {"Content-Type": "application/json", **({"X-API-Key": API_KEY} if API_KEY else {})}
# --------------------------------------------

# Globals populated from schema["lists"]["countyTownships"]
COUNTY_OPTIONS: List[str] = []
TOWNSHIPS_BY_COUNTY: Dict[str, List[str]] = {}

def _init_county_lists_from_schema(schema: Dict[str, Any]) -> None:
    """Build COUNTY_OPTIONS and TOWNSHIPS_BY_COUNTY from schema['lists']['countyTownships']."""
    global COUNTY_OPTIONS, TOWNSHIPS_BY_COUNTY
    COUNTY_OPTIONS = []
    TOWNSHIPS_BY_COUNTY = {}

    lists = schema.get("lists") or {}
    pairs = lists.get("countyTownships") or []
    if not isinstance(pairs, list):
        return

    # Build sets, then sort for stable UI
    counties_set = set()
    mapping = {}
    for r in pairs:
        c = str((r.get("county") or "")).strip()
        t = str((r.get("township") or "")).strip()
        if not c or not t:
            continue
        counties_set.add(c)
        mapping.setdefault(c, set()).add(t)

    COUNTY_OPTIONS = sorted(counties_set)
    TOWNSHIPS_BY_COUNTY = {c: sorted(ts) for c, ts in mapping.items()}

def logo_img_tag(width=220) -> str:
    logo_path = Path(__file__).parent / "assets" / "5lakes_logo.jpg"
    if logo_path.exists():
        b64 = base64.b64encode(logo_path.read_bytes()).decode("utf-8")
        return f"<img src='data:image/jpeg;base64,{b64}' alt='5 Lakes Energy Logo' style='width:{width}px; border-radius:5px;'>"
    # fallback placeholder
    return f"<div style='width:{width}px;height:{int(width*0.45)}px;background:#eee;color:#666;display:flex;align-items:center;justify-content:center;border-radius:5px;'>Logo</div>"

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

st.set_page_config(page_title="R-STEP Calculator", layout="wide")
apply_custom_style()

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

def _selectbox_with_placeholder(label: str, options: List[str], key: str, helptext: str | None, current_value: Any):
    """
    Utility: add '— select —' placeholder and compute index from current_value.
    current_value may be None or not in options, which yields index 0.
    """
    opts = ["— select —"] + options
    cur = "" if current_value is None else str(current_value)
    try:
        idx = opts.index(cur)
    except ValueError:
        idx = 0
    return st.selectbox(label, options=opts, index=idx, key=key, help=helptext)

def render_field(row, key_prefix: str, current_value):
    """Show Description as label, but use Name as key."""
    t = (row.get("Type") or "string").lower()
    name_key = row["Name"]
    label_text = (row.get("Description") or name_key).strip()
    label = f"{label_text}{' *' if row.get('Required') else ''}"
    helptext = _get_help(row)
    ev = row.get("EnumValues")

    # ---- SPECIAL CASES: county/township (use schema lists, ignore EnumValues) ----
    if name_key == "county" and COUNTY_OPTIONS:
        # county select (no default)
        return _selectbox_with_placeholder(
            label=label,
            options=COUNTY_OPTIONS,
            key=f"{key_prefix}:{name_key}",
            helptext=helptext,
            current_value=st.session_state.get(f"{key_prefix}:{name_key}")
        )

    if name_key == "township" and TOWNSHIPS_BY_COUNTY:
        # townships filtered by the currently selected county (global scope)
        selected_county = st.session_state.get("global:county")
        towns = TOWNSHIPS_BY_COUNTY.get(selected_county, [])
        # If current township is invalid for this county, clear it
        cur_key = f"{key_prefix}:{name_key}"
        cur_val = st.session_state.get(cur_key)
        if cur_val and str(cur_val) not in towns:
            st.session_state[cur_key] = ""
            cur_val = ""
        return _selectbox_with_placeholder(
            label=label,
            options=towns,
            key=cur_key,
            helptext=helptext,
            current_value=cur_val
        )

    # ---- DEFAULT CASES (existing behavior) ----
    # dropdown (generic EnumValues)
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

    # numbers
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
                <p style='margin:0; color:#3CB043; font-weight:500;'>Reliable Energy Siting through Technical Engagement and Planning</p>
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

    # Initialize county/township lists from schema
    _init_county_lists_from_schema(schema)

    calculators: List[Dict[str, Any]] = schema.get("calculators", [])
    globals_rows: List[Dict[str, Any]] = schema.get("globals", {}).get("inputs", [])
    global_names = {r["Name"] for r in globals_rows}
    label_map = build_label_map(schema)

    # Build county -> [township] map from schema.lists
    ct_pairs = (schema.get("lists", {}) or {}).get("countyTownships", []) or []
    county_to_townships = {}
    for p in ct_pairs:
        c = str(p.get("county", "")).strip()
        t = str(p.get("township", "")).strip()
        if c and t:
            county_to_townships.setdefault(c, []).append(t)
    
    # Sort for nice UI
    for k in county_to_townships:
        county_to_townships[k] = sorted(set(county_to_townships[k]))
    all_counties = sorted(county_to_townships.keys())


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

    # --- two-column layout
    left, right = st.columns([6, 6])

    with left:
        # Globals
        st.header("Global Inputs")
        gcols = st.columns(2)
        globals_vals: Dict[str, Any] = {}

        # We’ll render county/township specially; everything else via render_field
        # Pull current session values (if any) to keep continuity
        current_county = st.session_state.get("global:county", "")
        current_township = st.session_state.get("global:township", "")

        for i, row in enumerate(globals_rows):
            name = row.get("Name")
            with gcols[i % 2]:
                if name == "county":
                    # County select with a leading blank option
                    options = ["— select —"] + all_counties
                    try:
                        idx = options.index(current_county) if current_county in options else 0
                    except Exception:
                        idx = 0
                    sel = st.selectbox(
                        (row.get("Description") or "county"),
                        options=options,
                        index=idx,
                        key="global:county",
                        help=_get_help(row),
                    )
                    # Normalize blank choice to "" in globals
                    globals_vals["county"] = "" if sel == "— select —" else sel

                    # If county changed and current township no longer valid, clear it
                    if globals_vals["county"] and current_township and current_township not in county_to_townships.get(globals_vals["county"], []):
                        st.session_state["global:township"] = ""
                        current_township = ""

                elif name == "township":
                    # Township options depend on selected county
                    c = st.session_state.get("global:county", "")
                    t_options = county_to_townships.get(c, []) if c else []
                
                    # Fallback to EnumValues if mapping is empty (optional; keep if you used this before)
                    if not t_options:
                        for r_ in globals_rows:
                            if r_.get("Name") == "township" and isinstance(r_.get("EnumValues"), list):
                                t_options = sorted(str(x).strip() for x in r_["EnumValues"] if str(x).strip())
                                break
                
                    options = ["— select —"] + t_options
                
                    # IMPORTANT: sanitize session state BEFORE rendering the selectbox
                    cur_key = "global:township"
                    cur_val = st.session_state.get(cur_key, "")
                    if cur_val not in options:
                        # if the old value is invalid for the current county, clear it
                        st.session_state[cur_key] = "— select —"
                        cur_val = "— select —"
                
                    try:
                        idx = options.index(cur_val)
                    except ValueError:
                        idx = 0
                
                    sel = st.selectbox(
                        (row.get("Description") or "township"),
                        options=options,
                        index=idx,
                        key=cur_key,
                        help=_get_help(row),
                    )
                    globals_vals["township"] = "" if sel == "— select —" else sel

                else:
                    # All other globals use your existing generic renderer
                    globals_vals[name] = render_field(row, key_prefix="global", current_value=None)

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
            st.write("DEBUG setback keys:", list(results.get("Setback", {}).keys()))
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
                    df = pd.DataFrame(v["rows"], columns=v["columns"])
                    # CSV (raw numeric, no formatting)
                    csv_bytes = df.to_csv(index=False).encode("utf-8")
                    st.caption(header)
                    st.download_button(
                        label="Download annualized data (CSV)",
                        data=csv_bytes,
                        file_name=f"{cid}_{name}.csv",
                        mime="text/csv",
                        use_container_width=True,
                        key=f"dl:{cid}:{name}",
                    )
                    # Per-table preview toggle
                    preview_key = f"pv:{cid}:{name}"
                    if st.checkbox("Preview annualized data", key=preview_key):
                        with st.expander(f"{header} — preview", expanded=True):
                            max_rows = 6
                            df_preview = df.head(max_rows).applymap(format_number)
                            st.dataframe(df_preview, use_container_width=True)

if __name__ == "__main__":
    main()
