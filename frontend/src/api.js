// Live mode: the FastAPI endpoints. Static mode (GitHub Pages): the same responses exported as JSON files at build time.
export const STATIC_MODE = import.meta.env.VITE_STATIC === '1';

/** api('/api/analysis/eda', 'run-id') -> URL to fetch. In static mode the run id is ignored (one frozen snapshot). */
export function api(path, runId) {
  if (STATIC_MODE) return `${import.meta.env.BASE_URL}${path.replace(/^\//, '')}.json`;
  return runId ? `${path}?run_id=${encodeURIComponent(runId)}` : path;
}
