# Student Data Pipeline and UI

I built this as a Streamlit app that takes a messy student CSV, cleans
it up automatically, and gives you an interactive UI on top: a live
editable table, a minimum total score filter with running stats, and a
CSV export. You can also mark students Active or Debarred on the fly
and the shortlist updates immediately, no need to re-upload anything.

## Contents

- [Run it locally](#run-it-locally)
- [Architecture](#architecture)
- [Data Engineering and Pipeline Design](#data-engineering-and-pipeline-design)
- [UI Behavior](#ui-behavior)
- [Future Scaling and Trade-offs](#future-scaling-and-trade-offs)
- [Video Demo](#video-demo)
- [Live Deployment](#live-deployment)

## Run it locally

```bash
git clone https://github.com/Ishita-rxi/Student-data-pipeline.git
cd Student-data-pipeline
pip install -r requirements.txt
streamlit run app.py
```

It'll open at `http://localhost:8501` (or print the URL in your
terminal if it doesn't open automatically).

I've included `sample_raw_data.csv` so you can try it out right away.
This is actually the real 3,000-row dataset from the assignment, I
pulled it out of the provided PDF using `pdfplumber`'s table extraction
instead of plain text extraction, since the raw text version had rows
merged into each other with no column boundaries. Even after that fix,
the data itself is genuinely messy: about a third of the mark values
have the word "marks" stuck onto them, some names have stray quotes or
apostrophes wrapped around them, `Grade` shows up as both bare numbers
and text like "Grade 5", and there's a chunk of gender values that are
just `0` or `1` with no explanation anywhere of what they mean.

## Architecture

It's a single process, no separate backend. Streamlit reruns the whole
`app.py` script top to bottom on every interaction, so all the state
has to live in `st.session_state` between reruns instead of a database.

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

The cleaning step only runs once per uploaded file. I check
`(file.name, file.size)` against what's already in session state, so
moving the score filter or flipping a toggle doesn't trigger the whole
dataset getting re-cleaned every time.

## Data Engineering and Pipeline Design

I'm treating this like a small ETL job: extract from CSV, transform
through a set of validation and normalization steps, load into an
in-memory table that the UI reads from. The sections below map
directly to what `clean_student_data()` in `app.py` actually does.

### Feature Validation

I don't trust `Total` as raw input, I treat it as a derived value.
If it came from an upstream source (someone editing a spreadsheet by
hand, an export from another tool), there's always a risk it drifts
from the actual sum of the marks. So the pipeline recomputes
`Total = Math + Science + English` every time instead of reading
whatever value was in the file, which means the total and the marks
can never disagree with each other.

Before that recompute happens though, the marks themselves need
cleaning up first. This dataset specifically has a text unit word
("marks") stuck onto a lot of the values, things like "85 marks" or
"92marks". If I'd just run `pd.to_numeric` on that directly, it would
have silently turned roughly a third of all the mark values into
missing data, which would've been a pretty bad bug to ship without
noticing. So instead I pull out the leading number from each cell
first, then cast that. Whatever's genuinely still missing after that
gets filled with the column median and clipped to a 0-100 range, and
the cleaning report shows how many values were actually missing versus
how many got recovered.

I took the same approach with `Gender`. Most values map fine through a
lookup table ("m", "male", "boy" and so on all become "Male"), but the
dataset also has numeric codes, `0` and `1`, that aren't explained
anywhere. I checked whether those codes lined up with a student's
gender recorded somewhere else in the file (same name appears multiple
times), and they don't, both codes split roughly 50/50 either way, so
there's genuinely no way to recover what they mean. Rather than
guessing and possibly getting it wrong for around 16% of records, I
just label these "Unknown" and report the count. Felt more honest than
quietly picking one.

### Deduplication Strategy

If the source file has something like a roll number or student ID
column, I use that as the primary key for dedup. The pipeline scans
the header row for common id-style names ("Roll Number", "Roll No",
"Student ID", "Id", matched case and spacing insensitively), and if it
finds one, dedupes on that column alone, since two rows sharing an id
are the same student no matter what else differs between them (say, a
row that got re-entered with corrected marks). If there's no id column
at all, it falls back to a composite key of
`Name + Gender + Grade + Math + Science + English`. That still catches
exact duplicate rows, but it's more conservative on purpose, since
without a real id I can't assume two rows are the same person just
because the name matches.

The cleaning report in the UI actually shows which key got used, so
it's not a hidden assumption. For what it's worth, the dataset from
this assignment doesn't have an id column, and once cleaned it doesn't
have any duplicate rows under the composite key either, so the
fallback path is what's actually running against it. I tested the
id-based path separately too, with a small synthetic frame that
includes a roll number column, just to confirm it works the way I
expect once a real id is present.

### Future Extension

The pipeline spits out a flat table with a fixed schema (`Name`,
`Gender`, `Grade`, `Math`, `Science`, `English`, `Total`, `Status`),
and I kept the cleaning logic completely separate from the UI code on
purpose. `clean_student_data()` takes a raw DataFrame in and returns a
clean one out, there's no Streamlit calls anywhere inside it. That
means anything downstream that expects a clean DataFrame with this
schema could plug in without touching the cleaning code at all, for
example a matching algorithm against course or job requirements, or
even an LLM-based analyzer scoring a resume against the cleaned
record. Either one would just sit as another transform step after
`clean_student_data()`, not something that requires reworking how the
data gets ingested or validated.

## UI Behavior

**Cleaned table and Active/Debarred toggle.** The cleaned data shows up
in `st.data_editor`, every column is locked except `Status`, which is
a dropdown of Active/Debarred. Any edit writes straight back into
`st.session_state.data`, so status changes apply right away without
needing to re-upload the file.

**Filtering and shortlist.** The shortlist recomputes live as
`Status == Active and Total >= min_score` on every rerun. The summary
stats (matched count, debarred count, average total, average subject
scores) update alongside it.

**Export.** The download button just serializes whatever's currently
in the shortlist, respecting both the score filter and any status
edits, straight to CSV bytes when you click it. Nothing gets written
to disk on the server side.

## Future Scaling and Trade-offs

Right now everything runs in memory inside the Streamlit process,
which honestly is the right call for a dataset this size (a few
thousand rows). It's fast, there's zero infrastructure to manage, and
the cleaning and UI code both live in one place.

That would stop working well before you hit 100,000+ rows though.
Here's where it would actually break and what I'd do about it:

- **Memory.** `st.session_state` keeps the whole DataFrame in the
  Streamlit server's memory for as long as the session lasts. With a
  lot of concurrent users each holding their own uploaded dataset,
  that memory ceiling gets hit fast and risks the process getting
  OOM-killed.
- **Rerun cost.** Every single interaction reruns the entire script.
  That's cheap right now since the filtering and stats are simple
  pandas operations on a small frame, but at higher row counts that
  cost scales linearly and the UI starts feeling laggy on every
  keystroke.
- **What I'd actually change:** move the cleaning and filtering logic
  out of Streamlit entirely and into a backend service (FastAPI would
  be the natural fit since the pandas code is already Python), with
  the data living in a real store like Postgres or Parquet files
  instead of session state. For ingestion specifically, I'd read and
  clean the CSV in chunks using pandas' `chunksize` parameter, or
  switch to something like Polars or Dask that handles out-of-core
  processing properly, instead of loading the entire file into memory
  at once. Streamlit would then just be a thin client hitting that
  backend for filtered pages of data rather than holding the whole
  table itself.

The actual cleaning logic in `clean_student_data()` wouldn't need much
rework in that migration, since it's already just a pure function,
DataFrame in, DataFrame out, with no UI code mixed into it. The
migration is really about where that function runs and how data gets
to it, not about rewriting the cleaning rules themselves.

## Video Demo

<video src="demo/demo.mp4" controls width="600"></video>

Or open the file directly: [demo/demo.mp4](demo/demo.mp4)

The recording (57 seconds) walks through, in order:

1. Uploading the raw CSV and the cleaning report that shows up (rows
   in, rows out, gender values unmapped, which dedup key got used).
2. The cleaned table, toggling a student to Debarred, and the
   shortlist below updating right away without a re-upload.
3. Setting a minimum total score and watching the shortlist and stats
   update live.
4. Downloading the CSV and opening it to confirm it matches what's on
   screen.

## Live Deployment

https://ishita-rxi-student-data-pipeline-app-z2kr5d.streamlit.app/
