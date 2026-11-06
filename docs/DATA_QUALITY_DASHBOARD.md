# Data Quality Dashboard

The dashboard provides read-only visibility into source registration, raw landing, typed core tables, QC-filtered marts, quarantine, QC findings and the latest reconciliation run.

## Install and start

Install the optional dashboard dependencies:

```bash
pip install -e ".[dashboard]"
```

Configure the same PostgreSQL connection used by the pipeline:

```bash
# PowerShell
$env:ARIDTRIAL_DSN = "postgresql+psycopg2://USER:PASSWORD@HOST:5432/DATABASE"

# Bash
export ARIDTRIAL_DSN="postgresql+psycopg2://USER:PASSWORD@HOST:5432/DATABASE"
```

Initialize or migrate the schema if needed, then run the pipeline stages:

```bash
aridtrial init-db
aridtrial simulate
aridtrial ingest
aridtrial qc
aridtrial reconcile
```

Start the dashboard:

```bash
aridtrial dashboard
```

Open `http://127.0.0.1:8000`. For a remote development VM, use `aridtrial dashboard --host 0.0.0.0 --port 8000` and expose the port only through a trusted network or authenticated reverse proxy. The demo app does not implement user authentication; do not expose it directly to the public internet.

## API

- `GET /api/health`: database connectivity check.
- `GET /api/overview`: source registry, raw/core/final counts, quarantine and QC summary.
- `GET /api/reconciliation`: checks from the latest reconciliation run.
- `GET /api/qc-issues`: up to 500 findings from the latest QC run.
- `GET /docs`: interactive OpenAPI documentation.

## How to interpret the counts

- **Raw input rows** are rows represented by registered file drops or individual Kobo submissions. For file drops, the dashboard sums the `n_rows` recorded in `raw.file_drop`; for Kobo, it counts submissions in `raw.kobo_submission`.
- **Core records** are records in the typed, lineage-carrying table.
- **Final records** are records in the relevant QC-filtered mart. For reference datasets or plot layout, the final layer is the core reference table itself.
- **Quarantine** counts records in `audit.quarantine` associated with the dataset or source dataset.

These values do not always share the same grain. For example, one Kobo soil submission can produce multiple depth-layer records. Therefore, a raw-to-core count difference is not automatically a data-loss issue; use the reconciliation checks and their details to interpret it.

The source identity shown is the latest registered source contract row for each dataset. A source can be registered as absent or optional without having landed records.

## Safety and scope

The dashboard is read-only. It does not alter raw, core, QC or reconciliation records and does not attempt automatic repair. QC findings and reconciliation details are displayed for investigation. Fixes should happen through a reviewed source correction or pipeline change, followed by re-ingestion/QC/reconciliation as appropriate.

The dashboard uses a responsive React frontend served by the existing FastAPI application. The packaged development entry point loads React and Babel from a CDN; a self-contained Vite production bundle can be generated locally. See [`REACT_DASHBOARD.md`](REACT_DASHBOARD.md) for both workflows.
