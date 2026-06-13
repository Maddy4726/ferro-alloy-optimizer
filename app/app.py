import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime
from copy import deepcopy
import sqlite3
from optimize import read_ferro_alloy_chem, run_optimizer, append_to_sqlite
import warnings
import oracledb

warnings.filterwarnings("ignore")

# --- Initial Setup ---
home_dir = Path(__file__).resolve().parent.parent


def safe_float(val, default=0.0):
    """Safely converts DB values to float, handling None or NaN."""
    try:
        return default if pd.isna(val) or val is None else float(val)
    except (ValueError, TypeError):
        return default


# --- Database Connectors ---
def get_data_from_local_db(heat_number: str, addition_type: str) -> pd.Series:
    db_path = home_dir / "db" / "local_test.db"
    if not db_path.exists():
        st.error(f"Local database file not found at: {db_path}")
        return None
    try:
        with sqlite3.connect(db_path) as con:
            df_status = pd.read_sql_query(
                "SELECT *, rowid AS explicit_rowid FROM CONVERTER_STATUS_M", con
            )
            df_material = pd.read_sql_query(
                "SELECT *, rowid AS explicit_rowid FROM CONVERTER_MATERIAL", con
            )
            df_analysis = pd.read_sql_query(
                "SELECT * FROM SMS3_ANALYSIS1", con, parse_dates=["TEST_DT"]
            )

        status_row = (
            df_status[df_status["CURR_HEAT_NO"] == heat_number]
            .sort_values("explicit_rowid", ascending=False)
            .head(1)
        )
        if status_row.empty:
            return None
        material_row = (
            df_material[df_material["HEATNO"] == heat_number]
            .sort_values("explicit_rowid", ascending=False)
            .head(1)
        )
        if material_row.empty:
            return None

        analysis_subset = df_analysis[df_analysis["SID1"] == heat_number].copy()
        if addition_type == "Bulk":
            analysis_subset = analysis_subset[
                analysis_subset["SID2"].astype(str).str[1] == "B"
            ]
        elif addition_type == "Trim":
            analysis_subset = analysis_subset[
                analysis_subset["SID2"].astype(str).str.startswith("LF")
            ]

        analysis_row = analysis_subset.sort_values("TEST_DT", ascending=False).head(1)
        if analysis_row.empty:
            return None

        final_df = pd.merge(
            status_row,
            analysis_row,
            left_on="CURR_HEAT_NO",
            right_on="SID1",
            how="left",
        )
        final_df = pd.merge(
            final_df,
            material_row,
            left_on="CURR_HEAT_NO",
            right_on="HEATNO",
            how="left",
        )
        return final_df.loc[:, ~final_df.columns.duplicated()].iloc[0]
    except Exception as e:
        st.error(f"Local Database Error: {e}")
        return None


# TODO: Test this function throughly with live data
def get_data_from_online_db(heat_number: str, addition_type: str) -> pd.Series:
    """
    Connects to the live Oracle database, fetches data, and performs filtering
    and joining to return a single row for the specified heat.
    """
    try:
        # Establish connection using secrets
        with oracledb.connect(
            # user=st.secrets["oracle"]["user"],
            # password=st.secrets["oracle"]["password"],
            # dsn=st.secrets["oracle"]["dsn"],
            user= "C##MCKINSEY_CONVERTER",
            password= "admin123",
            dsn= "10.145.11.151:1521/PLANT" ,
        ) as oracle_conn:

            # --- Step 1: Read base tables from Oracle into pandas ---

            df_status = pd.read_sql_query(
                """
                SELECT a.*, ROW_NUMBER() OVER (ORDER BY (SELECT 0 FROM DUAL)) as explicit_rowid 
                FROM CONVERTER_STATUS_M a
            """,
                oracle_conn,
            )

            df_material = pd.read_sql_query(
                """
                SELECT a.*, ROW_NUMBER() OVER (ORDER BY (SELECT 0 FROM DUAL)) as explicit_rowid 
                FROM CONVERTER_MATERIAL a
            """,
                oracle_conn,
            )

            # For the analysis table, ensure the date column is parsed correctly.
            df_analysis = pd.read_sql_query("SELECT * FROM SMS3_ANALYSIS1", oracle_conn)
            # Convert date column manually after fetching, as read_sql doesn't have parse_dates for oracle
            if "TEST_DT" in df_analysis.columns:
                df_analysis["TEST_DT"] = pd.to_datetime(df_analysis["TEST_DT"])

            # --- Step 2: Filter each DataFrame (same logic as the local function) ---

            status_row = (
                df_status[df_status["CURR_HEAT_NO"] == heat_number]
                .sort_values("explicit_rowid", ascending=False)
                .head(1)
            )
            if status_row.empty:
                return None

            material_row = (
                df_material[df_material["HEATNO"] == heat_number]
                .sort_values("explicit_rowid", ascending=False)
                .head(1)
            )
            if material_row.empty:
                return None

            analysis_subset = df_analysis[df_analysis["SID1"] == heat_number].copy()
            if addition_type == "Bulk":
                analysis_subset = analysis_subset[
                    analysis_subset["SID2"].astype(str).str[1] == "B"
                ]
            elif addition_type == "Trim":
                analysis_subset = analysis_subset[
                    analysis_subset["SID2"].astype(str).str.startswith("LF")
                ]

            analysis_row = analysis_subset.sort_values("TEST_DT", ascending=False).head(
                1
            )
            if analysis_row.empty:
                return None

            # --- Step 3: Join the filtered data (same logic as the local function) ---

            final_df = status_row.copy()
            final_df = pd.merge(
                final_df,
                analysis_row,
                left_on="CURR_HEAT_NO",
                right_on="SID1",
                how="left",
            )
            final_df = pd.merge(
                final_df,
                material_row,
                left_on="CURR_HEAT_NO",
                right_on="HEATNO",
                how="left",
            )
            # Drop any remaining duplicate columns
            final_df = final_df.loc[:, ~final_df.columns.duplicated()]
            return final_df.iloc[0]

    except oracledb.Error as e:
        st.error(f"Oracle Database Connection Error: {e}")
        return None
    except KeyError as e:
        # This will catch errors if a column name is missing from the fetched data
        st.error(
            f"A required column was not found in the online database. Missing key: {e}"
        )
        return None
    except Exception as e:
        st.error(
            f"An unexpected error occurred while fetching data from the online DB: {e}"
        )
        return None


def fetch_heat_data(
    heat_number: str, addition_type: str, use_online: bool
) -> pd.Series:
    if not heat_number:
        return None
    get_data_from_online_db = (
        lambda hn, at: st.warning("Online database connection is not yet implemented.")
        or None
    )
    return (
        get_data_from_online_db(heat_number, addition_type)
        if use_online
        else get_data_from_local_db(heat_number, addition_type)
    )


# --- App Configuration & Callbacks ---
st.set_page_config(page_title="Ferro Alloy Optimiser", layout="wide")

st.markdown(
    """
    <style>
        .block-container { padding-top: 3.5rem !important; padding-bottom: 1rem !important; } 
        .stNumberInput { margin-bottom: -10px; }
        h3 { margin-bottom: 0px !important; padding-bottom: 5px !important; font-size: 1.2rem !important;}
    </style>
""",
    unsafe_allow_html=True,
)

# --- Constants & Lists ---
col_heat_no, heat_wt = "Heat Number", 165
list_fa_allowed = [
    "Si-Mn\n10-50mm",
    "SiMn LP\n25_50mm",
    "P.Coke",
    "Fe-Si",
    "Fe-Si\n0-50mm",
    "Ferro\nVanadium",
    # "Fe-Mn\nHi-C",
]

DEFAULT_ALLOYS_BULK = {
    "SAIL SEQR": ["Si-Mn\n10-50mm", "P.Coke", "Fe-Si", "Fe-Si\n0-50mm"],
    "IRST12/09R-260-60-E1": [
        "SiMn LP\n25_50mm",
        "P.Coke",
        "Fe-Si",
        "Fe-Si\n0-50mm",
        "Ferro\nVanadium",
    ],
    "IS2062E250BR-TMT25": ["Si-Mn\n10-50mm", "P.Coke", "Fe-Si", "Fe-Si\n0-50mm"],
    "IS 7887 GR-2": ["Si-Mn\n10-50mm", "P.Coke", "Fe-Si", "Fe-Si\n0-50mm"],
}

DEFAULT_ALLOYS_TRIM = {
    "SAIL SEQR": ["Si-Mn\n10-50mm", "P.Coke", "Fe-Si", "Fe-Si\n0-50mm"],
    "IRST12/09R-260-60-E1": [
        "SiMn LP\n25_50mm",
        "P.Coke",
        "Fe-Si",
        "Fe-Si\n0-50mm",
        "Ferro\nVanadium",
    ],
    "IS2062E250BR-TMT25": ["Si-Mn\n10-50mm", "P.Coke", "Fe-Si", "Fe-Si\n0-50mm"],
    "IS 7887 GR-2": ["Si-Mn\n10-50mm", "P.Coke", "Fe-Si", "Fe-Si\n0-50mm"],
}


def get_default_alloys():
    grade = st.session_state.get("current_grade", "SAIL SEQR")
    add_type = st.session_state.get("addition_type_state", "Bulk")
    if add_type == "Trim":
        return DEFAULT_ALLOYS_TRIM.get(grade, [])
    return DEFAULT_ALLOYS_BULK.get(grade, [])


def update_checkboxes():
    defaults = get_default_alloys()
    for item in list_fa_allowed:
        st.session_state[f"checkbox_{item}"] = item in defaults


def reset_all():
    keys_to_delete = [
        "show_main_panel",
        "run_pressed",
        "current_grade",
        "defaults",
        "optimizer_results",
        "expected_chemistry",
    ]
    for key in list(st.session_state.keys()):
        if key.startswith("checkbox_") or key in keys_to_delete:
            del st.session_state[key]
    initialize_session_state()


def reset_main_panel():
    (
        st.session_state.show_main_panel,
        st.session_state.run_pressed,
        st.session_state.optimizer_results,
        st.session_state.expected_chemistry,
        st.session_state.defaults,
    ) = (False, False, {}, {}, {})


def grade_changed():
    st.session_state.current_grade = st.session_state.grade_selectbox
    update_checkboxes()
    reset_main_panel()


def addition_type_changed():
    update_checkboxes()
    reset_main_panel()


def initialize_session_state():
    if "show_main_panel" not in st.session_state:
        st.session_state.show_main_panel = False
    if "run_pressed" not in st.session_state:
        st.session_state.run_pressed = False
    if "current_grade" not in st.session_state:
        st.session_state.current_grade = "SAIL SEQR"
    if "defaults" not in st.session_state:
        st.session_state.defaults = {}
    if "optimizer_results" not in st.session_state:
        st.session_state.optimizer_results = {}
    if "expected_chemistry" not in st.session_state:
        st.session_state.expected_chemistry = {}
    if "addition_type_state" not in st.session_state:
        st.session_state.addition_type_state = "Bulk"


initialize_session_state()

# --- Sidebar UI ---
with st.sidebar:
    st.button(
        "🔄 Reset All", on_click=reset_all, use_container_width=True, type="secondary"
    )
    st.divider()

    defaults, sidebar_input, USE_ONLINE_DB = (
        st.session_state.get("defaults", {}),
        {},
        True,  # TODO: Set this to True once the online data pull function is added
    )

    st.subheader("Step 1: Heat Details")
    sidebar_input["addition_type"] = st.radio(
        "Addition Type",
        ["Bulk", "Trim"],
        key="addition_type_state",
        on_change=addition_type_changed,
        horizontal=True,
    )
    heat_number_input = st.text_input("Heat Number", on_change=reset_main_panel)
    sidebar_input[col_heat_no] = heat_number_input

    grade_options = list(DEFAULT_ALLOYS_BULK.keys())
    sidebar_input["Plan Details_Grade"] = st.selectbox(
        "Grade",
        options=grade_options,
        index=grade_options.index(st.session_state.current_grade),
        key="grade_selectbox",
        on_change=grade_changed,
    )
    sidebar_input["Charging Details_HM Wt"] = st.number_input(
        "Heat Wt (Tons)", min_value=155, value=heat_wt, on_change=reset_main_panel
    )

    cn_lf_options, cn_lf_label = (
        (["A", "B", "C"], "Converter")
        if sidebar_input["addition_type"] == "Bulk"
        else (["1", "2", "3"], "LF")
    )
    try:
        idx = cn_lf_options.index(
            defaults.get("SID2", " ")[
                2 if sidebar_input["addition_type"] == "Trim" else 0
            ]
        )
    except (ValueError, IndexError):
        idx = 0
    sidebar_input["CN/LF"] = st.selectbox(
        cn_lf_label, options=cn_lf_options, index=idx, on_change=reset_main_panel
    )

    with st.expander("**Step 2: Select Materials**", expanded=False):
        list_material_options = []
        cols = st.columns(2)
        for i, item in enumerate(list_fa_allowed):
            with cols[i % 2]:
                if f"checkbox_{item}" not in st.session_state:
                    st.session_state[f"checkbox_{item}"] = item in get_default_alloys()
                if st.checkbox(
                    item, key=f"checkbox_{item}", on_change=reset_main_panel
                ):
                    list_material_options.append(item)

    if heat_number_input and list_material_options:
        if st.button(
            "**📥 Load Heat Properties**", type="primary", use_container_width=True
        ):
            fetched_data = fetch_heat_data(
                heat_number_input, sidebar_input["addition_type"], USE_ONLINE_DB
            )
            if fetched_data is not None:
                fetched_data[fetched_data.index.str.contains("OXYGEN", na=False)] = (
                    fetched_data[
                        fetched_data.index.str.contains("OXYGEN", na=False)
                    ].fillna(0)
                )
                st.session_state.defaults = {**fetched_data.to_dict(), **sidebar_input}
                (
                    st.session_state.show_main_panel,
                    st.session_state.run_pressed,
                    st.session_state.optimizer_results,
                    st.session_state.expected_chemistry,
                ) = (True, False, {}, {})
                st.toast("Data loaded successfully!", icon="✅")
                st.rerun()
            else:
                st.warning(f"Heat '{heat_number_input}' not found.")
    else:
        st.info("Enter Heat # & select materials.")

# --- MAIN PANEL ---
if not st.session_state.show_main_panel:
    st.info("⬅️ **Load Heat Properties from the sidebar to begin.**")
else:
    ui_state = {}
    defaults = st.session_state.get("defaults", {})

    if st.session_state.optimizer_results:
        with st.container(border=True):
            st.success("✅ Optimization Successful")
            res_col1, res_col2 = st.columns(2)

            with res_col1:
                st.markdown("**📦 Optimal Additions (kg)**")
                results = st.session_state.optimizer_results
                mat_cols = st.columns(len(results) if len(results) > 0 else 1)
                for i, (mat, amt) in enumerate(results.items()):
                    with mat_cols[i]:
                        clean_name = mat.replace("\n", " ")
                        st.markdown(
                            f"""
                        <div style="background-color: #e0f2fe; border: 1px solid #7dd3fc; padding: 15px 10px; border-radius: 8px; text-align: center;">
                            <div style="font-size: 1.15rem; font-weight: 600; color: #334155; margin-bottom: 5px;">{clean_name}</div>
                            <div style="font-size: 1.5rem; font-weight: 700; color: #0f172a; line-height: 1;">{amt:,.0f}</div>
                        </div>
                        """,
                            unsafe_allow_html=True,
                        )

            with res_col2:
                st.markdown("**🧪 Expected Chemistry (%)**")
                expected_chem = st.session_state.get("expected_chemistry", {})
                display_chem = {
                    k: v
                    for k, v in expected_chem.items()
                    if k in ["C", "Mn", "Si", "V"]
                }
                if display_chem:
                    chem_cols = st.columns(len(display_chem))
                    for i, (el, val) in enumerate(display_chem.items()):
                        with chem_cols[i]:
                            val_str = f"{val:.3f}" if pd.notnull(val) else "N/A"
                            st.markdown(
                                f"""
                            <div style="background-color: #f0fdf4; border: 1px solid #86efac; padding: 15px 10px; border-radius: 8px; text-align: center;">
                                <div style="font-size: 1.15rem; font-weight: 600; color: #334155; margin-bottom: 5px;">{el}</div>
                                <div style="font-size: 1.5rem; font-weight: 700; color: #0f172a; line-height: 1;">{val_str}</div>
                            </div>
                            """,
                                unsafe_allow_html=True,
                            )

    elif st.session_state.run_pressed:
        st.error("⚠️ No optimal combination found or solution was infeasible.")
    else:
        st.info(
            "Inputs loaded. Edit below if needed, then click 'Run Optimizer' to calculate results."
        )

    st.write("")

    col_left, col_right = st.columns([1.2, 1])

    with col_left:
        with st.container(border=True):
            st.subheader("📊 Bath Analysis (%)")
            b1, b2, b3 = st.columns(3)
            with b1:
                ui_state["C_Initial_Proportion"] = st.number_input(
                    "Carbon (C)",
                    value=safe_float(defaults.get("C_VALUE")),
                    format="%.3f",
                )
                ui_state["P_Initial_Proportion"] = st.number_input(
                    "Phosphorus (P)",
                    value=safe_float(defaults.get("P_VALUE")),
                    format="%.3f",
                )
            with b2:
                ui_state["Mn_Initial_Proportion"] = st.number_input(
                    "Manganese (Mn)",
                    value=safe_float(defaults.get("MN_VALUE")),
                    format="%.3f",
                )
                ui_state["S_Initial_Proportion"] = st.number_input(
                    "Sulphur (S)",
                    value=safe_float(defaults.get("S_VALUE")),
                    format="%.3f",
                )
            with b3:
                ui_state["Si_Initial_Proportion"] = st.number_input(
                    "Silicon (Si)",
                    value=safe_float(defaults.get("SI_VALUE")),
                    format="%.3f",
                )
                ui_state["V_Initial_Proportion"] = st.number_input(
                    "Vanadium (V)",
                    value=safe_float(defaults.get("V_VALUE")),
                    format="%.3f",
                )

    with col_right:
        with st.container(border=True):
            st.subheader("🪨 Non-FA Adds (kg)")
            if defaults.get("addition_type") == "Bulk":
                nf1, nf2 = st.columns(2)
                with nf1:
                    ui_state["Sum_Iron_Ore"] = (
                        st.number_input(
                            "Iron Ore", value=safe_float(defaults.get("IRNORE_WT"))
                        )
                        / 1000
                    )
                    ui_state["Sum_Lime_Added"] = (
                        st.number_input(
                            "Lime", value=safe_float(defaults.get("BLIME_WT"))
                        )
                        / 1000
                    )
                    ui_state["Sum_Al_Added"] = (
                        st.number_input(
                            "AL/Bauxite", value=safe_float(defaults.get("BAUXITE_WT"))
                        )
                        / 1000
                    )
                with nf2:
                    ui_state["Sum_Scrap_Steel"] = (
                        st.number_input(
                            "Scrap", value=safe_float(defaults.get("SCRAP_WT"))
                        )
                        / 1000
                    )
                    ui_state["Sum_Dolo_Added"] = (
                        st.number_input(
                            "Dolo", value=safe_float(defaults.get("BDOLO_WT"))
                        )
                        / 1000
                    )
            else:
                ui_state["Sum_Lime_Added"] = (
                    st.number_input("Lime Added", value=0.0) / 1000
                )

    if defaults.get("addition_type") == "Bulk":
        with st.container(border=True):
            st.subheader("⚙️ Operations")
            op1, op2, op3, op4 = st.columns(4)
            with op1:
                ui_state["TD_TEMP"] = st.number_input(
                    "TD Temp (°C)",
                    value=int(safe_float(defaults.get("TD_TEMP"))),
                    step=1,
                )
            with op2:
                ui_state["Original_OXYGEN"] = st.number_input(
                    "O2 Blown", value=safe_float(defaults.get("BL_OXYGEN_1"))
                )
            with op3:
                reblow_sum = sum(
                    safe_float(defaults.get(f"BL_OXYGEN_{i}")) for i in range(2, 6)
                )
                ui_state["Reblow_OXYGEN"] = st.number_input(
                    "Reblow O2", value=reblow_sum
                )
                ui_state["FLAG_REBLOW"] = (
                    1 if ui_state.get("Reblow_OXYGEN", 0) >= 500 else 0
                )
            with op4:
                ui_state["Life_CN"] = st.number_input(
                    "Converter Life",
                    value=int(safe_float(defaults.get("LINING_LIFE"))),
                    step=1,
                )

    st.write("")

    if list_material_options and st.button(
        "▶️ RUN OPTIMIZER", use_container_width=True, type="primary"
    ):
        final_input_dict = {**deepcopy(st.session_state.defaults), **ui_state}
        final_input_dict.update(
            {"Date": datetime.now().date(), "Tap_time": datetime.now()}
        )

        input_df = pd.DataFrame(final_input_dict, index=[0])
        input_df_processed = read_ferro_alloy_chem(input_df.copy(), home_dir)

        with st.spinner("Optimizing..."):
            detailed_results_df = run_optimizer(
                input_df_processed,
                list_material_options,
                final_input_dict["addition_type"],
                home_dir,
            )

        if (
            not detailed_results_df.empty
            and detailed_results_df.iloc[0]["Status"] == "Optimal"
        ):
            row_df = detailed_results_df.iloc[0]

            optimal_cols = [
                c for c in detailed_results_df.columns if c.startswith("Optimal_")
            ]
            optimal_materials = {
                c.replace("Optimal_", ""): (row_df[c] * 1000)
                for c in optimal_cols
                if row_df[c] > 0
            }
            st.session_state.optimizer_results = optimal_materials

            expected_chem = {}
            for el in ["C", "Mn", "Si", "P", "S", "V"]:
                col_name = f"Expected_{el}_%"
                if col_name in row_df and pd.notnull(row_df[col_name]):
                    expected_chem[el] = row_df[col_name]
            st.session_state.expected_chemistry = expected_chem

        else:
            st.session_state.optimizer_results = {}
            st.session_state.expected_chemistry = {}

        st.session_state.run_pressed = True
        append_to_sqlite(
            input_df,
            detailed_results_df,
            db_path=home_dir / "db" / "ferroalloy_records.db",
        )
        st.rerun()
