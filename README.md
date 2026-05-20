# Data Quality Platform

Automated data quality scanning for SQL Server databases. Three modules: **validity**, **stability**, and **consistency** (planned).

---

## Project Structure

```
data-quality/
├── main.py                        # CLI entry point
├── runner.py                      # Automated job queue runner (multi-worker)
├── .env                           # Configuration
├── wheels/                        # Offline pip wheels for air-gapped deployment
├── python/                        # Embeddable Python (air-gapped servers)
│
├── validity/                      # Anomaly detection on column values
│   ├── runner.py                  # Module entry point
│   ├── profiling/
│   │   ├── db.py                  # SQL Server connection + DB helpers
│   │   ├── profiler.py            # Per-column statistical profilers
│   │   ├── profile_loader.py      # Loads profiles from DB
│   │   ├── type_inference.py      # Infers logical type per column
│   │   └── utils.py               # Shape inference helpers
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
│       ├── datetime_scorer.py     # Datetime anomaly logic
│       ├── boolean_scorer.py      # Boolean anomaly logic
│       ├── null_handler.py        # Null scoring
│       └── utils.py               # Shared scorer utilities
│
├── stability/                     # Row count anomaly detection
│   ├── runner.py                  # Module entry point
│   └── row_count/
│       └── scanner.py             # Snapshot + rolling baseline check
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

### Air-gapped / restricted servers

If the target server has no internet access:

**On your dev machine:**
```bash
pip download -r requirements.txt -d wheels --platform win_amd64 --python-version 3.14 --only-binary=:all:
```

**Copy the project folder** (including `wheels/`) to the server, then:
```bash
pip install --no-index --find-links=wheels -r requirements.txt
```

If pip is not available (embeddable Python), bootstrap it first:
```bash
python\python.exe get-pip.py
python\python.exe -m pip install --no-index --find-links=wheels -r requirements.txt
```

Also add `..` to `python\python314._pth` so the project root is on the path:
```
python314.zip
.
..

import site
```

---

### `.env` Configuration

```env
# SQL Server connection
DB_SERVER=YOUR_SERVER_NAME
DB_DRIVER=ODBC Driver 17 for SQL Server
DB_DATABASES=MyDatabase1,MyDatabase2     # comma-separated list of DBs to scan

# SQL auth (optional — omit to use Windows auth / Trusted_Connection)
DB_USERNAME=ai_user
DB_PASSWORD=your_password

# Metadata storage
METADATA_DATABASE=MetadataRepository     # where profiles and scan results are stored

# Job queue
JOB_QUEUE_DATABASE=hrdm_dev              # DB that holds dbo.ai_scan
RUNNER_WORKERS=2                         # parallel job workers

# Validity scan settings
TABLE_SAMPLE_ROWS=50000
ROW_SCORE_THRESHOLD=0.85
MIN_CONTRIBUTION=0.15                    # minimum column score to contribute to row score

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
```

> **Windows auth vs SQL auth**: if `DB_USERNAME` and `DB_PASSWORD` are set, SQL Server authentication is used. Otherwise the connection falls back to `Trusted_Connection=yes` (Windows auth). Use SQL auth when running as a service account that cannot pass Windows credentials over the network (double-hop problem).

---

## Database Tables

### MetadataRepository (validity results)

#### `dbo.profiles`
Statistical profile for each table, stored as a JSON blob. Upserted on every profiling run.

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
| job_id | int | Links to `hrdm_dev.dbo.ai_scan` |
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
| job_id | int | Links to `hrdm_dev.dbo.ai_scan` |
| run_id | int | Links to `validity_scan_runs.id` |
| row_index | int | DataFrame index of the flagged row |
| row_score | float | Combined anomaly score [0–1] |
| row_data | nvarchar(max) | JSON snapshot of the entire row |

#### `dbo.validity_anomaly_details`
One row per anomalous column within a flagged row.

| Column | Type | Notes |
|---|---|---|
| job_id | int | Links to `hrdm_dev.dbo.ai_scan` |
| anomaly_row_id | int | FK → `validity_anomaly_rows.id` |
| column_name | nvarchar | |
| column_score | float | |
| reasons | nvarchar | Comma-separated anomaly signal names |

> All four tables share `job_id` from `dbo.ai_scan`, so any flagged result can be traced back to the originating job with a single ID.

---

### hrdm_dev (job queue + stability)

#### `dbo.ai_scan`
Job queue. Insert a row to trigger a scan.

| Column | Type | Notes |
|---|---|---|
| job_id | int PK | |
| scan_type | nvarchar | `validity` / `stability` |
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

`runner.py` polls `hrdm_dev.dbo.ai_scan` every second and processes jobs automatically using a thread pool.

```bash
python runner.py
```

Insert a job to trigger a scan:

```sql
-- Validity scan on a single table
INSERT INTO dbo.ai_scan (scan_type, scan, table_name, status)
VALUES ('validity', 'scan', '[HRDM_DEV].[dbo].[Employees]', 'pending')

-- Stability row count check on a whole database
INSERT INTO dbo.ai_scan (scan_type, scan, table_name, status)
VALUES ('stability', 'row_count', 'HRDM_DEV', 'pending')
```

Jobs are claimed atomically with `UPDATE TOP(1) ... OUTPUT` so multiple workers never process the same job. Worker count is controlled by `RUNNER_WORKERS` in `.env`.

---

### Production Deployment (Windows Server)

The runner is deployed as a Windows Task Scheduler task running as `SYSTEM` (or a service account).

**Create the task via PowerShell:**
```powershell
$action  = New-ScheduledTaskAction `
    -Execute "C:\path\to\data-quality\python\python.exe" `
    -Argument "runner.py" `
    -WorkingDirectory "C:\path\to\data-quality"
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "DataQualityRunner" -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -User "SYSTEM" -Force
Start-ScheduledTask -TaskName "DataQualityRunner"
```

The task:
- Starts automatically at system boot
- Restarts automatically on failure (up to 999 times, every 1 minute)
- Survives user logouts
- Logs all activity to `runner.log` in the project folder

> **Note**: When running as SYSTEM on a remote SQL Server, Windows credentials cannot be forwarded (Kerberos double-hop). Use SQL auth (`DB_USERNAME` / `DB_PASSWORD`) in `.env` instead.

**SQL permissions required for the service account:**
```sql
-- hrdm_dev
GRANT SELECT, INSERT, UPDATE ON dbo.ai_scan TO [ai_user];
GRANT SELECT, INSERT, UPDATE ON dbo.row_count_snapshots TO [ai_user];
GRANT SELECT, INSERT, UPDATE ON dbo.row_count_runs TO [ai_user];

-- MetadataRepository
GRANT SELECT, INSERT, UPDATE ON dbo.profiles TO [ai_user];
GRANT SELECT, INSERT, UPDATE ON dbo.validity_scan_runs TO [ai_user];
GRANT SELECT, INSERT, UPDATE ON dbo.validity_anomaly_rows TO [ai_user];
GRANT SELECT, INSERT, UPDATE ON dbo.validity_anomaly_details TO [ai_user];
```

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

### Anomaly Types

Full reference of all 28 anomaly signals across 6 categories — see `validity_anomaly_types.pdf`.

| Category | Key signals |
|---|---|
| Null | `null_not_allowed` (1.0), `rare_null` (0.15) |
| Numeric | `extreme_outlier` (0.7), `strong_outlier` (0.35), `parse_fail` (0.3), `moderate_outlier` (0.1), `outside_p01_p99` (0.1) |
| Categorical | `unseen_value` (0.35), `rare_value` (0.25), `unseen_value_high_cardinality` (0.1), `low_frequency` (0.1) |
| Text | `shape_violation` (0.5), `length_anomaly` (0.25), `unseen_shape` (0.1), `rare_shape` (0.1), `length_deviation` (0.1), `unseen_shape_soft` (0.05) |
| Datetime | `unexpected_future` (0.5), `far_before_historical_min` (0.35), `parse_fail` (0.3), `far_after_historical_max` (0.2), `rare_future` (0.2), `before_historical_min` (0.1), `after_historical_max` (0.1) |
| Boolean | `unexpected_false` (0.4), `unexpected_true` (0.4), `rare_false` (0.2), `rare_true` (0.2) |

### Scoring

Row score combines column scores probabilistically:

```
row_score = 1 - ∏(1 - col_score_i)   for all col_score_i >= MIN_CONTRIBUTION (0.15)
```

Scores below `MIN_CONTRIBUTION` are recorded in details but don't accumulate into the row score. Rows with `row_score >= ROW_SCORE_THRESHOLD` are written to `validity_anomaly_rows`.

### Column Filter

Columns are skipped from scoring (configurable via `.env`) when:
- Type is `identifier` — high-cardinality keys, almost always "unseen"
- Type is `free_text` — unstructured, scoring is not meaningful
- Distinct ratio ≥ `HIGH_CARDINALITY_THRESHOLD` — effectively unique per row
- Null rate ≥ `HIGH_NULL_THRESHOLD` — mostly empty
- Name matches a hint in `SKIP_COLUMN_HINTS`

### Performance

Scoring uses a two-pass approach:
- **Pass 1** (vectorized): score all rows for all eligible columns using pandas/numpy — no Python-level row loop
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

The adaptive baseline means normal organic growth is absorbed — only sudden aggressive changes are flagged.

---

## Consistency Module

Planned. Three sub-modules:

- **Cross-field** — relationships between columns within the same table
- **Cross-table** — referential integrity and distribution alignment across tables
- **Cross-system** — data consistency between different source systems
