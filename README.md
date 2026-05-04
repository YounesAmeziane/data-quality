# Data Quality Platform

Automated data quality scanning for SQL Server databases. Three modules: **validity**, **stability**, and **consistency** (in progress).

---

## Project Structure

```
data-quality/
├── main.py                        # CLI entry point
├── runner.py                      # Automated job queue runner
├── .env                           # Configuration
│
├── validity/                      # Anomaly detection on column values
│   ├── runner.py                  # Module entry point
│   ├── profiling/
│   │   ├── db.py                  # SQL Server connection + DB helpers
│   │   ├── profiler.py            # Per-column statistical profilers
│   │   ├── profile_loader.py      # Loads profiles from DB
│   │   ├── type_inference.py      # Infers logical type per column
│   │   └── utils.py               # Shape inference, char class helpers
│   ├── scanning/
│   │   ├── table_scanner.py       # Orchestrates scan + writes results to DB
│   │   └── column_filter.py       # Decides which columns to score
│   └── scoring/
│       ├── scorer.py              # Per-value scorer (dispatches by type)
│       ├── row_scorer.py          # Combines column scores into a row score
│       ├── vectorized.py          # Vectorized column scorers (pandas/numpy)
│       ├── numeric_scorer.py      # Numeric anomaly logic
│       ├── categorical_scorer.py  # Categorical anomaly logic
│       ├── text_scorer.py         # Text/shape anomaly logic
│       ├── null_handler.py        # Null scoring
│       └── utils.py               # Shared scorer utilities
│
├── stability/                     # Row count and batch anomaly detection
│   ├── runner.py                  # Module entry point
│   ├── row_count/
│   │   └── scanner.py             # Snapshot + rolling baseline check
│   └── batch/                     # Placeholder
│
└── consistency/                   # Cross-table / cross-field checks (planned)
    ├── cross_field/
    ├── cross_table/
    └── cross_system/
```

---

## Setup

### Requirements

```
pip install pandas sqlalchemy pyodbc python-dotenv numpy
```

### `.env` Configuration

```env
# SQL Server connection
DB_SERVER=YOUR_SERVER_NAME
DB_DRIVER=ODBC Driver 17 for SQL Server
DB_DATABASES=MyDatabase1,MyDatabase2     # comma-separated list of DBs to scan

# Metadata storage
METADATA_DATABASE=MetadataRepository     # where profiles and scan results are stored

# Job queue
JOB_QUEUE_DATABASE=hrdm_dev              # DB that holds dbo.ai_scan

# Validity scan settings
TABLE_SAMPLE_ROWS=50000
ROW_SCORE_THRESHOLD=0.85

# Column filter settings
SKIP_IDENTIFIERS=true
SKIP_FREE_TEXT=true
SKIP_HIGH_CARDINALITY=true
HIGH_CARDINALITY_THRESHOLD=0.98
SKIP_HIGH_NULL_COLUMNS=true
HIGH_NULL_THRESHOLD=0.98
SKIP_COLUMN_HINTS=                       # comma-separated name substrings to skip
FORCE_INCLUDE_COLUMNS=                   # override — always scan these columns
FORCE_EXCLUDE_COLUMNS=                   # override — never scan these columns

# Stability settings
STABILITY_WINDOW=30                      # number of historical snapshots for baseline
STABILITY_Z_THRESHOLD=3.0               # Z-score to flag a row count anomaly
STABILITY_CHANGE_PCT_THRESHOLD=0.5      # 50% single-step change threshold (thin baseline fallback)
```

---

## Database Tables

### MetadataRepository (validity results)

#### `dbo.profiles`
Stores the statistical profile for each table, as a JSON blob. Upserted on every profiling run.

| Column | Type | Notes |
|---|---|---|
| db_name | nvarchar | Source database name |
| table_name | nvarchar | `schema.table` format |
| profile | nvarchar(max) | JSON — all column profiles |
| last_profile | timestamp | Auto-managed by SQL Server |

#### `dbo.validity_scan_runs`
One row per table per scan job.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `dbo.ai_scan` |
| db_name | nvarchar | |
| table_name | nvarchar | `schema.table` format |
| scanned_at | datetime2 | |
| threshold | float | Row score threshold used |
| rows_scanned | int | |
| rows_flagged | int | |
| flagged_rate | float | rows_flagged / rows_scanned |

#### `dbo.validity_anomaly_rows`
One row per flagged row.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `validity_scan_runs.id` |
| row_index | int | DataFrame index of the flagged row |
| row_score | float | Combined anomaly score [0–1] |
| row_data | nvarchar(max) | JSON snapshot of the entire row |

#### `dbo.validity_anomaly_details`
One row per anomalous column within a flagged row.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| anomaly_row_id | int | FK → `validity_anomaly_rows.id` |
| column_name | nvarchar | |
| column_score | float | |
| reasons | nvarchar | Comma-separated signal names |

---

### hrdm_dev (job queue + stability)

#### `dbo.ai_scan`
Job queue. Insert a row to trigger a scan.

| Column | Type | Notes |
|---|---|---|
| job_id | int PK | |
| scan_type | nvarchar | `validity` / `stability` / `consistency` |
| scan | nvarchar | Operation within the module |
| table_name | nvarchar | Target (DB name or `[db].[schema].[table]`) |
| status | nvarchar | `pending` → `processing` → `done` / `failed` |
| started_at | datetime2 | Set when claimed |
| finished_at | datetime2 | Set on done/failed |
| error | nvarchar(max) | Full traceback on failure |

#### `dbo.row_count_snapshots`
Historical row count log. The rolling baseline is built from this.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| db_name | nvarchar | |
| table_name | nvarchar | `schema.table` format |
| row_count | bigint | |
| snapshotted_at | datetime2 | |

#### `dbo.row_count_runs`
One row per table per stability scan job.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `dbo.ai_scan` |
| db_name | nvarchar | |
| table_name | nvarchar | `schema.table` format |
| row_count | bigint | Current count |
| previous_count | bigint | Last snapshot count |
| change_pct | float | `(current - previous) / previous` |
| run_at | datetime2 | |
| anomaly | bit | 1 = flagged |
| z_score | float | Distance from rolling mean in std devs |

---

## Running

### CLI (manual)

```bash
# Validity — profile all tables in DB_DATABASES
python main.py --scan_type validity --scan profile

# Validity — profile a single database
python main.py --scan_type validity --scan profile --table_name HRDM_DEV

# Validity — scan a single table
python main.py --scan_type validity --scan scan --table_name [HRDM_DEV].[dbo].[Employees]

# Stability — snapshot + check all tables in a database
python main.py --scan_type stability --scan row_count --table_name HRDM_DEV

# Stability — check a single table
python main.py --scan_type stability --scan row_count --table_name [HRDM_DEV].[dbo].[Employees]
```

### Automated Job Runner

`runner.py` polls `hrdm_dev.dbo.ai_scan` every second and processes jobs automatically.

```bash
python runner.py
```

Insert a job to trigger a scan:

```sql
INSERT INTO dbo.ai_scan (scan_type, scan, table_name, status)
VALUES ('validity', 'scan', '[HRDM_DEV].[dbo].[Employees]', 'pending')
```

```sql
INSERT INTO dbo.ai_scan (scan_type, scan, table_name, status)
VALUES ('stability', 'row_count', 'HRDM_DEV', 'pending')
```

Jobs are claimed atomically with `UPDATE TOP(1) ... OUTPUT` so multiple runners can run in parallel without double-processing.

---

## Validity Module

### How It Works

Two phases per table:

**1. Profile** — read a sample of the table, compute statistical profiles per column, save to `dbo.profiles`.

**2. Scan** — load profiles, score every row for anomalies, save flagged rows and details to DB.

### Logical Types

Each column is assigned one of 7 logical types at profile time:

| Type | How detected |
|---|---|
| `boolean` | bool dtype |
| `datetime` | datetime dtype, date tokens in column name, YYYYMMDD integer range |
| `numeric` | numeric dtype or >95% numeric-parseable strings |
| `categorical` | distinct ratio < 5%, avg length ≤ 30 |
| `identifier` | ends in `_id`, distinct ratio > 95%, avg length ≤ 40 |
| `structured_text` | column name contains email/phone/postal/code keywords |
| `free_text` | avg length > 60 |

### Scoring

Each column type has its own scorer:

- **Numeric**: Z-score using IQR-based spread. Values within p01–p99 are capped at 0.1 regardless of Z-score to avoid false positives on tight-IQR sentinel codes.
- **Categorical**: unseen values score 0.35 (low-cardinality) or 0.1 (high-cardinality). Rare known values (< 0.1%) score 0.25.
- **Text/Structured**: length deviation + shape pattern scoring. Dominant-format columns score unseen shapes at 0.5.
- **Null**: unexpected nulls score 1.0 (not nullable column) or 0.15 (nullable but null rate < 1%).

Row score combines column scores probabilistically:

```
row_score = 1 - ∏(1 - col_score_i)   for all col_score_i >= 0.15
```

Scores below 0.15 are recorded in details but don't accumulate into the row score.

### Column Filter

Columns are skipped from scoring (configurable via `.env`) when:
- Type is `identifier` — high-cardinality keys, almost always "unseen"
- Type is `free_text` — unstructured, scoring is meaningless
- Distinct ratio ≥ 98% — effectively unique per row
- Null rate ≥ 98% — mostly empty
- Name matches a hint in `SKIP_COLUMN_HINTS`

### Performance

Scoring uses a two-pass approach:
- **Pass 1** (vectorized): score all rows for all eligible columns at once using pandas/numpy — no Python-level row loop
- **Pass 2** (detail): run the per-row scorer only for flagged rows to collect human-readable reasons

---

## Stability Module

### Row Count Anomaly

On each run for a table:
1. Snapshot the current row count using `sys.partitions` (fast, no full table scan)
2. Load the last 30 snapshots from `dbo.row_count_snapshots`
3. Compute rolling mean and std of the history
4. Flag if Z-score ≥ 3.0 (gradual growth shifts the baseline naturally over time)
5. Fallback: flag if single-step change ≥ 50% when baseline is thin (< 3 snapshots) or has zero variance
6. Save result to `dbo.row_count_runs` and append new snapshot

The adaptive window means normal organic growth is absorbed — only sudden aggressive changes are flagged.

---

## Consistency Module

Planned. Three sub-modules:

- **Cross-field** — relationships between columns within the same table
- **Cross-table** — referential integrity and distribution alignment across tables
- **Cross-system** — data consistency between different source systems
