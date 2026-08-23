"""
Student Data Pipeline and UI.

Upload a raw student CSV, it gets cleaned automatically, then you can
review the cleaned table, filter by minimum total score, mark students
Active or Debarred on the fly, and export the shortlist.

Run with: streamlit run app.py
"""

import pandas as pd
import streamlit as st

st.set_page_config(page_title="Student Data Pipeline", layout="wide")

REQUIRED_COLUMNS = ["Name", "Gender", "Grade", "Math", "Science", "English", "Total"]
SUBJECTS = ["Math", "Science", "English"]

GENDER_LOOKUP = {
    "m": "Male", "male": "Male", "man": "Male", "boy": "Male",
    "f": "Female", "female": "Female", "woman": "Female", "girl": "Female",
}

# matches a leading number, so "85 marks", "85marks", "85.0 marks" all
# resolve to the number and drop whatever unit word follows it
NUMBER_PREFIX_RE = r"(-?\d+\.?\d*)"


def clean_student_data(raw):
    """Take a raw student dataframe and return a cleaned copy plus a stats dict."""
    stats = {}
    df = raw.copy()

    # match column names case-insensitively and fix spacing typos in headers
    df.columns = [str(c).strip() for c in df.columns]
    existing = {c.lower().replace(" ", "").replace("_", ""): c for c in df.columns}
    renames = {}
    for target in REQUIRED_COLUMNS:
        key = target.lower()
        if key in existing and existing[key] != target:
            renames[existing[key]] = target
    df = df.rename(columns=renames)

    missing_cols = [c for c in REQUIRED_COLUMNS if c not in df.columns and c != "Total"]
    if missing_cols:
        stats["missing_columns"] = missing_cols
    for c in REQUIRED_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    stats["rows_in"] = len(df)

    for col in ["Name", "Gender", "Grade"]:
        df[col] = df[col].astype(str).str.strip()

    # drop rows where the name is blank or unusable
    before = len(df)
    df = df[~df["Name"].str.lower().isin(["", "nan", "none", "n/a"])]
    stats["rows_dropped_no_name"] = before - len(df)

    # fix name casing. some source files wrap names in stray quote marks
    # or trailing apostrophes ("Aarav", Navya'), strip those before casing
    df["Name"] = (
        df["Name"]
        .str.replace('"', "", regex=False)
        .str.strip("'")
        .str.strip()
        .str.title()
    )
    df["Gender"] = (
        df["Gender"]
        .str.lower()
        .map(GENDER_LOOKUP)
        .fillna(df["Gender"].str.title())
    )
    # some source files use numeric or otherwise unrecognized gender codes
    # (e.g. "0" / "1") with no documented mapping anywhere in the file. we
    # do not guess at what they mean, we record them as Unknown and report
    # the count rather than silently assigning a gender that may be wrong
    unmapped_gender = int((~df["Gender"].isin(["Male", "Female"])).sum())
    stats["gender_values_unmapped"] = unmapped_gender
    df.loc[~df["Gender"].isin(["Male", "Female"]), "Gender"] = "Unknown"

    # normalize grade to a single consistent format. source files mix bare
    # numbers ("5") with prefixed text ("Grade 5", "GRADE5"), so pull out
    # the grade number and rebuild the label rather than just fixing case
    grade_digits = df["Grade"].str.extract(r"(\d+)", expand=False)
    df["Grade"] = "GRADE " + grade_digits.fillna(df["Grade"].str.upper().str.strip())

    # coerce marks to numeric. some source files append a stray unit word
    # to the value ("85 marks", "85marks"), pull out the leading number
    # first so that data is not lost to missing-value imputation below
    for col in SUBJECTS:
        extracted = df[col].astype(str).str.extract(NUMBER_PREFIX_RE, expand=False)
        df[col] = pd.to_numeric(extracted, errors="coerce")

    # fill missing marks with the column median, then keep values in a valid range
    filled_counts = {}
    for col in SUBJECTS:
        filled_counts[col] = int(df[col].isna().sum())
        median_val = df[col].median()
        if pd.isna(median_val):
            median_val = 0
        df[col] = df[col].fillna(median_val)
        df[col] = df[col].clip(lower=0, upper=100).round(0).astype(int)
    stats["missing_marks_filled"] = filled_counts

    # recompute total from the three subjects instead of trusting the raw value
    df["Total"] = df[SUBJECTS].sum(axis=1)

    # deduplicate on a roll number or id column if the raw file has one, since
    # that is the natural primary key for a student record. if no id column
    # exists, fall back to a composite key of name, gender, grade, and marks.
    id_col = None
    for candidate in ["rollnumber", "rollno", "studentid", "id"]:
        if candidate in existing:
            id_col = existing[candidate]
            break

    before = len(df)
    if id_col:
        df[id_col] = df[id_col].astype(str).str.strip()
        df = df.drop_duplicates(subset=[id_col], keep="first")
        stats["dedup_key"] = id_col
    else:
        df = df.drop_duplicates(subset=["Name", "Gender", "Grade"] + SUBJECTS, keep="first")
        stats["dedup_key"] = "composite (Name, Gender, Grade, Math, Science, English), no id column found"
    stats["duplicate_rows_removed"] = before - len(df)

    df = df.reset_index(drop=True)
    df["Status"] = "Active"

    stats["rows_out"] = len(df)
    return df, stats


if "data" not in st.session_state:
    st.session_state.data = None
if "upload_key" not in st.session_state:
    st.session_state.upload_key = None

st.title("Student Data Pipeline")
st.caption("Upload, clean, review, filter, debar or undebar, export")

uploaded_file = st.file_uploader("Upload raw student CSV", type=["csv"])

if uploaded_file is not None:
    upload_key = (uploaded_file.name, uploaded_file.size)
    if upload_key != st.session_state.upload_key:
        raw = pd.read_csv(uploaded_file)
        cleaned, stats = clean_student_data(raw)
        st.session_state.data = cleaned
        st.session_state.upload_key = upload_key
        st.session_state.stats = stats
        st.success(f"Uploaded and cleaned: {stats['rows_in']} rows in, {stats['rows_out']} rows out")

if st.session_state.data is not None:
    stats = st.session_state.stats
    with st.expander("Cleaning report", expanded=False):
        st.write(f"- Rows in: **{stats['rows_in']}**")
        st.write(f"- Rows dropped for no name: **{stats['rows_dropped_no_name']}**")
        st.write(f"- Gender values not recognized, set to Unknown: **{stats['gender_values_unmapped']}**")
        st.write(f"- Deduplication key used: **{stats['dedup_key']}**")
        st.write(f"- Duplicate rows removed: **{stats['duplicate_rows_removed']}**")
        st.write("- Missing marks filled with column median:")
        for k, v in stats["missing_marks_filled"].items():
            st.write(f"  - {k}: {v}")
        if stats.get("missing_columns"):
            st.warning(f"Missing expected columns, added as empty: {stats['missing_columns']}")
        st.write(f"- Rows out: **{stats['rows_out']}**")

    st.subheader("Cleaned data, toggle Active or Debarred per student")
    table = st.data_editor(
        st.session_state.data,
        column_config={
            "Status": st.column_config.SelectboxColumn(
                "Status",
                options=["Active", "Debarred"],
                required=True,
            )
        },
        disabled=[c for c in st.session_state.data.columns if c != "Status"],
        use_container_width=True,
        hide_index=True,
        key="student_editor",
    )
    # save toggle changes right away so filtering below reflects them
    # without needing to re-upload the file
    st.session_state.data = table

    st.subheader("Shortlist by minimum total score")
    min_score = st.number_input("Minimum total score", min_value=0, max_value=300, value=0, step=1)

    active = table[table["Status"] == "Active"]
    shortlist = active[active["Total"] >= min_score]
    debarred_count = int((table["Status"] == "Debarred").sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Matched students", len(shortlist))
    c2.metric("Debarred, excluded", debarred_count)
    c3.metric("Avg total, shortlist", f"{shortlist['Total'].mean():.1f}" if len(shortlist) else "n/a")
    c4.metric(
        "Avg Math / Sci / Eng",
        f"{shortlist['Math'].mean():.0f} / {shortlist['Science'].mean():.0f} / {shortlist['English'].mean():.0f}"
        if len(shortlist) else "n/a",
    )

    st.dataframe(
        shortlist.drop(columns=["Status"]),
        use_container_width=True,
        hide_index=True,
    )

    csv_bytes = shortlist.drop(columns=["Status"]).to_csv(index=False).encode("utf-8")
    st.download_button(
        label="Download shortlist as CSV",
        data=csv_bytes,
        file_name="shortlist.csv",
        mime="text/csv",
    )
else:
    st.info("Upload a CSV file to get started.")
