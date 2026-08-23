# Student Data Pipeline and UI

A Streamlit app that ingests a raw student CSV, runs it through a
validation and cleaning pipeline, and exposes the result as an
interactive UI: a live editable table, a minimum-total-score filter with
summary stats, and a CSV export. Students can be marked Active or
Debarred at any time and the shortlist recomputes immediately, no
re-upload required.

## Contents

- [Run it locally](#run-it-locally)
- [Architecture](#architecture)
- [Data Engineering and Pipeline Design](#data-engineering-and-pipeline-design)
- [UI Behavior](#ui-behavior)
- [Future Scaling and Trade-offs](#future-scaling-and-trade-offs)
- [Video Demo](#video-demo)
- [Live Deployment](#live-deployment-optional)

## Run it locally

```bash
git clone <your-repo-url>
cd <your-repo>
pip install -r requirements.txt
streamlit run app.py
```

Open the local URL Streamlit prints, usually `http://localhost:8501`.

A `sample_raw_data.csv` file is included, this is the actual 3,000-row
selection dataset (extracted from the provided PDF export with
`pdfplumber`'s table extraction, which preserves column boundaries that
plain text extraction loses). It contains real messiness the pipeline
handles: a `"marks"` unit word appended to roughly a third of the mark
values, names wrapped in stray quotes or trailing apostrophes, `Grade`
given as both bare numbers and `"Grade N"` text, and unrecoverable
numeric gender codes.

## Architecture

Single process, no separate backend. Streamlit reruns `app.py` top to
bottom on every interaction, so all state lives in `st.session_state`
between reruns rather than in a database.

```
Raw CSV
   |
   v
pandas.read_csv()              (ingestion)
   |
   v
clean_student_data()           (validation and cleaning pipeline)
   |  - column and header normalization
   |  - name normalization (strip stray quotes/apostrophes, title case)
   |  - gender normalization (lookup table; unmapped codes -> Unknown)
   |  - grade normalization (bare numbers and "Grade N" text -> "GRADE N")
   |  - numeric extraction and range validation (marks, strips unit words)
   |  - missing value imputation (median fill, only for genuinely missing)
   |  - feature recomputation (Total)
   |  - deduplication (id-based, falls back to composite key)
   v
st.session_state.data          (in-memory table, holds cleaned rows + Status)
   |
   +--> st.data_editor          (editable table, Status toggle writes back
   |                              into session_state on every rerun)
   |
   +--> filter step              (Status == Active and Total >= min_score)
            |
            v
        shortlist + live stats (count, averages)
            |
            v
        st.download_button     (CSV export of the current shortlist)
```

The cleaning step only runs once per uploaded file. The app tracks
`(file.name, file.size)` in session state and skips re-cleaning on
reruns triggered by filter or toggle changes, so editing the score
threshold does not reprocess the whole dataset.

## Data Engineering and Pipeline Design

This is framed as a small ETL pipeline: extract from CSV, transform
through validation and normalization steps, load into an in-memory
table that backs the UI. Each step below maps to a stage in
`clean_student_data()` in `app.py`.

### Feature Validation

`Total` is treated as a derived feature, not raw input. Trusting a
`Total` value that arrived from an upstream source (a spreadsheet
someone edited by hand, an export from another system) risks silent
drift between the stored total and the sum of the underlying subject
marks. The pipeline recomputes `Total = Math + Science + English` on
every run rather than reading it from the file, so the stored total and
the underlying marks can never disagree.

Before that recomputation happens, the subject marks themselves go
through validation, not a straight numeric cast. The actual dataset
here has a text unit word (`"marks"`) appended to a large share of the
values (`"85 marks"`, `"92marks"`), which a plain `pd.to_numeric` call
would silently turn into missing data on roughly a third of all mark
values. The pipeline extracts the leading numeric token from each cell
before casting, so a validated numeric value is recovered instead of
being lost to imputation. Whatever is still missing after that gets
filled with the column median and clipped to a 0-100 range, and the
count of genuinely missing values (as opposed to recovered ones) is
shown in the cleaning report.

The same validate-rather-than-guess approach applies to `Gender`. Most
values map cleanly through a lookup table (`m`, `male`, `boy` and
similar all resolve to `Male`), but the dataset also contains numeric
codes (`0`, `1`) with no key anywhere in the file explaining what they
mean. I checked whether those codes correlated with a student's gender
recorded elsewhere in the dataset (same name, different row), and they
do not: `0` and `1` split roughly 50/50 across genders regardless of
which code is used, so there is no reliable mapping to recover. Rather
than guessing (which would silently inject wrong data into just over
16% of records), the pipeline labels these `Unknown` and reports the
count, treating an unrecoverable value as a data quality issue to
surface, not one to paper over.

### Deduplication Strategy

The pipeline treats a roll number or student id column as the natural
primary key when one is present in the source file. It scans the
header row for common id-style names (`Roll Number`, `Roll No`,
`Student ID`, `Id`, matched case and spacing insensitively) and, if
found, deduplicates on that column alone, since two rows sharing a
primary key are the same student record regardless of what else
differs between them (a re-entered row with corrected marks, for
example). If no id column exists in the source file, it falls back to
a composite key of `Name + Gender + Grade + Math + Science + English`,
which catches exact re-uploads or copy-paste duplicates but is
intentionally more conservative, since without a stable id it cannot
assume two rows are the same student just because the name matches.
The cleaning report in the UI shows which key was actually used, so
this is not a hidden assumption. The provided dataset has no id column
and, once cleaned, no duplicate records under the composite key either,
so the fallback path is what actually runs against it; both paths are
covered by a direct test of `clean_student_data()` against a small
frame with a synthetic id column, confirming the primary-key path
works the same way once a real id column is present.

### Future Extension

The pipeline outputs a flat, typed table with a fixed schema
(`Name`, `Gender`, `Grade`, `Math`, `Science`, `English`, `Total`,
`Status`) and keeps the cleaning step fully decoupled from the UI
rendering step. That separation is intentional: `clean_student_data()`
takes a raw DataFrame and returns a clean one, with no Streamlit calls
inside it. Anything downstream that consumes a clean, validated
DataFrame with the same schema can plug in without touching the
cleaning logic, for example a matching or ranking algorithm against a
job or course requirement, or an LLM-based analyzer scoring a resume
or application against the cleaned record. Both would sit as an
additional transform stage after `clean_student_data()` and before (or
alongside) the UI layer, rather than requiring changes to how the data
is ingested or validated.

## UI Behavior

**Cleaned table and Active/Debarred toggle.** The cleaned table renders
through `st.data_editor` with every column locked except `Status`,
a dropdown of `Active` / `Debarred`. Edits write back into
`st.session_state.data` immediately, so status changes take effect
without re-uploading the file.

**Filtering and shortlist.** The shortlist is computed live as
`Status == Active and Total >= min_score` on every rerun. Summary
metrics (matched count, debarred count, average total, average subject
scores) recompute alongside it.

**Export.** The download button serializes whatever the shortlist
currently contains, respecting both the score filter and any Status
edits, to CSV bytes at render time. Nothing is written to disk
server-side.

## Future Scaling and Trade-offs

The current design processes the entire dataset in memory inside the
Streamlit process, which is the right call for a campus-sized dataset
(hundreds to low thousands of rows): it is fast, has zero
infrastructure to run, and keeps the cleaning and UI code in one place.

That stops being true well before 100,000+ rows. A few concrete limits
and what I would change:

- **Memory.** `st.session_state` holds the full DataFrame in the
  Streamlit server process's memory for the life of the session. At
  large row counts, especially with many concurrent users each holding
  their own uploaded dataset, this becomes a real memory ceiling and
  risks the process crashing or getting OOM-killed.
- **Rerun cost.** Every interaction reruns the whole script. Right now
  that is cheap because the filter and stats are simple pandas
  operations over a small frame. At high row counts this rerun cost
  grows linearly with data size and starts to make the UI feel
  sluggish on every keystroke.
- **What I would change:** move cleaning and filtering out of the
  Streamlit process and into a backend service (FastAPI is the natural
  fit given the existing Python/pandas code), with the dataset living
  in a proper store (Postgres, or a columnar format like Parquet on
  disk) instead of in-memory session state. For the CSV ingestion step
  specifically, I would read and clean the file in chunks with
  pandas' `chunksize` parameter or switch to a library like Polars or
  Dask that supports out-of-core and lazy processing, rather than
  loading the whole file into memory at once. Streamlit would then
  become a thin client calling that backend for filtered pages of data,
  rather than holding the whole table itself.

The pipeline logic itself (`clean_student_data()`) would not need to
change much in that migration, since it is already a pure function of
a DataFrame in and a DataFrame out with no UI code mixed in. The
migration is mostly about where that function runs and how the data
gets to it, not about rewriting the cleaning rules.

## Video Demo

*(Add your 90 second or shorter screen recording link here.)*

Suggested structure for the recording, framed as a pipeline walkthrough
rather than a feature tour:

1. Upload the raw CSV, call out the cleaning report (rows in, rows out,
   dedup key used, marks imputed).
2. Show the cleaned table and toggle a student to Debarred, point out
   the shortlist below updating without a re-upload.
3. Set a minimum total score, show the shortlist and stats updating
   live.
4. Download the CSV and open it to confirm it matches what is on
   screen.

## Live Deployment (optional)

*(Add your Streamlit Community Cloud or Hugging Face Spaces link here
once deployed.)* To deploy on Streamlit Community Cloud: push this repo
to GitHub, go to share.streamlit.io, connect the repo, and point it at
`app.py`.
#   S t u d e n t - d a t a - p i p e l i n e  
 