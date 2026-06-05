# Data Quality Platform

Automated data quality scanning for SQL Server databases. Three modules: **validity**, **stability**, and **consistency**.

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
└── consistency/                   # Cross-system data consistency
    ├── runner.py                  # Module entry point
    └── cross_system/
        └── scanner.py             # Hash-based cross-system table comparison
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
DB_DATABASES=MyDatabase1,MyDatabase2     # comma-separated list of DBs to scan (validity)

# SQL auth (optional — omit to use Windows auth / Trusted_Connection)
DB_USERNAME=ai_user
DB_PASSWORD=your_password

# Metadata storage + job queue (all results and the queue live here)
METADATA_DATABASE=MetadataRepository
JOB_QUEUE_DATABASE=MetadataRepository
RUNNER_WORKERS=2                         # parallel job workers

# Validity scan settings
TABLE_SAMPLE_ROWS=0                      # 0 = full table scan, N = TOP N rows
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

# Stability settings
STABILITY_WINDOW=30                      # number of snapshots used for baseline
STABILITY_Z_THRESHOLD=3.0               # z-score threshold to flag anomaly
STABILITY_CHANGE_PCT_THRESHOLD=0.5      # fallback: flag if change >= 50%
```

> **Windows auth vs SQL auth**: if `DB_USERNAME` and `DB_PASSWORD` are set, SQL Server authentication is used. Otherwise the connection falls back to `Trusted_Connection=yes` (Windows auth). Use SQL auth when running as a service account that cannot pass Windows credentials over the network (double-hop problem).

---

## Database Tables

All tables live in `MetadataRepository.dm_dq` except `dbo.profiles`.

### Job Queue

#### `dm_dq.scan_queue`
Central job queue. Insert a row to trigger any scan.

| Column | Type | Notes |
|---|---|---|
| job_id | int PK | Auto-increment |
| scan_type | nvarchar | `validity` / `stability` / `consistency` |
| scan | nvarchar | Operation within the module |
| table_name | nvarchar | Target table, DB name, or pipe-delimited pair |
| status | nvarchar | `pending` → `processing` → `done` / `failed` |
| started_at | datetime2 | Set when claimed by a worker |
| finished_at | datetime2 | Set on done/failed |
| error | nvarchar(max) | Full traceback on failure |

---

### Validity

#### `dbo.profiles`
Statistical profile per column, stored as a JSON blob. Upserted on every profiling run.

| Column | Type | Notes |
|---|---|---|
| db_name | nvarchar | Source database name |
| table_name | nvarchar | `schema.table` format |
| profile | nvarchar(max) | JSON — all column profiles |
| last_profile | datetime2 | UTC timestamp of last profile run |

#### `dm_dq.validity_scan_summary`
One row per table per scan job.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `dm_dq.scan_queue` |
| db_name | nvarchar | |
| table_name | nvarchar | `schema.table` format |
| scanned_at | datetime2 | |
| threshold | float | Row score threshold used |
| rows_scanned | int | |
| rows_flagged | int | |
| flagged_rate | float | rows_flagged / rows_scanned |

#### `dm_dq.validity_scan_row_data`
One row per flagged row.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `dm_dq.scan_queue` |
| run_id | int | Links to `validity_scan_summary.id` |
| row_index | int | DataFrame index of the flagged row |
| row_score | float | Combined anomaly score [0–1] |
| row_data | nvarchar(max) | JSON snapshot of the entire row |

#### `dm_dq.validity_scan_result`
One row per anomalous column within a flagged row.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `dm_dq.scan_queue` |
| anomaly_row_id | int | FK → `validity_scan_row_data.id` |
| row_index | int | DataFrame index of the flagged row |
| column_name | nvarchar | |
| column_score | float | |
| column_value | nvarchar(max) | Actual cell value |
| reasons | nvarchar | Comma-separated anomaly signal names |

---

### Stability

#### `dm_dq.stability_targets`
Registry of tables to monitor. Add a row per table — set `enabled = 0` to pause without deleting.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| db_name | nvarchar | |
| schema_name | nvarchar | |
| table_name | nvarchar | |
| enabled | bit | 1 = active |
| added_at | datetime2 | |

#### `dbo.row_count_snapshots`
Historical row count log. The rolling baseline is built from this.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| db_name | nvarchar | |
| table_name | nvarchar | `schema.table` format |
| row_count | bigint | |
| snapshotted_at | datetime2 | |

#### `dm_dq.row_count_runs`
One row per table per stability check job.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `dm_dq.scan_queue` |
| db_name | nvarchar | |
| table_name | nvarchar | `schema.table` format |
| row_count | bigint | Current count |
| previous_count | bigint | Last snapshot count |
| change_pct | float | `(current - previous) / previous` |
| run_at | datetime2 | |
| anomaly | bit | 1 = flagged |
| z_score | float | Distance from rolling mean in std devs |

---

### Consistency

#### `dm_dq.consistency_runs`
One row per cross-system comparison job.

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| job_id | int | Links to `dm_dq.scan_queue` |
| source_db | nvarchar | |
| source_table | nvarchar | `schema.table` format |
| target_db | nvarchar | |
| target_table | nvarchar | `schema.table` format |
| join_key | nvarchar | Column used to match rows |
| scanned_at | datetime2 | |
| source_count | bigint | Row count in source |
| target_count | bigint | Row count in target |
| missing_count | int | Rows in source not in target |
| extra_count | int | Rows in target not in source |
| modified_count | int | Rows with matching key but differing values |

#### `dm_dq.consistency_result`
One row per discrepancy (missing row, extra row, or differing cell).

| Column | Type | Notes |
|---|---|---|
| id | int PK | |
| run_id | int | Links to `consistency_runs.id` |
| job_id | int | Links to `dm_dq.scan_queue` |
| join_key_value | nvarchar | The key value of the affected row |
| issue_type | nvarchar | `missing` / `extra` / `modified` |
| column_name | nvarchar | NULL for missing/extra rows |
| source_value | nvarchar(max) | |
| target_value | nvarchar(max) | |

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

# Stability — snapshot all tables in stability_targets
python main.py --scan_type stability --scan snapshot

# Stability — check all tables in stability_targets
python main.py --scan_type stability --scan check

# Stability — snapshot a single table
python main.py --scan_type stability --scan snapshot --table_name [HRDM_DEV].[dbo].[Employees]

# Consistency — compare two tables across systems
python main.py --scan_type consistency --scan cross_system --table_name "[HRDM_DEV].[dbo].[Employee]|[STG].[dbo].[Employee]|EmployeeKey"
```

### Automated Job Runner

`runner.py` polls `MetadataRepository.dm_dq.scan_queue` every second and processes jobs using a thread pool.

```bash
python runner.py
```

Insert a job to trigger a scan:

```sql
-- Validity profile
INSERT INTO MetadataRepository.dm_dq.scan_queue (scan_type, scan, table_name, status)
VALUES ('validity', 'profile', '[HRDM_DEV].[dbo].[Employees]', 'pending')

-- Validity scan
INSERT INTO MetadataRepository.dm_dq.scan_queue (scan_type, scan, table_name, status)
VALUES ('validity', 'scan', '[HRDM_DEV].[dbo].[Employees]', 'pending')

-- Stability snapshot (all targets)
INSERT INTO MetadataRepository.dm_dq.scan_queue (scan_type, scan, table_name, status)
VALUES ('stability', 'snapshot', NULL, 'pending')

-- Stability check (all targets)
INSERT INTO MetadataRepository.dm_dq.scan_queue (scan_type, scan, table_name, status)
VALUES ('stability', 'check', NULL, 'pending')

-- Consistency cross-system
INSERT INTO MetadataRepository.dm_dq.scan_queue (scan_type, scan, table_name, status)
VALUES ('consistency', 'cross_system', '[HRDM_DEV].[dbo].[Employee]|[STG].[dbo].[Employee]|EmployeeKey', 'pending')
```

Jobs are claimed atomically with `UPDATE TOP(1) ... OUTPUT` so multiple workers never process the same job. Worker count is controlled by `RUNNER_WORKERS` in `.env`.

---

### Production Deployment (Windows Server)

Three Task Scheduler tasks run automatically:

| Task | Schedule | What it does |
|---|---|---|
| `DataQualityRunner` | At startup (always running) | Polls queue and processes jobs |
| `DataQuality_StabilitySnapshot` | Daily 6:00 AM | Inserts stability snapshot job |
| `DataQuality_StabilityCheck` | Daily 8:00 AM | Inserts stability check job |

**Create all tasks via PowerShell (run as Administrator):**
```powershell
# Runner — always on
$action  = New-ScheduledTaskAction `
    -Execute "C:\path\to\data-quality\python\python.exe" `
    -Argument "runner.py" `
    -WorkingDirectory "C:\path\to\data-quality"
$trigger  = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1)
Register-ScheduledTask -TaskName "DataQualityRunner" -Action $action -Trigger $trigger `
    -Settings $settings -RunLevel Highest -User "SYSTEM" -Force
Start-ScheduledTask -TaskName "DataQualityRunner"

# Stability snapshot — 6:00 AM daily
$action = New-ScheduledTaskAction `
    -Execute "sqlcmd" `
    -Argument "-S YOUR_SERVER -d MetadataRepository -U ai_user -P `"your_password`" -Q `"INSERT INTO dm_dq.scan_queue (scan_type, scan, table_name, status) VALUES ('stability', 'snapshot', NULL, 'pending')`""
$trigger  = New-ScheduledTaskTrigger -Daily -At "06:00"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "DataQuality_StabilitySnapshot" `
    -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -User "SYSTEM" -Force

# Stability check — 8:00 AM daily
$action = New-ScheduledTaskAction `
    -Execute "sqlcmd" `
    -Argument "-S YOUR_SERVER -d MetadataRepository -U ai_user -P `"your_password`" -Q `"INSERT INTO dm_dq.scan_queue (scan_type, scan, table_name, status) VALUES ('stability', 'check', NULL, 'pending')`""
$trigger  = New-ScheduledTaskTrigger -Daily -At "08:00"
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Hours 1)
Register-ScheduledTask -TaskName "DataQuality_StabilityCheck" `
    -Action $action -Trigger $trigger -Settings $settings -RunLevel Highest -User "SYSTEM" -Force
```

> **Note**: When running as SYSTEM on a remote SQL Server, Windows credentials cannot be forwarded (Kerberos double-hop). Use SQL auth (`DB_USERNAME` / `DB_PASSWORD`) in `.env` instead.

**SQL permissions required for `ai_user`:**
```sql
-- Run once per source database being scanned
USE [SourceDatabase];
CREATE USER [ai_user] FOR LOGIN [ai_user];
GRANT SELECT ON SCHEMA::your_schema TO [ai_user];

-- Run once for MetadataRepository
USE MetadataRepository;
CREATE USER [ai_user] FOR LOGIN [ai_user];
GRANT SELECT, INSERT, UPDATE ON SCHEMA::dm_dq TO [ai_user];
GRANT SELECT, INSERT, UPDATE ON dbo.profiles   TO [ai_user];
```

---

## Validity Module

### How It Works

Two phases per table:

**1. Profile** — read the table (full scan or TOP N rows), compute statistical profiles per column, save to `dbo.profiles`.

**2. Scan** — load profiles, score every row for anomalies using a two-pass vectorized approach, save flagged rows and column-level details to DB.

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

Scores below `MIN_CONTRIBUTION` are recorded in details but don't accumulate into the row score. Rows with `row_score >= ROW_SCORE_THRESHOLD` are written to `dm_dq.validity_scan_row_data`.

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

### How It Works

Two operations, run separately on a schedule:

**1. Snapshot** (`scan = 'snapshot'`) — for every table registered in `dm_dq.stability_targets`, capture the current row count via `sys.partitions` (no full table scan) and write it to `dbo.row_count_snapshots`.

**2. Check** (`scan = 'check'`) — for every registered table, read the most recent snapshot, compare against the rolling baseline, flag anomalies, and write results to `dm_dq.row_count_runs`.

Snapshots run at **6:00 AM**, checks run at **8:00 AM** via Task Scheduler.

### Anomaly Detection

1. Load the last 30 snapshots from `dbo.row_count_snapshots`
2. Compute rolling mean and std dev of the history
3. Flag if Z-score ≥ 3.0
4. Fallback: flag if single-step change ≥ 50% when baseline is thin (< 3 snapshots) or has zero variance

The adaptive baseline absorbs normal organic growth — only sudden aggressive changes are flagged. Z-score becomes meaningful after ~3 snapshots, reliable after ~10.

### Registering Tables

```sql
INSERT INTO MetadataRepository.dm_dq.stability_targets (db_name, schema_name, table_name)
VALUES ('HRDM_DEV', 'DIMT', 'Employee'),
       ('HRDM_DEV', 'dbo',  'Employment');

-- Pause a table without removing it
UPDATE MetadataRepository.dm_dq.stability_targets
SET enabled = 0
WHERE table_name = 'Employment';
```

---

## Consistency Module

### Cross-System

Verifies that two tables across different databases or systems contain identical data. Designed for scenarios where System X sends data to System Y and you need to confirm the transfer was complete and accurate.

### How It Works

1. **Hash** — compute a SHA-256 hash per row on both sides in SQL (only `(key, hash)` pairs transferred over the network)
2. **Diff** — compare hash sets to find missing rows, extra rows, and modified rows
3. **Detail** — for modified rows only, fetch the actual data and compare column by column to identify exactly which cells differ

Up to 10,000 modified rows are stored in detail. The summary counts are always complete.

### Triggering a Check

```sql
-- Format: [source_db].[schema].[table]|[target_db].[schema].[table]|join_key_column
INSERT INTO MetadataRepository.dm_dq.scan_queue (scan_type, scan, table_name, status)
VALUES (
    'consistency',
    'cross_system',
    '[HRDM_DEV].[dbo].[Employee]|[STG].[dbo].[Employee]|EmployeeKey',
    'pending'
)
```

### Querying Results

```sql
-- Summary
SELECT * FROM MetadataRepository.dm_dq.consistency_runs ORDER BY scanned_at DESC;

-- All discrepancies for a run
SELECT * FROM MetadataRepository.dm_dq.consistency_result
WHERE run_id = 1
ORDER BY issue_type, join_key_value, column_name;
```
