# React Data Quality Dashboard

The dashboard frontend is implemented in React and served by the existing FastAPI application. The API remains read-only and continues to use the same PostgreSQL-backed endpoints.

## Run

The built bundle (`src/aridtrial/dashboard/static/`) is committed, so no Node is needed to run the dashboard:

```bash
pip install -e ".[dashboard]"
aridtrial dashboard      # needs ARIDTRIAL_DSN
```

There is a single frontend (Vite + React, no CDN, no in-browser Babel). It hosts two dashboards: **Data quality** (`#overview`)
and **Scientific & agronomic analysis** (`#/analysis/<section>`, see `SCIENTIFIC_DASHBOARD.md`).

## Build a self-contained production bundle

For deployments where browser access to a public CDN is not desired, build the frontend locally and commit/package the generated static assets:

```bash
cd frontend
npm install
npm run build
```

Vite writes the optimized bundle into `src/aridtrial/dashboard/static/`. The generated `index.html` references local bundled assets and no longer needs the CDN React runtime. Rebuild after changing files in `frontend/src/`.

## Development workflow

Run the API in one terminal:

```bash
aridtrial dashboard --reload
```

Run Vite in another terminal:

```bash
cd frontend
npm install
npm run dev
```

Open the Vite URL printed by the command. Requests under `/api` are proxied to FastAPI at `127.0.0.1:8000`.

## Frontend features

- Responsive workspace navigation and status summary.
- Raw, Core and Final volume indicators for each dataset.
- Source identity and quarantine visibility.
- Reconciliation results with expandable details.
- QC findings with severity filtering.
- Dataset search, refresh, loading, empty and database-error states.
- Read-only interactions; no data repair or mutation is exposed.

## API contract

The frontend uses these existing endpoints without changing their semantics:

- `GET /api/health`
- `GET /api/overview`
- `GET /api/reconciliation`
- `GET /api/qc-issues`
