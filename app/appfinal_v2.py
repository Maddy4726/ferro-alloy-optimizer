import streamlit as st
import pandas as pd
from pathlib import Path
from datetime import datetime
from copy import deepcopy
import sqlite3
from optimize import read_ferro_alloy_chem, run_optimizer, append_to_sqlite
import warnings
import oracledb
import logging
from pathlib import Path
from datetime import datetime

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

        status_row = df_status[df_status["CURR_HEAT_NO"] == heat_number].head(1)
        if status_row.empty:
            return None

        material_row = df_material[df_material["HEATNO"] == heat_number].head(1)
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


def get_data_from_online_db(heat_number: str, addition_type: str) -> pd.Series:
    """
    Fetch heat data from Oracle DB without using CONVERTER_MATERIAL.
    Only status and analysis tables are used.
    """
    import oracledb
    import pandas as pd
    import streamlit as st

    try:
        with oracledb.connect(
            user="C##MCKINSEY_CONVERTER",
            password="admin123",
            dsn="10.145.11.151:1521/PLANT"
        ) as oracle_conn:

            # --- Read required tables only ---
            df_status = pd.read_sql_query(
                "SELECT * FROM CONVERTER_STATUS_M", oracle_conn
            )

            df_analysis = pd.read_sql_query(
                "SELECT * FROM SMS3_ANALYSIS1", oracle_conn
            )

            # Convert date column
            if "TEST_DT" in df_analysis.columns:
                df_analysis["TEST_DT"] = pd.to_datetime(df_analysis["TEST_DT"])

            # --- Filter latest status row ---
            status_row = df_status[df_status["CURR_HEAT_NO"] == heat_number]

            if status_row.empty:
                return None

            status_row = status_row.tail(1)

            # --- Filter analysis table ---
            analysis_subset = df_analysis[df_analysis["SID1"] == heat_number].copy()

            if addition_type == "Bulk":
                analysis_subset = analysis_subset[
                    analysis_subset["SID2"].astype(str).str[1] == "B"
                ]

            elif addition_type == "Trim":
                analysis_subset = analysis_subset[
                    analysis_subset["SID2"].astype(str).str.startswith("LF")
                ]

            analysis_row = analysis_subset.sort_values(
                "TEST_DT", ascending=False
            ).head(1)

            if analysis_row.empty:
                return None

            # --- Merge status and analysis ---
            final_df = pd.merge(
                status_row,
                analysis_row,
                left_on="CURR_HEAT_NO",
                right_on="SID1",
                how="left"
            )

            return final_df.iloc[0]

    except Exception as e:
        st.error(f"Oracle fetch error: {e}")
        return None


# def upsert_fealloy_results(
#     heat_id: str,
#     optimal_materials: dict,
#     grade: str,
#     table_name: str,
#     convert_id=None,
#     lf_id=None 
# ):
#     import oracledb

#     try:
#         conn = oracledb.connect(
#             user="C##MCKINSEY_CONVERTER",
#             password="admin123",
#             dsn="10.145.11.151:1521/PLANT"
#         )

#         cursor = conn.cursor()

#         if table_name == "SMS3_BOF_MODEL_FEALLOY":
#             id_column = "CONVERT_ID"
#             id_value = convert_id
#         else:
#             id_column = "LF_ID"
#             id_value = lf_id

#         # --- Timestamp (Oracle DATE) ---
#         run_time = datetime.now()

#         # --- Safe mapping ---
#         def get_val(keys):
#             for k in keys:
#                 if k in optimal_materials:
#                     return float(optimal_materials[k])
#             return 0.0

#         # --- Rounded values ---
#         fesi = round(get_val(["Fe-Si", "Fe-Si\n0-50mm"]))
#         simn = round(get_val(["Si-Mn\n10-50mm"]))
#         petcoke = round(get_val(["P.Coke"]))
#         simn_lophos = round(get_val(["SiMn LP\n25_50mm"]))

#         # --- Dynamic MERGE query ---
#         merge_query = f"""
#             MERGE INTO {table_name} tgt
#             USING (
#                 SELECT 
#                     :heat_id AS HEAT_ID,
#                     :fesi AS FESI,
#                     :simn AS SIMN,
#                     :petcoke AS PETROCOKE,
#                     :simn_lophos AS SIMN_LOPHOS,
#                     :quality AS QUALITY,
#                     :convert_id AS CONVERT_ID,
#                     :run_time AS DATETIME
#                 FROM dual
#             ) src
#             ON (tgt.HEAT_ID = src.HEAT_ID)

#             WHEN MATCHED THEN
#                 UPDATE SET
#                     tgt.FESI = src.FESI,
#                     tgt.SIMN = src.SIMN,
#                     tgt.PETROCOKE = src.PETROCOKE,
#                     tgt.SIMN_LOPHOS = src.SIMN_LOPHOS,
#                     tgt.QUALITY = src.QUALITY,
#                     tgt.CONVERT_ID = src.CONVERT_ID, 
#                     tgt.DATETIME = src.DATETIME

#             WHEN NOT MATCHED THEN
#                 INSERT (HEAT_ID, FESI, SIMN, PETROCOKE, SIMN_LOPHOS, QUALITY, CONVERT_ID, DATETIME)
#                 VALUES (
#                     src.HEAT_ID,
#                     src.FESI,
#                     src.SIMN,
#                     src.PETROCOKE,
#                     src.SIMN_LOPHOS,
#                     src.QUALITY,
#                     src.CONVERT_ID,
#                     src.DATETIME
#                 )
#         """

#         cursor.execute(merge_query, {
#             "heat_id": str(heat_id),
#             "fesi": fesi,
#             "simn": simn,
#             "petcoke": petcoke,
#             "simn_lophos": simn_lophos,
#             "quality": str(grade),
#             "convert_id": str(convert_id),
#             "run_time": run_time
#         })

#         conn.commit()
#         cursor.close()
#         conn.close()

#         print(f"✅ UPSERT successful into {table_name}")

#     except Exception as e:
#         print(f"❌ Oracle UPSERT Error ({table_name}):", e)


#########################################

# def upsert_fealloy_results(
#     heat_id: str,
#     optimal_materials: dict,
#     grade: str,
#     table_name: str,
#     convert_id=None,
#     lf_id=None
# ):
#     try:
#         conn = oracledb.connect(
#             user="C##MCKINSEY_CONVERTER",
#             password="admin123",
#             dsn="10.145.11.151:1521/PLANT"
#         )
#         cursor = conn.cursor()

#         # --- Determine correct ID column and value ---
#         if table_name == "SMS3_BOF_MODEL_FEALLOY":
#             id_column = "CONVERT_ID"  # or "CONVERTOR_ID" if your DB uses that
#             id_value = convert_id
#         else:
#             id_column = "LF_ID"
#             id_value = lf_id

#         # --- Timestamp ---
#         run_time = datetime.now()

#         # --- Safe mapping helper ---
#         def get_val(keys):
#             for k in keys:
#                 if k in optimal_materials:
#                     return float(optimal_materials[k])
#             return 0.0

#         # --- Rounded values ---
#         fesi = round(get_val(["Fe-Si", "Fe-Si\n0-50mm"]))
#         simn = round(get_val(["Si-Mn\n10-50mm"]))
#         petcoke = round(get_val(["P.Coke"]))
#         simn_lophos = round(get_val(["SiMn LP\n25_50mm"]))

#         # --- Dynamic MERGE query ---
#         merge_query = f"""
#             MERGE INTO {table_name} tgt
#             USING (
#                 SELECT 
#                     :heat_id AS HEAT_ID,
#                     :fesi AS FESI,
#                     :simn AS SIMN,
#                     :petcoke AS PETROCOKE,
#                     :simn_lophos AS SIMN_LOPHOS,
#                     :quality AS QUALITY,
#                     :id_value AS {id_column},
#                     :run_time AS DATETIME
#                 FROM dual
#             ) src
#             ON (tgt.HEAT_ID = src.HEAT_ID)
#             WHEN MATCHED THEN
#                 UPDATE SET
#                     tgt.FESI = src.FESI,
#                     tgt.SIMN = src.SIMN,
#                     tgt.PETROCOKE = src.PETROCOKE,
#                     tgt.SIMN_LOPHOS = src.SIMN_LOPHOS,
#                     tgt.QUALITY = src.QUALITY,
#                     tgt.{id_column} = src.{id_column},
#                     tgt.DATETIME = src.DATETIME
#             WHEN NOT MATCHED THEN
#                 INSERT (HEAT_ID, FESI, SIMN, PETROCOKE, SIMN_LOPHOS, QUALITY, {id_column}, DATETIME)
#                 VALUES (src.HEAT_ID, src.FESI, src.SIMN, src.PETROCOKE, src.SIMN_LOPHOS, src.QUALITY, src.{id_column}, src.DATETIME)
#         """

#         # --- Execute query with dynamic ID value ---
#         cursor.execute(merge_query, {
#             "heat_id": str(heat_id),
#             "fesi": fesi,
#             "simn": simn,
#             "petcoke": petcoke,
#             "simn_lophos": simn_lophos,
#             "quality": str(grade),
#             "id_value": str(id_value),
#             "run_time": run_time
#         })

#         conn.commit()
#         cursor.close()
#         conn.close()

#         print(f"✅ UPSERT successful into {table_name}")

#     except Exception as e:
#         print(f"❌ Oracle UPSERT Error ({table_name}):", e)



#########################################

from datetime import datetime
import oracledb


# def get_next_batch_id(conn, table_name):
#     cursor = conn.cursor()

#     query = f"SELECT NVL(MAX(BATCH), 0) + 1 FROM {table_name}"
#     cursor.execute(query)

#     next_batch = cursor.fetchone()[0]
#     cursor.close()

#     return int(next_batch)


def get_next_batch_id(conn, table_name):
    cursor = conn.cursor()

    query = f"""
        SELECT NVL(MAX(TO_NUMBER(BATCH)), 0) + 1
        FROM {table_name}
    """

    cursor.execute(query)

    next_batch = cursor.fetchone()[0]

    cursor.close()

    return int(next_batch)

# def upsert_fealloy_results(heat_id, optimal_materials, grade, table_name, convert_id=None, lf_id=None):
#     try:
#         conn = oracledb.connect(
#             user="C##MCKINSEY_CONVERTER",
#             password="admin123",
#             dsn="10.145.11.151:1521/PLANT"
#         )
#         cursor = conn.cursor()

#         if table_name == "SMS3_BOF_MODEL_FEALLOY":
#             id_column = "CONVERT_ID"
#             if convert_id in (None, '', ' '):
#                 raise ValueError("convert_id cannot be empty")
#             id_value = str(convert_id)  # use parameter, not final_input_dict
#         else:
#             id_column = "LF_ID"
#             if lf_id in (None, '', ' '):
#                 raise ValueError("lf_id cannot be empty")
#             id_value = int(lf_id)      # use parameter

#         # --- Timestamp ---
#         run_time = datetime.now()

#         # --- Safe mapping helper ---
#         def get_val(keys):
#             for k in keys:
#                 if k in optimal_materials:
#                     return float(optimal_materials[k])
#             return 0.0

#         # --- Rounded values ---
#         fesi = round(get_val(["Fe-Si", "Fe-Si\n0-50mm"]))
#         simn = round(get_val(["Si-Mn\n10-50mm"]))
#         petcoke = round(get_val(["P.Coke"]))
#         simn_lophos = round(get_val(["SiMn LP\n25_50mm"]))

#         # --- MERGE query ---
#         # merge_query = f"""
#         # MERGE INTO {table_name} tgt
#         # USING (SELECT :heat_id AS HEAT_ID FROM dual) src
#         # ON (tgt.HEAT_ID = src.HEAT_ID)
#         # WHEN MATCHED THEN
#         #     UPDATE SET
#         #         tgt.FESI = :fesi,
#         #         tgt.SIMN = :simn,
#         #         tgt.PETROCOKE = :petcoke,
#         #         tgt.SIMN_LOPHOS = :simn_lophos,
#         #         tgt.QUALITY = :grade,
#         #         tgt.{id_column} = :id_value,
#         #         tgt.DATETIME = :run_time
#         # WHEN NOT MATCHED THEN
#         #     INSERT (HEAT_ID, FESI, SIMN, PETROCOKE, SIMN_LOPHOS, QUALITY, {id_column}, DATETIME)
#         #     VALUES (:heat_id, :fesi, :simn, :petcoke, :simn_lophos, :grade, :id_value, :run_time)
#         # """

#         merge_query = f"""
#         MERGE INTO {table_name} tgt
#         USING (SELECT 1 AS dummy FROM dual) src
#         ON (tgt.HEAT_ID = :heat_id)
#         WHEN MATCHED THEN
#             UPDATE SET
#                 FESI = :fesi,
#                 SIMN = :simn,
#                 PETROCOKE = :petcoke,
#                 SIMN_LOPHOS = :simn_lophos,
#                 QUALITY = :grade,
#                 {id_column} = :id_value,
#                 DATETIME = :run_time
#         WHEN NOT MATCHED THEN
#             INSERT (HEAT_ID, FESI, SIMN, PETROCOKE, SIMN_LOPHOS, QUALITY, {id_column}, DATETIME)
#             VALUES (:heat_id, :fesi, :simn, :petcoke, :simn_lophos, :grade, :id_value, :run_time)
#         """

#         # --- Bind correct types ---
#         # bind_vars = {
#         #     "heat_id": str(heat_id),
#         #     "fesi": fesi,
#         #     "simn": simn,
#         #     "petcoke": petcoke,
#         #     "simn_lophos": simn_lophos,
#         #     "grade": str(grade),
#         #     "id_value": id_value,  # pass number or string directly
#         #     "run_time": run_time
#         # }

#         # cursor.execute(merge_query, bind_vars)


#         bind_vars = {
#             "heat_id": str(heat_id),
#             "fesi": fesi,
#             "simn": simn,
#             "petcoke": petcoke,
#             "simn_lophos": simn_lophos,
#             "grade": str(grade),
#             "id_value": str(convert_id) if table_name == "SMS3_BOF_MODEL_FEALLOY" else int(lf_id),
#             "run_time": run_time
#         }
#         cursor.execute(merge_query, bind_vars)




#         conn.commit()
#         cursor.close()
#         conn.close()

#         print(f"✅ UPSERT successful into {table_name}")

#     except Exception as e:
#         print(f"❌ Oracle UPSERT Error ({table_name}):", e)




def upsert_fealloy_results(
    heat_id,
    optimal_materials,
    grade,
    table_name,
    convert_id=None,
    lf_id=None
):
    try:
        conn = oracledb.connect(
            user="C##MCKINSEY_CONVERTER",
            password="admin123",
            dsn="10.145.11.151:1521/PLANT"
        )
        cursor = conn.cursor()

        # --- Timestamp ---
        run_time = datetime.now()

        # --- Helper ---
        def get_val(keys):
            for k in keys:
                if k in optimal_materials:
                    return float(optimal_materials[k])
            return 0.0

        # --- Values ---
        fesi = round(get_val(["Fe-Si", "Fe-Si\n0-50mm"]))
        simn = round(get_val(["Si-Mn\n10-50mm"]))
        petcoke = round(get_val(["P.Coke"]))
        simn_lophos = round(get_val(["SiMn LP\n25_50mm"]))

        # ==============================
        # 🔵 BULK → KEEP MERGE
        # ==============================
        if table_name == "SMS3_BOF_MODEL_FEALLOY":

            if convert_id in (None, '', ' '):
                raise ValueError("convert_id cannot be empty")

            merge_query = f"""
            MERGE INTO {table_name} tgt
            USING (SELECT 1 FROM dual) src
            ON (tgt.HEAT_ID = :heat_id)
            WHEN MATCHED THEN
                UPDATE SET
                    FESI = :fesi,
                    SIMN = :simn,
                    PETROCOKE = :petcoke,
                    SIMN_LOPHOS = :simn_lophos,
                    QUALITY = :grade,
                    CONVERT_ID = :convert_id,
                    DATETIME = :run_time
            WHEN NOT MATCHED THEN
                INSERT (HEAT_ID, FESI, SIMN, PETROCOKE, SIMN_LOPHOS, QUALITY, CONVERT_ID, DATETIME)
                VALUES (:heat_id, :fesi, :simn, :petcoke, :simn_lophos, :grade, :convert_id, :run_time)
            """

            cursor.execute(merge_query, {
                "heat_id": str(heat_id),
                "fesi": fesi,
                "simn": simn,
                "petcoke": petcoke,
                "simn_lophos": simn_lophos,
                "grade": str(grade),
                "convert_id": str(convert_id),
                "run_time": run_time
            })

        # ==============================
        # 🟢 TRIM → INSERT with BATCH
        # ==============================
        else:

            if lf_id in (None, '', ' '):
                raise ValueError("lf_id cannot be empty")

            # ✅ Get next batch
            batch_id = get_next_batch_id(conn, table_name)

            insert_query = f"""
            INSERT INTO {table_name}
            (BATCH, HEAT_ID, FESI, SIMN, PETROCOKE, SIMN_LOPHOS, QUALITY, LF_ID, DATETIME)
            VALUES
            (:batch, :heat_id, :fesi, :simn, :petcoke, :simn_lophos, :grade, :lf_id, :run_time)
            """

            cursor.execute(insert_query, {
                "batch": batch_id,
                "heat_id": str(heat_id),
                "fesi": fesi,
                "simn": simn,
                "petcoke": petcoke,
                "simn_lophos": simn_lophos,
                "grade": str(grade),
                "lf_id": int(lf_id),
                "run_time": run_time
            })

        conn.commit()
        cursor.close()
        conn.close()

        print(f"✅ Data written to {table_name}")

    except Exception as e:
        print(f"❌ Oracle Error ({table_name}):", e)


def fetch_heat_data(heat_number: str, addition_type: str) -> pd.Series:
    """
    Fetch heat data exclusively from the online Oracle database.
    """
    if not heat_number:
        return None

    # Always fetch from the online database
    return get_data_from_online_db(heat_number, addition_type)

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


# def get_default_alloys():
#     grade = st.session_state.get("current_grade", "SAIL SEQR")
#     add_type = st.session_state.get("addition_type_state", "Bulk")
#     if add_type == "Trim":
#         return DEFAULT_ALLOYS_TRIM.get(grade, [])
#     return DEFAULT_ALLOYS_BULK.get(grade, [])

def get_default_alloys():
    grade = st.session_state.get("current_grade", "SAIL SEQR")
    add_type = st.session_state.get("addition_type_state", "Bulk")

    if add_type == "Trim":
        alloys = DEFAULT_ALLOYS_TRIM.get(grade, []).copy()

        # ✅ RULE: For R260 in TRIM → use normal SiMn instead of LP
        if grade == "IRST12/09R-260-60-E1":
            if "SiMn LP\n25_50mm" in alloys:
                alloys.remove("SiMn LP\n25_50mm")
            if "Si-Mn\n10-50mm" not in alloys:
                alloys.append("Si-Mn\n10-50mm")

        return alloys

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



    ################# OLD CODE ###############

    # cn_lf_options, cn_lf_label = (
    #     (["A", "B", "C"], "Converter")
    #     if sidebar_input["addition_type"] == "Bulk"
    #     else (["1", "2", "3"], "LF")
    # )

    # # --- Safely determine CN/LF selectbox index ---
    # sid2_value = defaults.get("SID2", "")
    # sid2_str = str(sid2_value) if pd.notnull(sid2_value) else ""  # convert NaN/float to string
    # pos = 2 if sidebar_input["addition_type"] == "Trim" else 0

    # try:
    #     idx = cn_lf_options.index(sid2_str[pos])
    # except (ValueError, IndexError):
    #     idx = 0
    # sidebar_input["CN/LF"] = st.selectbox(
    #     cn_lf_label, options=cn_lf_options, index=idx, on_change=reset_main_panel
    # )

    ################################################

    # Determine CN/LF options and label
    cn_lf_options, cn_lf_label = (
        (["A", "B", "C"], "Converter")
        if sidebar_input["addition_type"] == "Bulk"
        else (["1", "2", "3"], "LF")
    )

    # Extract the proper CN/LF value from SID2
    sid2_value = defaults.get("SID2", "")  # e.g., 'LF2-2' or 'B' etc.
    if sidebar_input["addition_type"] == "Bulk":
        # For converter, take last char if SID2 has dash, else full
        default_cn_lf = sid2_value.split('-')[-1] if sid2_value else cn_lf_options[0]
    else:
        # For LF, take last char after dash
        default_cn_lf = sid2_value.split('-')[-1] if sid2_value else cn_lf_options[0]

    # Determine default index safely
    try:
        idx = cn_lf_options.index(default_cn_lf)
    except ValueError:
        idx = 0

    # Create selectbox with correct first-run value
    sidebar_input["CN/LF"] = st.selectbox(
        cn_lf_label,
        options=cn_lf_options,
        index=idx,
        on_change=reset_main_panel
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
            # fetched_data = fetch_heat_data(
            #     heat_number_input, sidebar_input["addition_type"], USE_ONLINE_DB
            # )
            fetched_data = fetch_heat_data(
                    heat_number_input, sidebar_input["addition_type"]
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
            st.subheader("📊 Chemical Analysis (%)")
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

    ############# Changed for Non FA additions #############

    with col_right:
        if defaults.get("addition_type") == 'Trim':
            with st.container(border=True):
                st.subheader("Non-FA Adds (kg)")

                ui_state["Sum_Lime_Added"] = (
                    st.number_input("Lime (kg)", value=0.0) / 1000
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
    print(f"THIS IS MY FINAL INPUT: {final_input_dict}")

    input_df = pd.DataFrame(final_input_dict, index=[0])
    input_df_processed = read_ferro_alloy_chem(input_df.copy(), home_dir)

    # --- Override for R260 Trim ---
    addition_type = final_input_dict.get("addition_type")
    grade = final_input_dict.get("Plan Details_Grade")
    if addition_type == "Trim" and grade == "IRST12/09R-260-60-E1":
        if "SiMn LP\n25_50mm" in list_material_options:
            list_material_options.remove("SiMn LP\n25_50mm")
        if "Si-Mn\n10-50mm" not in list_material_options:
            list_material_options.append("Si-Mn\n10-50mm")

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

        # --- Rounded optimal material outputs ---
        optimal_cols = [
            c for c in detailed_results_df.columns if c.startswith("Optimal_")
        ]
        optimal_materials = {
            c.replace("Optimal_", ""): round(row_df[c] * 1000)
            for c in optimal_cols
            if row_df[c] > 0
        }
        st.session_state.optimizer_results = optimal_materials

        # # --- connect to Oracle ---
        # conn = oracledb.connect(
        #     user="C##MCKINSEY_CONVERTER",
        #     password="admin123",
        #     dsn="10.145.11.151:1521/PLANT"
        # )
        # cursor = conn.cursor()

        # # --- fetch the real CN/LF for this heat ---
        # cursor.execute("""
        #     SELECT CN_LF
        #     FROM FERROALLOY_LOOKUP
        #     WHERE HEAT_ID = :heat_id
        # """, {"heat_id": final_input_dict["Heat Number"]})

        # row = cursor.fetchone()
        # if not row:
        #     raise ValueError(f"No CN/LF found for heat {final_input_dict['Heat Number']}")

        # real_cn_lf = row[0]  # get the actual value

        # cursor.close()
        # conn.close()

        # final_input_dict["CN/LF"] = sidebar_input["CN/LF"]
        # --- UPSERT into Oracle based on addition type ---
        if addition_type == "Bulk":
            upsert_fealloy_results(
                heat_id=final_input_dict["Heat Number"],
                optimal_materials=optimal_materials,
                grade=grade,
                table_name="SMS3_BOF_MODEL_FEALLOY",
                convert_id=final_input_dict["CN/LF"],
            )
        elif addition_type == "Trim":
            upsert_fealloy_results(
                heat_id=final_input_dict["Heat Number"],
                optimal_materials=optimal_materials,
                grade=grade,
                table_name="SMS3_TRIM_MODEL_FEALLOY",
                lf_id=final_input_dict["CN/LF"],
            )


        # --- Expected chemistry display ---
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

    # --- Append all runs to local SQLite ---
    append_to_sqlite(
        input_df,
        detailed_results_df,
        db_path=home_dir / "db" / "ferroalloy_records.db",
    )
    st.rerun()