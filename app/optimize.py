import pandas as pd
import numpy as np
import pulp as p
import logging
from datetime import datetime
from os.path import join
from pathlib import Path
import joblib
import sqlite3
import json
import warnings

warnings.filterwarnings("ignore")
pd.set_option("display.max_columns", 1000)
pd.set_option("display.max_rows", 1000)

home_dir = Path(__file__).resolve().parent.parent
current_dir = Path(__file__).resolve().parent
col_heat_wt = "Heat_Wt"
col_heat_no = "Heat Number"

try:
    historical = pd.read_excel(current_dir / "historical day.xlsx")
except FileNotFoundError:
    print(
        "Warning: 'historical day.xlsx' not found. Some features may use static defaults."
    )
    historical = None


def get_recoveries(input_df, home_dir):
    element_list = ["%C", "%Mn", "%Si", "%V", "%Cr", "%Ti"]
    default_bulk = {"%C": 90, "%V": 95, "%Cr": 90, "%Ti": 90}
    default_trim = {"%Mn": 85, "%V": 95}
    model_paths = {
        "%Mn": home_dir / "ml_models/MnBulkRecoveryModel_20251210.pkl",
        "%Si": home_dir / "ml_models/SiBulkRecoveryModel_20251210.pkl",
    }
    default_rec_dict = {
        "IRST12/09R-260-60-E1": {"Mn": 85, "Si": 70},
        "SAIL SEQR": {"Mn": 88, "Si": 55},
        "IS 7887 GR-2": {"Mn": 85, "Si": 55},
        "IS2062E250BR-TMT25": {"Mn": 85, "Si": 55},
    }
    mape_val_mn = 10
    mape_val_si = 10
    model_features = [
        "{target}_last_shift_avg",
        "{target}_last_shift_cn_avg",
        "Charging Details_HM Wt",
        "Mn_Initial_Proportion",
        "Si_Initial_Proportion",
        "C_Initial_Proportion",
        "P_Initial_Proportion",
        "S_Initial_Proportion",
        "Cumulative_Material_NonFerro_Wt",
        "Sum_Scrap_Steel",
        "Sum_Iron_Ore",
        "TOT_OXYGEN",
        "TD_TEMP",
        "Life_CN",
        "FLAG_REBLOW",
        "DoloProp_SiO2",
        "DoloProp_CaO",
        "DoloProp_MgO",
        "LimeProp_SiO2",
        "LimeProp_CaO",
        "LimeProp_MgO",
        "Fluxes_Reactivity",
        "HMProp_SI",
        "HMProp_MN",
        "HMProp_P",
        "HMProp_S",
    ]

    if historical is not None and len(historical) > 0:
        last_hist_row = historical.iloc[-1]
        default_model_feature_values = {
            col: last_hist_row[col]
            for col in last_hist_row.index
            if "last_shift" in col
        }
    else:
        default_model_feature_values = {
            "recovery_bulk_%_Mn_last_shift_avg": 90,
            "recovery_bulk_%_Mn_last_shift_cn_avg": 90,
            "recovery_bulk_%_Si_last_shift_avg": 65,
            "recovery_bulk_%_Si_last_shift_cn_avg": 65,
        }

    models = {}
    for el, path in model_paths.items():
        try:
            models[el] = joblib.load(path)
        except Exception as e:
            models[el] = None

    for element in element_list:
        bulk_col, trim_col = f"recovery_bulk_%_{element}", f"recovery_trim_%_{element}"
        if element in ["%Mn", "%Si"] and models.get(element):
            target = bulk_col.replace("_%_%", "_%_")
            features = [
                f.format(target=target) if "{target}" in f else f
                for f in model_features
            ]
            for f in features:
                if f not in input_df.columns and f in default_model_feature_values:
                    input_df[f] = default_model_feature_values[f]

            missing = [f for f in features if f not in input_df.columns]
            grade = input_df["Plan Details_Grade"].iloc[0]
            if missing:
                input_df[bulk_col] = default_rec_dict[grade][
                    "Mn" if element == "%Mn" else "Si"
                ]
            else:
                input_df[bulk_col] = models[element].predict(input_df[features])

            for idx, row in input_df.iterrows():
                grade = row.get("Plan Details_Grade")
                if grade in default_rec_dict:
                    el_key = "Mn" if element == "%Mn" else "Si"
                    if abs(row[bulk_col] - default_rec_dict[grade][el_key]) > (
                        mape_val_mn if element == "%Mn" else mape_val_si
                    ):
                        input_df.at[idx, bulk_col] = default_rec_dict[grade][el_key]
        else:
            input_df[bulk_col] = default_bulk.get(element, 90)
        input_df[trim_col] = default_trim.get(element, 90)
    return input_df


def read_ferro_alloy_chem(input_df, home_dir=None):
    weights = {
        "SiMn\n12-25mm": {"C": 0.02, "Mn": 0.6, "Si": 0.15, "V": 0, "Cr": 0, "Ti": 0},
        "SiMn LP\n25_50mm": {
            "C": 0.02,
            "Mn": 0.6,
            "Si": 0.15,
            "V": 0,
            "Cr": 0,
            "Ti": 0,
        },
        "Si-Mn\n25-50mm": {"C": 0.02, "Mn": 0.6, "Si": 0.15, "V": 0, "Cr": 0, "Ti": 0},
        "Si-Mn\n10-50mm": {"C": 0.02, "Mn": 0.6, "Si": 0.15, "V": 0, "Cr": 0, "Ti": 0},
        "Fe-Si": {"C": 0.0015, "Mn": 0, "Si": 0.7, "V": 0, "Cr": 0, "Ti": 0},
        "Fe-Si\n0-50mm": {"C": 0.0015, "Mn": 0, "Si": 0.7, "V": 0, "Cr": 0, "Ti": 0},
        "Ferro\nVanadium": {"C": 0, "Mn": 0, "Si": 0, "V": 0.5, "Cr": 0, "Ti": 0},
        "Fe-Mn\nHi-C": {"C": 0.075, "Mn": 0.7, "Si": 0.015, "V": 0, "Cr": 0, "Ti": 0},
        "Fe-Mn\nLo-C": {"C": 0.001, "Mn": 0.7, "Si": 0.05, "V": 0, "Cr": 0, "Ti": 0},
        "Fe-Mn \nMb": {"C": 0, "Mn": 0.01, "Si": 0.01, "V": 0, "Cr": 0, "Ti": 0},
        "Fe-Cr\nHi-C": {"C": 0.08, "Mn": 0, "Si": 0.04, "V": 0, "Cr": 0.7, "Ti": 0},
        "Fe-Cr\nLo-C": {"C": 0.02, "Mn": 0, "Si": 0.04, "V": 0, "Cr": 0.7, "Ti": 0},
        "Ferro\nTitanium": {
            "C": 0.0025,
            "Mn": 0,
            "Si": 0.02,
            "V": 0,
            "Cr": 0,
            "Ti": 0.65,
        },
        "Ferro\nNiobium": {"C": 0.0025, "Mn": 0, "Si": 0.03, "V": 0, "Cr": 0, "Ti": 0},
        "P.Coke": {"C": 0.95, "Mn": 0, "Si": 0, "V": 0, "Cr": 0, "Ti": 0},
    }
    elements = ["C", "Mn", "Si", "V", "Cr", "Ti"]
    for alloy, comp in weights.items():
        for element in elements:
            input_df[f"FAProp_{alloy}_{element}"] = comp[element]
    return input_df


def read_fluxes_chemistry_and_reactivity(home_dir, input_df):
    try:
        chemistry_df = pd.read_excel(
            join(home_dir, "data", "fluxes_chem_properties_RCL_lookup.xlsx")
        )
        reactivity_df = pd.read_excel(
            join(home_dir, "data", "fluxes_reactivity_RCL_lookup.xlsx")
        )
    except FileNotFoundError as e:
        print(f"Warning: Flux data file not found: {e}.")
        return input_df

    flux_chem_lime_df = chemistry_df[chemistry_df["Flux"] == "Lime"].copy()
    flux_chem_lime_df.columns = [
        f"LimeProp_{col}" if col != "Date" else col for col in flux_chem_lime_df.columns
    ]
    flux_chem_lime_df = flux_chem_lime_df[
        ["Date", "LimeProp_SiO2", "LimeProp_CaO", "LimeProp_MgO"]
    ].copy()

    flux_chem_dolo_df = chemistry_df[chemistry_df["Flux"] != "Lime"].copy()
    flux_chem_dolo_df.columns = [
        f"DoloProp_{col}" if col != "Date" else col for col in flux_chem_dolo_df.columns
    ]
    flux_chem_dolo_df = flux_chem_dolo_df[
        ["Date", "DoloProp_SiO2", "DoloProp_CaO", "DoloProp_MgO"]
    ].copy()

    month_to_number = {
        "January": 1,
        "February": 2,
        "March": 3,
        "April": 4,
        "May": 5,
        "June": 6,
        "July": 7,
        "August": 8,
        "September": 9,
        "October": 10,
        "November": 11,
        "December": 12,
    }
    reactivity_df["Date"] = pd.to_datetime(
        reactivity_df[["Year", "Month", "Day"]].assign(
            Month=lambda d: d["Month"].map(month_to_number)
        )
    )

    input_df["Date"] = pd.to_datetime(input_df["Date"])
    flux_chem_lime_df["Date"] = pd.to_datetime(flux_chem_lime_df["Date"])
    flux_chem_dolo_df["Date"] = pd.to_datetime(flux_chem_dolo_df["Date"])

    input_df.sort_values("Date", inplace=True, ignore_index=True)
    flux_chem_lime_df.sort_values("Date", inplace=True, ignore_index=True)
    flux_chem_dolo_df.sort_values("Date", inplace=True, ignore_index=True)
    reactivity_df.sort_values("Date", inplace=True, ignore_index=True)

    input_df = pd.merge_asof(
        input_df, flux_chem_dolo_df, on="Date", direction="nearest"
    )
    input_df = pd.merge_asof(
        input_df, flux_chem_lime_df, on="Date", direction="nearest"
    )
    input_df = pd.merge_asof(
        input_df,
        reactivity_df[["Date", "Fluxes_Reactivity"]],
        on="Date",
        direction="nearest",
    )
    return input_df


def create_price_dict(home_dir):
    price_df = pd.read_excel(
        join(home_dir, "data", "ferro_alloy_price_reference.xlsx"), sheet_name="Price"
    )
    return price_df.set_index("Compound")["Price"].to_dict()


def create_grades_df(addition_type):
    if addition_type == "Bulk":
        grades_data = {
            "SAIL SEQR": {
                "Aim Min.": {"%C": 0.2, "%Mn": 0.75, "%Si": 0.17},
                "Aim Max.": {
                    "%C": 0.24,
                    "%Mn": 0.85,
                    "%Si": 0.25,
                    "%P": 0.03,
                    "%S": 0.03,
                },
            },
            "IRST12/09R-260-60-E1": {
                "Aim Min.": {"%C": 0.64, "%Mn": 0.95, "%Si": 0.26, "%V": 0.02},
                "Aim Max.": {
                    "%C": 0.7,
                    "%Mn": 1.12,
                    "%Si": 0.35,
                    "%V": 0.026,
                    "%P": 0.02,
                    "%S": 0.02,
                },
            },
            "IS2062E250BR-TMT25": {
                "Aim Min.": {"%C": 0.17, "%Mn": 0.61, "%Si": 0.14},
                "Aim Max.": {
                    "%C": 0.19,
                    "%Mn": 0.7,
                    "%Si": 0.2,
                    "%P": 0.03,
                    "%S": 0.03,
                },
            },
            "IS 7887 GR-2": {
                "Aim Min.": {"%C": 0.04, "%Mn": 0.29, "%Si": 0.03},
                "Aim Max.": {
                    "%C": 0.06,
                    "%Mn": 0.35,
                    "%Si": 0.06,
                    "%P": 0.035,
                    "%S": 0.025,
                },
            },
        }
    else:
        grades_data = {
            "SAIL SEQR": {
                "Aim Min.": {"%C": 0.215, "%Mn": 0.8, "%Si": 0.225},
                "Aim Max.": {
                    "%C": 0.24,
                    "%Mn": 0.85,
                    "%Si": 0.25,
                    "%P": 0.03,
                    "%S": 0.03,
                },
            },
            "IRST12/09R-260-60-E1": {
                "Aim Min.": {"%C": 0.685, "%Mn": 1.08, "%Si": 0.325, "%V": 0.022},
                "Aim Max.": {
                    "%C": 0.7,
                    "%Mn": 1.12,
                    "%Si": 0.35,
                    "%V": 0.026,
                    "%P": 0.02,
                    "%S": 0.02,
                },
            },
            "IS2062E250BR-TMT25": {
                "Aim Min.": {"%C": 0.185, "%Mn": 0.64, "%Si": 0.175},
                "Aim Max.": {
                    "%C": 0.19,
                    "%Mn": 0.7,
                    "%Si": 0.2,
                    "%P": 0.03,
                    "%S": 0.03,
                },
            },
            "IS 7887 GR-2": {
                "Aim Min.": {"%C": 0.055, "%Mn": 0.31, "%Si": 0.045},
                "Aim Max.": {
                    "%C": 0.06,
                    "%Mn": 0.35,
                    "%Si": 0.06,
                    "%P": 0.035,
                    "%S": 0.025,
                },
            },
        }
    return (
        pd.DataFrame.from_dict(
            {(i, j): d for i, g in grades_data.items() for j, d in g.items()},
            orient="index",
        ).fillna(0)
        / 100
    )


def write_run_to_excel(input_df, detailed_results_df, timestamp, home_dir):
    output_dir = Path(home_dir) / "debug_runs"
    output_dir.mkdir(exist_ok=True)
    filename = output_dir / f"run_output_{timestamp}.xlsx"

    def prep(df):
        df_copy = df.copy()
        for c in df_copy.columns:
            if df_copy[c].apply(lambda x: isinstance(x, (dict, list))).any():
                df_copy[c] = df_copy[c].astype(str)
        return df_copy

    try:
        with pd.ExcelWriter(filename, engine="xlsxwriter") as writer:
            prep(input_df).to_excel(writer, sheet_name="Input_Data", index=False)
            prep(detailed_results_df).to_excel(
                writer, sheet_name="Optimization_Results", index=False
            )
    except Exception as e:
        print(f"Error saving Excel file: {e}")


def apply_business_rules(pure_solution, row, addition_type, available_materials):
    """Adjusts pure mathematical quantities based on plant operational rules."""
    adjusted_solution = {m: pure_solution.get(m, 0) for m in available_materials}
    EXCLUSION_LIST = ["IS 7887 GR-2"]

    grade = row.get("Plan Details_Grade", "")
    flag_reblow = row.get("FLAG_REBLOW", 0)
    reblow_oxygen = row.get("Reblow_OXYGEN", 0)
    s_initial = row.get("S_Initial_Proportion", 0)

    if addition_type == "Trim":
        if s_initial >= 0.026 and "Fe-Si" in available_materials:
            adjusted_solution["Fe-Si"] = 0.100
        if "Ferro\nVanadium" in adjusted_solution:
            adjusted_solution["Ferro\nVanadium"] = 0.0
    else:  # Bulk
        for k in available_materials:
            v = adjusted_solution[k]
            if v > 0:
                if (
                    k in ["SiMn LP\n25_50mm", "Si-Mn\n10-50mm"]
                    and flag_reblow == 1
                    and grade not in EXCLUSION_LIST
                ):
                    adjusted_solution[k] = v + 0.100
                elif (
                    k in ["Fe-Si", "Fe-Si\n0-50mm"]
                    and 0 <= reblow_oxygen <= 500
                    and grade not in EXCLUSION_LIST
                ):
                    adjusted_solution[k] = v + 0.050
                elif (
                    k in ["Fe-Si", "Fe-Si\n0-50mm"]
                    and 500 < reblow_oxygen <= 1000
                    and grade not in EXCLUSION_LIST
                ):
                    adjusted_solution[k] = v + 0.100

        if (
            "Ferro\nVanadium" in adjusted_solution
            and pure_solution.get("Ferro\nVanadium", 0) > 0
        ):
            adjusted_solution["Ferro\nVanadium"] = 0.075

    return adjusted_solution


def calculate_optimal_additions_pulp_dynamic(
    row, grade, material_columns, grades_df, detailed_results, price_dict, addition_type
):
    element_list = ["%C", "%Mn", "%Si", "%V", "%S", "%P", "%Cr", "%Ti"]
    elements_short, weight_columns = [e[1:] for e in element_list], [
        f"input_{e[1:]}_weight" for e in element_list
    ]
    try:
        aim_min, aim_max = grades_df.loc[(grade, "Aim Min.")].fillna(0), grades_df.loc[
            (grade, "Aim Max.")
        ].fillna(0)
    except KeyError:
        raise KeyError(f"Grade {grade} not found in grades_df.")

    heat_no, final_input_weight_in_ton = (
        str(row[col_heat_no]).replace("\n", "_"),
        row[col_heat_wt],
    )
    row_result = {"Heat No": heat_no, "Grade": grade}

    if pd.isna(final_input_weight_in_ton) or final_input_weight_in_ton <= 0:
        row_result.update(
            {"Status": "Invalid", "Reason": "Invalid or missing Heat Weight."}
        )
        detailed_results.append(row_result)
        return

    for el in elements_short:
        row_result[f"Current_{el}_%"] = row.get(f"{el}_Initial_Proportion", 0)
    current_weight_ton = pd.Series(
        {
            el: (row_result[f"Current_{el}_%"] * final_input_weight_in_ton) / 100
            for el in elements_short
        }
    ).fillna(0)

    materials = row.get("Materials_Added", [])
    if not materials:
        row_result.update({"Status": "No materials available"})
        detailed_results.append(row_result)
        return

    available_weights = {
        m: {el: row.get(f"FAProp_{m}_{el}", 0) for el in elements_short}
        for m in materials
    }
    recovery_rates = {
        el[1:]: row.get(f"recovery_{addition_type.lower()}_%_{el}", 10) / 100
        for el in element_list
    }
    for el_short, rate in recovery_rates.items():
        row_result[f"Recovery_{el_short}_%"] = rate * 100

    target_min_ton, target_max_ton = (
        aim_min * final_input_weight_in_ton,
        aim_max * final_input_weight_in_ton,
    )

    Lp_prob = p.LpProblem(f"Optimize_{heat_no}", p.LpMinimize)
    material_vars = {m: p.LpVariable(m, lowBound=0) for m in materials}
    Lp_prob += p.lpSum(material_vars[m] * price_dict.get(m, 999999) for m in materials)

    for element in element_list:
        el_short = element[1:]
        total_contribution = p.lpSum(
            material_vars[m]
            * available_weights[m].get(el_short, 0)
            * recovery_rates.get(el_short, 0)
            for m in materials
        )

        if not pd.isna(target_min_ton.get(element)):
            Lp_prob += (
                current_weight_ton[el_short] + total_contribution
            ) >= target_min_ton[element]

        if el_short == "Mn":
            if not pd.isna(target_max_ton.get(element)):
                Lp_prob += (
                    current_weight_ton[el_short] + total_contribution
                ) <= target_max_ton[element]


    status = Lp_prob.solve(p.PULP_CBC_CMD(msg=False))

    if p.LpStatus[status] == "Optimal":
        pure_solution = {m: p.value(material_vars[m]) for m in materials}
        total_pure_weight_ton = final_input_weight_in_ton + sum(pure_solution.values())

        for el in elements_short:
            gain = sum(
                pure_solution[m]
                * available_weights[m].get(el, 0)
                * recovery_rates.get(el, 0)
                for m in materials
            )
            row_result[f"Expected_{el}_%"] = (
                ((current_weight_ton[el] + gain) / total_pure_weight_ton) * 100
                if total_pure_weight_ton > 0
                else 0
            )

        adjusted_solution = apply_business_rules(
            pure_solution, row, addition_type, materials
        )

        row_result.update(
            {
                "Status": "Optimal",
                "FA Cost": sum(
                    adjusted_solution.get(m, 0) * price_dict.get(m, 0)
                    for m in materials
                ),
            }
        )

        # for m in material_columns:
        #     row_result[f"Optimal_{m}"] = adjusted_solution.get(m, 0)

    ################## DISPLAYING ONLY X% OF RECOMMENDATION ##################

        for m in material_columns:
            val = adjusted_solution.get(m, 0)

            # Detect all SiMn variants
            if "simn" in m.lower() or "si-mn" in m.lower():
                row_result[f"Optimal_{m}"] = val * 0.92
            else:
                row_result[f"Optimal_{m}"] = val


    else:
        row_result.update(
            {
                "Status": "Infeasible",
                "Reason": "Solver determined problem is infeasible.",
            }
        )
        for el in elements_short:
            row_result[f"Expected_{el}_%"] = None
        for m in material_columns:
            row_result[f"Optimal_{m}"] = 0
    detailed_results.append(row_result)


def run_optimizer(input_df, list_material_options, addition_type, home_dir):
    material_columns = [
        "Fe-Cr\nHi-C",
        "Fe-Cr\nLo-C",
        "Fe-Mn \nMb",
        "Fe-Mn\nHi-C",
        "Fe-Mn\nLo-C",
        "Fe-Si",
        "Fe-Si\n0-50mm",
        "Ferro\nNiobium",
        "Ferro\nTitanium",
        "Ferro\nVanadium",
        "Si-Mn\n10-50mm",
        "Si-Mn\n25-50mm",
        "SiMn LP\n25_50mm",
        "SiMn\n12-25mm",
        "P.Coke",
    ]
    txt_timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    # logging.basicConfig(
    #     filename=f"optimization_log_{txt_timestamp}.txt",
    #     level=logging.DEBUG,
    #     format="%(message)s",
    # )
    grades_df, price_dict, input_df_for_excel = (
        create_grades_df(addition_type),
        create_price_dict(home_dir),
        input_df.copy(),
    )
    input_df["Materials_Added"], input_df[col_heat_wt] = [
        list_material_options
    ], input_df["Charging Details_HM Wt"]
    input_df = get_recoveries(input_df, home_dir)
    input_df = read_fluxes_chemistry_and_reactivity(home_dir, input_df)
    detailed_results = []
    for _, row in input_df.iterrows():
        calculate_optimal_additions_pulp_dynamic(
            row,
            row["Plan Details_Grade"],
            material_columns,
            grades_df,
            detailed_results,
            price_dict,
            addition_type,
        )

    detailed_results_df = pd.DataFrame(detailed_results)
    if not detailed_results_df.empty:
        elements_short = [
            el[1:] for el in ["%C", "%Mn", "%Si", "%V", "%S", "%P", "%Cr", "%Ti"]
        ]
        id_cols, chem_cols = ["Heat No", "Grade", "Status", "FA Cost", "Reason"], []
        for el in elements_short:
            if f"Current_{el}_%" in detailed_results_df.columns:
                chem_cols.extend(
                    [f"Current_{el}_%", f"Expected_{el}_%", f"Recovery_{el}_%"]
                )
        optimal_cols = [
            c for c in detailed_results_df.columns if c.startswith("Optimal_")
        ]
        final_order = [
            c
            for c in id_cols + chem_cols + optimal_cols
            if c in detailed_results_df.columns
        ]
        remaining_cols = [
            c for c in detailed_results_df.columns if c not in final_order
        ]
        detailed_results_df = detailed_results_df[final_order + remaining_cols]

    # uncomment this only for debugging at a run level
    # write_run_to_excel(input_df_for_excel, detailed_results_df, txt_timestamp, home_dir)
    return detailed_results_df


def append_to_sqlite(input_df, detailed_results_df, db_path="ferroalloy_records.db"):
    if detailed_results_df.empty:
        return
    input_df["Heat No"] = input_df["Heat Number"].astype(str)
    detailed_results_df["Heat No"] = detailed_results_df["Heat No"].astype(str)
    cols_to_drop = [
        c
        for c in detailed_results_df.columns
        if c in input_df.columns and c != "Heat No"
    ]
    combined_df = pd.merge(
        detailed_results_df,
        input_df.drop(columns=cols_to_drop, errors="ignore"),
        on="Heat No",
        how="left",
    )
    combined_df["run_timestamp"] = datetime.now().isoformat()
    cols_to_drop_db = [
        "CONVERTOR_ID_x",
        "CURR_HEAT_NO",
        "AIM_GRADE",
        "explicit_rowid_x",
        "SID1",
        "SID3",
        "SID4",
        "AL_VALUE",
        "NB_VALUE",
        "MO_VALUE",
        "NI_VALUE",
        "CU_VALUE",
        "CR_VALUE",
        "TI_VALUE",
        "B_VALUE",
        "SN_VALUE",
        "PB_VALUE",
        "SB_VALUE",
        "W_VALUE",
        "FE_P_VALUE",
        "N_VALUE",
        "CA_VALUE",
        "RECV_DT",
        "CONVERTOR_ID_y",
        "explicit_rowid_y",
    ]
    combined_df.drop(
        columns=[c for c in cols_to_drop_db if c in combined_df.columns],
        inplace=True,
        errors="ignore",
    )
    for col in combined_df.select_dtypes(include=["object"]).columns:
        combined_df[col] = combined_df[col].apply(
            lambda v: json.dumps(v) if isinstance(v, (dict, list)) else v
        )
    try:
        with sqlite3.connect(db_path) as conn:
            combined_df.to_sql(
                "ferroalloy_records", conn, if_exists="append", index=False
            )
    except Exception as e:
        print(f"Error appending: {e}")
