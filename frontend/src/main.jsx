import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import { api, STATIC_MODE } from './api.js';
import './analysis.css';
import AnalysisPage, { TABS } from './AnalysisPage.jsx';

const number = new Intl.NumberFormat('en-US');
const dateTime = (value) => value ? new Date(value).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—';
const compact = (value) => number.format(Number(value || 0));

function Icon({ name, size = 18 }) {
  const common = { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true };
  const paths = {
    grid: <><rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/></>,
    database: <><ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/></>,
    layers: <><path d="m12 3 9 5-9 5-9-5 9-5Z"/><path d="m3 12 9 5 9-5M3 16l9 5 9-5"/></>,
    shield: <><path d="M12 3 20 6v5c0 5-3.4 8.5-8 10-4.6-1.5-8-5-8-10V6l8-3Z"/><path d="m9 12 2 2 4-4"/></>,
    alert: <><path d="M10.3 4.3 2.8 18a2 2 0 0 0 1.7 3h15a2 2 0 0 0 1.7-3L13.7 4.3a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4m0 4h.01"/></>,
    archive: <><rect x="3" y="4" width="18" height="4" rx="1"/><path d="M5 8v12h14V8m-9 4h4"/></>,
    refresh: <><path d="M20 7v5h-5M4 17v-5h5"/><path d="M5.5 9A7 7 0 0 1 18 6l2 6M4 12l2 6a7 7 0 0 0 12.5-3"/></>,
    chevron: <path d="m9 18 6-6-6-6"/>,
    check: <path d="m5 12 4 4L19 6"/>,
    search: <><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
    clock: <><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/></>,
    external: <><path d="M14 3h7v7m0-7-9 9"/><path d="M19 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h6"/></>,
    sprout: <><path d="M12 21v-9m0 3c-5 0-8-3-8-8 5 0 8 3 8 8Zm0-4c0-5 3-8 8-8 0 5-3 8-8 8Z"/></>,
  };
  return <svg {...common}>{paths[name] || paths.grid}</svg>;
}

function MetricCard({ label, value, note, icon, tone = 'neutral' }) {
  return <article className={`metric-card metric-${tone}`}>
    <div className="metric-top"><span>{label}</span><span className="metric-icon"><Icon name={icon} /></span></div>
    <div className="metric-value">{value == null ? '—' : compact(value)}</div>
    <div className="metric-note">{note}</div>
  </article>;
}

function Pill({ severity, children }) {
  const value = (severity || 'unknown').toLowerCase();
  return <span className={`pill pill-${value}`}>{children || value}</span>;
}

function SectionTitle({ eyebrow, title, detail, action }) {
  return <div className="section-title"><div><div className="eyebrow">{eyebrow}</div><h2>{title}</h2>{detail && <p>{detail}</p>}</div>{action}</div>;
}

function LoadingState({ label = 'Loading data…' }) {
  return <div className="loading-state"><span className="spinner" />{label}</div>;
}

function EmptyState({ title, detail }) {
  return <div className="empty-state"><div className="empty-icon"><Icon name="database" size={22} /></div><strong>{title}</strong><span>{detail}</span></div>;
}

function FlowBar({ raw, core, final }) {
  const max = Math.max(Number(raw || 0), Number(core || 0), Number(final || 0), 1);
  return <div className="flow-bars" aria-label={`Raw ${raw}, core ${core}, final ${final}`}>
    <div className="flow-track"><span className="flow-fill flow-raw" style={{ width: `${Math.max(2, Number(raw || 0) / max * 100)}%` }} /></div>
    <div className="flow-track"><span className="flow-fill flow-core" style={{ width: `${Math.max(2, Number(core || 0) / max * 100)}%` }} /></div>
    <div className="flow-track"><span className="flow-fill flow-final" style={{ width: `${Math.max(2, Number(final || 0) / max * 100)}%` }} /></div>
  </div>;
}

function QualityPage() {
  const [overview, setOverview] = useState(null);
  const [reconciliation, setReconciliation] = useState([]);
  const [issues, setIssues] = useState([]);
  const [health, setHealth] = useState('connecting');
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState('');
  const [updatedAt, setUpdatedAt] = useState(null);
  const [activeSection, setActiveSection] = useState('overview');
  const [datasetFilter, setDatasetFilter] = useState('all');
  const [severityFilter, setSeverityFilter] = useState('all');
  const [search, setSearch] = useState('');

  const load = useCallback(async (quiet = false) => {
    if (quiet) setRefreshing(true); else setLoading(true);
    setError('');
    try {
      const [healthResponse, overviewResponse, reconResponse, issuesResponse] = await Promise.all([
        fetch(api('/api/health')), fetch(api('/api/overview')), fetch(api('/api/reconciliation')), fetch(api('/api/qc-issues')),
      ]);
      const responses = [healthResponse, overviewResponse, reconResponse, issuesResponse];
      const failed = responses.find((response) => !response.ok);
      if (failed) {
        let message = `Request failed (${failed.status})`;
        try { const body = await failed.json(); message = body.detail || message; } catch { /* keep fallback */ }
        throw new Error(message);
      }
      const [healthData, overviewData, reconData, issueData] = await Promise.all(responses.map((response) => response.json()));
      setHealth(healthData.status === 'ok' ? 'connected' : 'degraded');
      setOverview(overviewData);
      setReconciliation(reconData);
      setIssues(issueData);
      setUpdatedAt(new Date());
    } catch (err) {
      setHealth('disconnected');
      setError(err.message || 'Unable to load dashboard data.');
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  useEffect(() => {
    const sections = ['overview', 'sources', 'reconciliation', 'quality'];
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting).sort((a, b) => b.intersectionRatio - a.intersectionRatio)[0];
      if (visible) setActiveSection(visible.target.id);
    }, { rootMargin: '-15% 0px -70% 0px', threshold: [0, 0.2, 0.5] });
    sections.forEach((id) => { const node = document.getElementById(id); if (node) observer.observe(node); });
    return () => observer.disconnect();
  }, [loading, overview]);

  const datasets = overview?.datasets || [];
  const totals = overview?.totals || {};
  const filteredDatasets = useMemo(() => datasets.filter((item) => {
    const text = `${item.label} ${item.dataset} ${item.source?.source_name || ''}`.toLowerCase();
    return (datasetFilter === 'all' || item.dataset === datasetFilter) && text.includes(search.toLowerCase());
  }), [datasets, datasetFilter, search]);
  const filteredIssues = useMemo(() => issues.filter((item) => severityFilter === 'all' || item.severity === severityFilter), [issues, severityFilter]);
  const reconFailures = reconciliation.filter((item) => item.severity === 'fail').length;
  const reconWarnings = reconciliation.filter((item) => item.severity === 'warn').length;
  const hasRun = Boolean(overview?.latest_reconciliation);

  const nav = [
    { id: 'overview', label: 'Overview', icon: 'grid' },
    { id: 'sources', label: 'Data sources', icon: 'database' },
    { id: 'reconciliation', label: 'Reconciliation', icon: 'layers' },
    { id: 'quality', label: 'Quality findings', icon: 'shield' },
  ];

  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#overview"><span className="brand-symbol"><Icon name="sprout" size={22} /></span><span><b>aridtrial</b><small>FIELD DATA PLATFORM</small></span></a>
      <div className="side-label">WORKSPACE</div>
      <nav className="side-nav" aria-label="Dashboard sections">
        {nav.map((item) => <a key={item.id} href={`#${item.id}`} className={activeSection === item.id ? 'active' : ''}><Icon name={item.icon} size={17} /><span>{item.label}</span>{item.id === 'quality' && issues.length > 0 && <em>{issues.length}</em>}</a>)}
      </nav>
      <div className="side-label" style={{ marginTop: 22 }}>ANALYSIS</div>
      <nav className="side-nav" aria-label="Analysis"><a href="#/analysis/eda"><Icon name="layers" size={17} /><span>Scientific &amp; agronomic</span></a></nav>
      <div className="sidebar-bottom">
        <div className="trial-card"><div className="trial-card-icon"><Icon name="sprout" size={17} /></div><b>Desert soil trial</b><span>Data quality workspace</span><div className="trial-card-meta"><span>RCBD</span><span>24 plots</span><span>6 blocks</span></div></div>
        <div className="sidebar-foot"><span className={`connection-dot ${health}`} />{health === 'connected' ? (STATIC_MODE ? 'Frozen demo snapshot' : 'Database connected') : health === 'connecting' ? 'Connecting…' : 'Database unavailable'}<small>Read-only access</small></div>
      </div>
    </aside>

    <main className="main-content">
      <header className="topbar"><div className="breadcrumb"><span>Workspace</span><Icon name="chevron" size={14} /><b>Data quality</b></div><div className="topbar-actions"><span className={`connection-status ${health}`}><i />{health === 'connected' ? (STATIC_MODE ? 'Static snapshot' : 'Live database') : health === 'connecting' ? 'Connecting' : 'Offline'}</span><button className="refresh-button" onClick={() => load(true)} disabled={refreshing}><Icon name="refresh" size={16} /><span>{refreshing ? 'Refreshing' : 'Refresh data'}</span></button><div className="avatar">AT</div></div></header>

      <section id="overview" className="page-section overview-section">
        <div className="page-heading"><div><div className="eyebrow">FIELD TRIAL DATA PLATFORM <span className="eyebrow-line" /></div><h1>Data quality overview</h1><p>Understand what arrived, what passed validation, and what is ready for analysis.</p></div><div className="last-updated"><span>LAST UPDATED</span><b>{updatedAt ? dateTime(updatedAt) : '—'}</b></div></div>

        {error && <div className="error-banner" role="alert"><Icon name="alert" size={18} /><div><b>Could not load live data</b><span>{error}</span></div><button onClick={() => load()}>Try again</button></div>}

        {loading && !overview ? <div className="panel"><LoadingState label="Connecting to PostgreSQL and loading quality metrics…" /></div> : <>
          <div className="metric-grid">
            <MetricCard label="Raw input rows" value={totals.raw_rows} note="Landed files and Kobo submissions" icon="database" />
            <MetricCard label="Core records" value={totals.core_rows} note="Standardized, traceable records" icon="layers" />
            <MetricCard label="Analysis-ready" value={totals.final_rows} note="QC-filtered marts and references" icon="check" tone="green" />
            <MetricCard label="Quarantined" value={totals.quarantined_rows} note="Isolated during ingestion" icon="archive" tone="amber" />
            <MetricCard label="QC failures" value={totals.qc_failures} note="Failed validation rules" icon="alert" tone="red" />
            <MetricCard label="QC warnings" value={totals.qc_warnings} note="Items requiring review" icon="shield" tone="amber" />
          </div>

          <section className="panel health-panel">
            <div className="health-summary"><div className="health-icon"><Icon name="shield" size={21} /></div><div><b>{health === 'connected' ? (reconFailures ? 'Some checks need attention' : hasRun ? 'Quality checks completed' : 'Database connected') : 'Waiting for database connection'}</b><span>{health === 'connected' ? (hasRun ? `Latest reconciliation: ${dateTime(overview?.latest_reconciliation?.started_at)}` : 'Run QC and reconciliation to establish a current quality baseline.') : 'The dashboard will show live metrics when PostgreSQL is reachable.'}</span></div></div>
            <div className="health-stats"><div><b>{hasRun ? compact(reconciliation.length) : '—'}</b><span>Reconciliation checks</span></div><div><b className="text-red">{hasRun ? compact(reconFailures) : '—'}</b><span>Failed checks</span></div><div><b className="text-amber">{hasRun ? compact(reconWarnings) : '—'}</b><span>Warnings</span></div></div>
          </section>
        </>}
      </section>

      <section id="sources" className="page-section">
        <SectionTitle eyebrow="SOURCE LINEAGE" title="Data sources & pipeline flow" detail="Follow each dataset from its original source into standardized and analysis-ready tables." action={<span className="section-counter">{datasets.length} DATASETS</span>} />
        <div className="panel source-panel">
          <div className="table-toolbar"><div className="legend"><span><i className="legend-raw" />Raw</span><span><i className="legend-core" />Core</span><span><i className="legend-final" />Final</span></div><div className="toolbar-controls"><div className="search-box"><Icon name="search" size={15} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search datasets or sources…" /></div><select aria-label="Filter dataset" value={datasetFilter} onChange={(event) => setDatasetFilter(event.target.value)}><option value="all">All datasets</option>{datasets.map((item) => <option key={item.dataset} value={item.dataset}>{item.label}</option>)}</select></div></div>
          <div className="data-table-wrap"><table className="data-table"><thead><tr><th>DATASET</th><th>SOURCE</th><th>RAW INPUT</th><th>CORE</th><th>FINAL LAYER</th><th>PIPELINE VOLUME</th><th>QUARANTINE</th><th>STATE</th></tr></thead><tbody>
            {loading && !datasets.length ? <tr><td colSpan="8"><LoadingState /></td></tr> : filteredDatasets.map((item) => {
              const failed = Number(item.reconciliation?.failed_checks || 0) + Number(item.qc?.failures || 0);
              const warnings = Number(item.reconciliation?.warning_checks || 0) + Number(item.qc?.warnings || 0);
              const qualityRunExists = Boolean(overview?.latest_qc || overview?.latest_reconciliation);
              const state = failed ? 'attention' : warnings ? 'warning' : item.source?.source_type === 'not registered' ? 'unregistered' : !qualityRunExists ? 'unverified' : item.raw_rows ? 'healthy' : 'no-data';
              const stateLabel = { attention: 'Needs attention', warning: 'Review', unregistered: 'Unregistered', unverified: 'Not checked', healthy: 'Healthy', 'no-data': 'No data' }[state];
              return <tr key={item.dataset}><td><div className="dataset-name">{item.label}</div><code>{item.dataset}</code></td><td><div className="source-name">{item.source?.source_name || '—'}</div><span className="source-type">{item.source?.source_type || 'unknown'}</span></td><td><b>{compact(item.raw_rows)}</b><small>{compact(item.raw_batches)} batches / submissions</small></td><td><b>{compact(item.core_rows)}</b></td><td><b>{compact(item.final_rows)}</b><small>{item.final_label}</small></td><td><FlowBar raw={item.raw_rows} core={item.core_rows} final={item.final_rows} /></td><td><span className={Number(item.quarantined_rows) ? 'quarantine-count has-count' : 'quarantine-count'}>{compact(item.quarantined_rows)}</span></td><td><Pill severity={state}>{stateLabel}</Pill></td></tr>;
            })}
            {!filteredDatasets.length && <tr><td colSpan="8"><EmptyState title="No matching datasets" detail="Try changing your search or dataset filter." /></td></tr>}
          </tbody></table></div>
          <div className="table-footnote"><Icon name="alert" size={15} /><span>Counts can have different grains. For example, one Kobo soil submission may normalize into several depth-layer records. Use reconciliation checks—not raw count differences alone—to identify data loss.</span></div>
        </div>
      </section>

      <section id="reconciliation" className="page-section">
        <SectionTitle eyebrow="CROSS-LAYER CONTROL" title="Reconciliation checks" detail="Compare expected and observed records across pipeline stages. A difference is a signal to investigate, not automatically an error." action={<Pill severity={hasRun ? reconFailures ? 'fail' : reconWarnings ? 'warn' : 'pass' : 'unknown'}>{hasRun ? reconFailures ? `${reconFailures} failed` : reconWarnings ? `${reconWarnings} warnings` : 'All checks passed' : 'Not run'}</Pill>} />
        <div className="panel">
          <div className="recon-overview"><div className="recon-run"><span className="mini-label">LATEST RECONCILIATION</span><b>{overview?.latest_reconciliation ? `Run #${overview.latest_reconciliation.run_id}` : 'No reconciliation run yet'}</b><small>{overview?.latest_reconciliation ? dateTime(overview.latest_reconciliation.started_at) : 'Run `aridtrial reconcile` to populate this section.'}</small></div><div className="recon-legend"><span><i className="legend-dot dot-pass" />Passed</span><span><i className="legend-dot dot-warn" />Warning</span><span><i className="legend-dot dot-fail" />Failed</span></div></div>
          <div className="data-table-wrap"><table className="data-table recon-table"><thead><tr><th>DATASET</th><th>CHECK</th><th>RESULT</th><th>EXPECTED / SOURCE</th><th>OBSERVED / TARGET</th><th>DIFFERENCE</th><th>DETAILS</th></tr></thead><tbody>
            {reconciliation.map((item, index) => <tr key={`${item.dataset}-${item.check_id}-${index}`}><td><span className="dataset-name">{item.dataset}</span></td><td><code>{item.check_id}</code></td><td><Pill severity={item.severity}>{item.severity}</Pill></td><td>{item.source_count == null ? '—' : compact(item.source_count)}</td><td>{item.target_count == null ? '—' : compact(item.target_count)}</td><td><b className={Number(item.difference) ? 'difference-value' : ''}>{item.difference == null ? '—' : compact(item.difference)}</b></td><td><details className="detail-pop"><summary>View details</summary><pre>{typeof item.details === 'string' ? item.details : JSON.stringify(item.details, null, 2)}</pre></details></td></tr>)}
            {!reconciliation.length && <tr><td colSpan="7"><EmptyState title="No reconciliation results" detail="Run the reconciliation command after ingestion and QC to see cross-layer checks here." /></td></tr>}
          </tbody></table></div>
        </div>
      </section>

      <section id="quality" className="page-section">
        <SectionTitle eyebrow="VALIDATION & EXCEPTIONS" title="Quality findings" detail="Inspect the rules that flagged records and identify where investigation is needed." action={<span className="section-counter">{issues.length} FINDINGS</span>} />
        <div className="quality-grid">
          <article className="panel quality-summary"><div className="quality-summary-head"><div><span className="mini-label">LATEST QC RUN</span><h3>{overview?.latest_qc ? `Run #${overview.latest_qc.run_id}` : 'No QC run recorded'}</h3><span>{overview?.latest_qc ? dateTime(overview.latest_qc.started_at) : 'Run `aridtrial qc` to populate findings.'}</span></div><div className="quality-shield"><Icon name="shield" size={23} /></div></div><div className="quality-counts"><div><span className="count-dot fail-dot" /><b>{compact(totals.qc_failures)}</b><small>Failures</small></div><div><span className="count-dot warn-dot" /><b>{compact(totals.qc_warnings)}</b><small>Warnings</small></div></div><div className="quality-note">QC findings describe rule evaluations. Quarantine records are ingestion exceptions; the two counts represent different stages and should not be added together.</div></article>
          <article className="panel findings-panel"><div className="findings-toolbar"><div><b>Record-level findings</b><span>Latest QC run · up to 500 records</span></div><select aria-label="Filter by severity" value={severityFilter} onChange={(event) => setSeverityFilter(event.target.value)}><option value="all">All severities</option><option value="fail">Failures</option><option value="warn">Warnings</option><option value="info">Info</option></select></div><div className="data-table-wrap"><table className="data-table findings-table"><thead><tr><th>DATASET / RECORD</th><th>RULE</th><th>SEVERITY</th><th>VARIABLE</th><th>OBSERVED VALUE</th><th>MESSAGE</th></tr></thead><tbody>
            {filteredIssues.map((item, index) => <tr key={`${item.dataset}-${item.record_key}-${item.rule_id}-${index}`}><td><b className="dataset-name">{item.dataset}</b><small className="record-key">{item.record_key || item.plot_id || '—'}</small></td><td><code>{item.rule_id}</code></td><td><Pill severity={item.severity}>{item.severity}</Pill></td><td>{item.variable || '—'}</td><td>{item.value == null ? '—' : String(item.value)}</td><td className="message-cell">{item.message || '—'}</td></tr>)}
            {!filteredIssues.length && <tr><td colSpan="6"><EmptyState title={issues.length ? 'No findings for this filter' : 'No QC findings'} detail={issues.length ? 'Choose another severity to see more results.' : 'No findings were returned for the latest QC run.'} /></td></tr>}
          </tbody></table></div></article>
        </div>
      </section>

      <footer className="app-footer"><span><span className="footer-mark"><Icon name="sprout" size={14} /></span> AridTrial <i>·</i> Data quality & reconciliation</span><span>Read-only dashboard <i>·</i> PostgreSQL-backed</span></footer>
    </main>
  </div>;
}

function AnalysisShell({ tab, setTab }) {
  return <div className="app-shell">
    <aside className="sidebar">
      <a className="brand" href="#overview"><span className="brand-symbol"><Icon name="sprout" size={22} /></span><span><b>aridtrial</b><small>FIELD DATA PLATFORM</small></span></a>
      <div className="side-label">DASHBOARDS</div>
      <nav className="side-nav" aria-label="Dashboards">
        <a href="#overview"><Icon name="database" size={17} /><span>Data quality</span></a>
        <a href="#/analysis/eda" className="active"><Icon name="layers" size={17} /><span>Scientific &amp; agronomic</span></a>
      </nav>
      <div className="side-label" style={{ marginTop: 22 }}>SECTIONS</div>
      <nav className="side-nav an-side" aria-label="Analysis sections">
        {TABS.map((t) => <a key={t.id} href={`#/analysis/${t.id}`} className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}><span>{t.label}</span></a>)}
      </nav>
      <div className="sidebar-bottom"><div className="trial-card"><div className="trial-card-icon"><Icon name="sprout" size={17} /></div><b>Desert soil trial</b><span>Synthetic demonstration data</span><div className="trial-card-meta"><span>RCBD</span><span>24 plots</span><span>6 blocks</span></div></div></div>
    </aside>
    <main className="main-content"><section className="page-section an-page"><AnalysisPage tab={tab} setTab={setTab} /></section></main>
  </div>;
}

const parseHash = () => { const m = /^#\/analysis\/(\w+)/.exec(window.location.hash); return m && TABS.some((t) => t.id === m[1]) ? m[1] : window.location.hash.startsWith('#/analysis') ? 'eda' : null; };

function Root() {
  const [tab, setTabState] = useState(parseHash());
  useEffect(() => { const on = () => setTabState(parseHash()); window.addEventListener('hashchange', on); return () => window.removeEventListener('hashchange', on); }, []);
  const setTab = useCallback((t) => { window.location.hash = `#/analysis/${t}`; setTabState(t); }, []);
  return tab ? <AnalysisShell tab={tab} setTab={setTab} /> : <QualityPage />;
}

createRoot(document.getElementById('root')).render(<React.StrictMode><Root /></React.StrictMode>);
