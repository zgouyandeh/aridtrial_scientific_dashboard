import React, { useEffect, useMemo, useState } from 'react';
import { api } from './api.js';
import { BoxStrip, Bars, Forest, Heatmap, Histogram, LayoutMap, LineChart, Scatter, ShareBar, TREATMENTS, TRT_COLOR, TRT_LABEL, TrtLegend, Legend, fmt, fmtP } from './charts.jsx';

const DAYS = { T0: 0, T1: 91, T2: 182, T3: 273, T4: 364 };
const dayTick = (v) => `d${Math.round(v)}`;
const campTicks = Object.values(DAYS);
const dt = (v) => (v ? new Date(v).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' }) : '—');

export const TABS = [
  { id: 'eda', label: 'Exploratory analysis' },
  { id: 'design', label: 'Experimental design' },
  { id: 'comparison', label: 'Treatment comparison' },
  { id: 'models', label: 'Mixed-effects models' },
  { id: 'inference', label: 'Statistical inference' },
  { id: 'power', label: 'Power & sensitivity' },
  { id: 'agronomy', label: 'Agronomy & environment' },
  { id: 'survival', label: 'Survival' },
  { id: 'evaluation', label: 'Validation' },
];

// ---------------------------------------------------------------- data access
const cache = new Map();
function useSection(section, runId) {
  const key = `${runId || 'latest'}:${section}`;
  const [state, setState] = useState(() => cache.get(key) || { status: 'loading' });
  useEffect(() => {
    if (cache.has(key)) { setState(cache.get(key)); return undefined; }
    let live = true;
    setState({ key: null, status: 'loading' });
    fetch(api(`/api/analysis/${section}`, runId))
      .then(async (r) => {
        if (!r.ok) { let d = `Request failed (${r.status})`; try { d = (await r.json()).detail || d; } catch { /* keep */ } throw Object.assign(new Error(d), { status: r.status }); }
        return r.json();
      })
      .then((j) => { const s = { status: 'ok', key, ...j }; cache.set(key, s); if (live) setState(s); })
      .catch((e) => { if (live) setState({ key, status: e.status === 404 ? 'empty' : 'error', message: e.message }); });
    return () => { live = false; };
  }, [key, section, runId]);
  return state.key === key ? state : { status: 'loading' };
}

// ---------------------------------------------------------------- shared UI
const Card = ({ title, sub, children, wide, actions }) => <article className={`panel an-card${wide ? ' an-wide' : ''}`}>
  <div className="an-card-head"><div><h3>{title}</h3>{sub && <p>{sub}</p>}</div>{actions}</div>{children}</article>;
const Stat = ({ label, value, note, tone }) => <div className={`an-stat${tone ? ` an-${tone}` : ''}`}><span>{label}</span><b>{value}</b>{note && <small>{note}</small>}</div>;
const Badge = ({ tone = 'neutral', children }) => <span className={`an-badge an-b-${tone}`}>{children}</span>;
const Notes = ({ items }) => items?.length ? <ul className="an-notes">{items.map((n, i) => <li key={i}>{n}</li>)}</ul> : null;
function Select({ value, onChange, options, label }) {
  return <label className="an-select"><span>{label}</span><select value={value} onChange={(e) => onChange(e.target.value)}>{options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}</select></label>;
}
function Table({ cols, rows, dense }) {
  return <div className="data-table-wrap"><table className={`data-table an-table${dense ? ' an-dense' : ''}`}>
    <thead><tr>{cols.map((c, i) => <th key={i} style={{ textAlign: c.right ? 'right' : 'left' }}>{c.h}</th>)}</tr></thead>
    <tbody>{rows.map((r, i) => <tr key={i}>{cols.map((c, j) => <td key={j} style={{ textAlign: c.right ? 'right' : 'left' }}>{c.f(r)}</td>)}</tr>)}</tbody></table></div>;
}
const Sig = ({ p }) => p == null ? '—' : <Badge tone={p < 0.05 ? 'good' : 'neutral'}>{fmtP(p)}</Badge>;

function Gate({ state, children }) {
  if (state.status === 'loading') return <div className="panel"><div className="loading-state"><span className="spinner" />Loading stored analysis…</div></div>;
  if (state.status === 'empty') return <div className="panel"><div className="empty-state"><strong>No analysis run stored yet</strong><span>Run <code>aridtrial analyze</code> (or <code>aridtrial demo</code>) so the results are computed and persisted, then refresh.</span></div></div>;
  if (state.status === 'error') return <div className="error-banner" role="alert"><div><b>Could not load this section</b><span>{state.message}</span></div></div>;
  return children;
}

const respOptions = (resp, only) => Object.entries(resp).filter(([k]) => !only || only.includes(k)).map(([k, v]) => ({ value: k, label: `${v.label}${v.unit ? ` (${v.unit})` : ''}` }));
const unitOf = (resp, k) => resp?.[k]?.unit || '';
const se = (r) => (r.sd != null && r.n > 0 ? r.sd / Math.sqrt(r.n) : null);

// ---------------------------------------------------------------- 1 EDA
function EDA({ d }) {
  const [resp, setResp] = useState('soc_pct');
  const [corr, setCorr] = useState('within_block');
  const rows = d.summary.filter((r) => r.response === resp);
  const series = TREATMENTS.map((t) => ({
    key: t, label: TRT_LABEL[t], color: TRT_COLOR[t],
    points: rows.filter((r) => r.treatment === t).map((r) => ({ x: DAYS[r.campaign], y: r.mean, lo: se(r) != null ? r.mean - 1.96 * se(r) : undefined, hi: se(r) != null ? r.mean + 1.96 * se(r) : undefined })),
  }));
  const boxes = TREATMENTS.map((t) => {
    const b = d.boxplots.find((x) => x.response === resp && x.treatment === t) || {};
    return { key: t, label: TRT_LABEL[t], color: TRT_COLOR[t], stats: b, points: (b.points || []).map((p) => ({ value: p.value, tip: `${p.plot_id} (block ${p.block}): ${fmt(p.value)}` })) };
  });
  const miss = d.missingness.filter((m) => m.response === resp);
  const c = d.correlation;
  return <>
    <div className="an-toolbar"><Select label="Response" value={resp} onChange={setResp} options={respOptions(d.responses)} /><TrtLegend /></div>
    <div className="an-grid">
      <Card title="Trajectory by treatment" sub="Mean per campaign with approximate 95 % band (±1.96 SE of the plot values). Descriptive only.">
        <LineChart series={series} xTicks={campTicks} xFmt={dayTick} xLabel="Days since trial start" yLabel={unitOf(d.responses, resp)} label={`Trajectory of ${resp}`} />
      </Card>
      <Card title="Endpoint distribution (T4)" sub="Box = quartiles, whiskers = 1.5×IQR, dots = individual plots.">
        <BoxStrip boxes={boxes} yLabel={unitOf(d.responses, resp)} label={`Endpoint distribution of ${resp}`} />
      </Card>
      <Card title="Correlation of plot-level endpoint variables" sub={`n = ${c.n} plots · exploratory, no multiplicity control`} actions={<Select label="Type" value={corr} onChange={setCorr} options={[{ value: 'within_block', label: 'Within block' }, { value: 'raw', label: 'Raw' }]} />}>
        <Heatmap rows={c.labels} cols={c.labels} values={c[corr]} diverging label="Correlation matrix" />
        <p className="an-hint">{c.note}</p>
      </Card>
      <Card title="Baseline SOC distribution (T0)" sub="Shape and spread of the starting condition, pre-treatment.">
        <Histogram counts={d.baseline_soc_hist.counts} edges={d.baseline_soc_hist.edges} xLabel="SOC (%)" label="Baseline SOC histogram" />
      </Card>
      <Card title="Block × treatment pattern (SOC, T4)" sub="One observation per cell: descriptive pattern, not an estimable interaction.">
        <Heatmap rows={d.block_treatment_heatmap.rows} cols={d.block_treatment_heatmap.cols} values={d.block_treatment_heatmap.values} rowLabel={(r) => `Block ${r}`} digits={3} label="Block by treatment heatmap" />
      </Card>
      <Card title="Records excluded by QC" sub="Expected vs observed plots per campaign (flagged values are excluded from analysis, never edited).">
        <Table dense cols={[{ h: 'Campaign', f: (r) => r.campaign }, { h: 'Expected', f: (r) => r.expected, right: true }, { h: 'Observed', f: (r) => r.observed, right: true }, { h: 'Excluded', f: (r) => r.excluded_by_qc ? <Badge tone="warn">{r.excluded_by_qc}</Badge> : 0, right: true }]} rows={miss} />
      </Card>
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 2 Design
function Design({ d }) {
  const g = d.design;
  return <>
    <div className="an-stats">
      <Stat label="Design" value={g.type} note="randomised complete block" />
      <Stat label="Blocks × treatments" value={`${g.blocks} × ${g.treatments}`} note={`${g.plots} plots`} />
      <Stat label="Error df" value={g.df.error} note={`block ${g.df.block} · treatment ${g.df.treatment} · total ${g.df.total}`} />
      <Stat label="Blocking efficiency" value={`${fmt(d.blocking.relative_efficiency_vs_crd, 2)}×`} note="vs completely randomised" tone={d.blocking.relative_efficiency_vs_crd > 1 ? 'good' : 'warn'} />
      <Stat label="Block ICC (baseline)" value={fmt(d.blocking.icc_block_baseline, 2)} note="share of T0 variance between blocks" />
    </div>
    <div className="an-grid">
      <Card title="Field layout" sub={`${g.plot_size_m[0]} × ${g.plot_size_m[1]} m plots, ${g.trees_per_plot} trees per plot. Colour = treatment.`}>
        <LayoutMap plots={d.layout} plotW={g.plot_size_m[0]} plotL={g.plot_size_m[1]} /><TrtLegend />
      </Card>
      <Card title="Randomisation balance" sub="Each treatment appears exactly once in each block (RCBD constraint).">
        <Heatmap rows={d.balance_matrix.rows} cols={d.balance_matrix.cols} values={d.balance_matrix.values} rowLabel={(r) => `Block ${r}`} digits={0} label="Treatment counts per block" />
        <p className="an-hint">Experimental unit: <b>plot</b>. Trees, soil layers and visits are subsamples / repeated measures and never counted as replicates.</p>
      </Card>
      <Card title="Baseline balance (T0)" sub="Block-adjusted ANOVA on pre-treatment values. A sanity check on the allocation, not proof that randomisation 'worked'." wide>
        <Table dense cols={[{ h: 'Response', f: (r) => r.response }, ...TREATMENTS.map((t) => ({ h: t, f: (r) => fmt(r.means[t], 3), right: true })),
          { h: 'Max std. diff', f: (r) => fmt(r.max_std_diff, 2), right: true }, { h: 'F', f: (r) => fmt(r.F, 2), right: true }, { h: 'p', f: (r) => <Sig p={r.p} />, right: true }]} rows={d.baseline_balance} />
      </Card>
      <Card title="Variance structure at baseline" sub="Feeds the power analysis (design-stage assumptions are replaced by these estimates).">
        <div className="an-kv"><span>Block SD</span><b>{fmt(d.baseline_variance.sd_block, 4)}</b><span>Residual SD</span><b>{fmt(d.baseline_variance.sd_resid, 4)}</b><span>Mean square: block / error</span><b>{fmt(d.blocking.ms_block, 4)} / {fmt(d.blocking.ms_error, 5)}</b><span>Plots used</span><b>{d.baseline_variance.n}</b></div>
      </Card>
      <Card title="Satellite pixel purity" sub="How many pure 10 m Sentinel-2 pixels fit in a plot (limits plot-level remote sensing).">
        <div className="an-kv">{Object.entries(d.pixel).map(([k, v]) => <React.Fragment key={k}><span>{k.replace(/_/g, ' ').replace(/\bge1\b/, '≥ 1').replace(/\bpx\b/, 'pixels').replace('nobuf', 'no buffer').replace('buf5', '5 m buffer')}</span><b>{fmt(v, Number.isInteger(v) ? 0 : 1)}</b></React.Fragment>)}</div>
      </Card>
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 3 Comparison
function Comparison({ d }) {
  const [resp, setResp] = useState('soc_pct');
  const emm = d.emm.filter((r) => r.response === resp);
  const series = TREATMENTS.map((t) => ({ key: t, label: TRT_LABEL[t], color: TRT_COLOR[t], points: emm.filter((r) => r.treatment === t).map((r) => ({ x: DAYS[r.campaign], y: r.emm, lo: r.lo, hi: r.hi })) }));
  const eff = d.endpoint_effects.filter((e) => e.response === resp);
  const anc = (Array.isArray(d.ancova) ? d.ancova : []).filter((a) => a.response === resp);
  const stdRows = d.endpoint_effects.filter((e) => e.contrast.startsWith('COMP_BIOC') || true).map((e) => ({ label: `${d.responses[e.response].label.replace(' (plot mean)', '')} · ${e.contrast.split(' - ')[0]}`, est: e.std_effect, lo: e.std_lo, hi: e.std_hi, color: TRT_COLOR[e.contrast.split(' - ')[0]], note: `p(Holm)=${fmtP(e.p_holm)}` }));
  return <>
    <div className="an-toolbar"><Select label="Response" value={resp} onChange={setResp} options={respOptions(d.responses)} /><TrtLegend /></div>
    <div className="an-grid">
      <Card title="Model-adjusted means (EMM)" sub="Estimated marginal means from the mixed model with 95 % CI (RCBD error df).">
        <LineChart series={series} xTicks={campTicks} xFmt={dayTick} xLabel="Days since trial start" yLabel={unitOf(d.responses, resp)} label="Estimated marginal means" />
      </Card>
      <Card title="Treatment − control at endpoint" sub="Difference with 95 % CI; dashed line = no difference.">
        <Forest rows={eff.map((e) => ({ label: TRT_LABEL[e.contrast.split(' - ')[0]], est: e.estimate, lo: e.lo, hi: e.hi, color: TRT_COLOR[e.contrast.split(' - ')[0]], note: `p(Holm)=${fmtP(e.p_holm)}` }))} xLabel={`Difference (${unitOf(d.responses, resp)})`} label="Endpoint contrasts" />
        <Table dense cols={[{ h: 'Contrast', f: (e) => e.contrast }, { h: 'Estimate', f: (e) => fmt(e.estimate), right: true }, { h: '% vs control', f: (e) => `${fmt(e.pct_vs_control, 1)} %`, right: true }, { h: 'Std. effect', f: (e) => fmt(e.std_effect, 2), right: true }, { h: 'p (Holm)', f: (e) => <Sig p={e.p_holm} />, right: true }]} rows={eff} />
      </Card>
      <Card title="Sensitivity: baseline-adjusted ANCOVA" sub="Plot-level model: endpoint ~ block + treatment + baseline. Agreement with the mixed model supports robustness." >
        <Table dense cols={[{ h: 'Contrast', f: (e) => e.contrast }, { h: 'Estimate', f: (e) => fmt(e.estimate), right: true }, { h: '95 % CI', f: (e) => `[${fmt(e.lo)}, ${fmt(e.hi)}]`, right: true }, { h: 'p (Holm)', f: (e) => <Sig p={e.p_holm} />, right: true }, { h: 'Model', f: (e) => <small>{e.model}</small> }]} rows={anc} />
      </Card>
      <Card title="Standardised effects across responses" sub="Estimate ÷ plot-level SD with 95 % CI: puts different units on one scale." wide>
        <Forest rows={stdRows} xLabel="Standardised difference (SD units)" label="Standardised effects" rowH={24} />
      </Card>
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 4 Models
function Models({ d }) {
  const [resp, setResp] = useState('soc_pct');
  const m = d.models.find((x) => x.response === resp) || d.models[0];
  const v = m.variance.var;
  const qq = m.diagnostics.qq.map((p) => ({ x: p.theoretical, y: p.sample, color: '#2f6f8f', tip: `theoretical ${fmt(p.theoretical, 2)}, sample ${fmt(p.sample, 2)}` }));
  const rvf = m.diagnostics.resid_vs_fitted.map((p) => ({ x: p.fitted, y: p.resid, color: '#315e49', tip: `fitted ${fmt(p.fitted)}, std. residual ${fmt(p.resid, 2)}` }));
  const blup = Object.entries(m.block_blup);
  return <>
    <div className="an-toolbar"><Select label="Response" value={m.response} onChange={setResp} options={d.models.map((x) => ({ value: x.response, label: `${x.label}${x.unit ? ` (${x.unit})` : ''}` }))} /><Badge>{m.method}</Badge>{m.converged ? <Badge tone="good">converged</Badge> : <Badge tone="warn">not converged</Badge>}{m.boundary && <Badge tone="warn">variance at boundary</Badge>}</div>
    <p className="an-formula"><code>{m.formula}</code> · n = {m.n_obs} observations · {m.n_blocks} blocks</p>
    <div className="an-stats">
      <Stat label="Treatment × campaign" value={`F = ${fmt(m.interaction_wald.F, 2)}`} note={`df ${m.interaction_wald.df}, ${m.interaction_wald.df2} · p ${fmtP(m.interaction_wald.p_F)}`} />
      <Stat label="Treatment effect at T2 (interim)" value={`F = ${fmt(m.mid_joint_F.F, 2)}`} note={`p ${fmtP(m.mid_joint_F.p)}`} />
      <Stat label="Treatment effect at T4" value={`F = ${fmt(m.endpoint_joint_F.F, 2)}`} note={`df ${m.endpoint_joint_F.df1}, ${m.endpoint_joint_F.df2} · p ${fmtP(m.endpoint_joint_F.p)}`} tone={m.endpoint_joint_F.p < 0.05 ? 'good' : undefined} />
      <Stat label="ICC block / plot" value={`${fmt(m.variance.icc_block, 2)} / ${fmt(m.variance.icc_plot, 2)}`} note="share of total variance" />
    </div>
    <div className="an-grid">
      <Card title="Variance components" sub="Where the variability lives: between blocks, between plots within blocks, and residual (visit-to-visit / measurement).">
        <ShareBar parts={[{ key: 'b', label: 'Block', value: v.block, color: '#2f6f8f' }, { key: 'p', label: 'Plot', value: v.plot, color: '#c27c2c' }, { key: 'r', label: 'Residual', value: v.resid, color: '#9aa39c' }]} />
        <div className="an-kv"><span>SD block</span><b>{fmt(m.variance.sd.block, 4)}</b><span>SD plot</span><b>{fmt(m.variance.sd.plot, 4)}</b><span>SD residual</span><b>{fmt(m.variance.sd.resid, 4)}</b></div>
        {blup.length > 0 && <><h4 className="an-h4">Block effects (BLUPs)</h4><Forest rows={blup.map(([k, val]) => ({ label: `Block ${k}`, est: val, lo: val, hi: val, color: val >= 0 ? '#2e7d5b' : '#b44f48' }))} xLabel={`Shrunken block effect (${m.unit})`} label="Block BLUPs" rowH={22} /></>}
      </Card>
      <Card title="Fixed effects" sub="Asymptotic Wald z (anti-conservative with 6 blocks): use the contrasts on the inference page for decisions.">
        <Table dense cols={[{ h: 'Term', f: (r) => <code>{r.term.replace("C(trt, Treatment('CTRL'))", 'trt').replace('C(time)', 'time')}</code> }, { h: 'Estimate', f: (r) => fmt(r.estimate, 4), right: true }, { h: 'SE', f: (r) => fmt(r.se, 4), right: true }, { h: 'z', f: (r) => fmt(r.z, 2), right: true }, { h: 'p (z)', f: (r) => fmtP(r.p_wald_z), right: true }]} rows={m.fixed_effects} />
      </Card>
      <Card title="Normal Q–Q of residuals" sub={`Shapiro–Wilk W = ${fmt(m.diagnostics.shapiro_W, 3)}, p = ${fmtP(m.diagnostics.shapiro_p)}`}>
        <Scatter points={qq} diagonal xLabel="Theoretical quantiles" yLabel="Standardised residuals" label="QQ plot" />
      </Card>
      <Card title="Residuals vs fitted" sub={`Levene across treatments: W = ${fmt(m.diagnostics.levene_W, 2)}, p = ${fmtP(m.diagnostics.levene_p)}. Look for funnels or curvature.`}>
        <Scatter points={rvf} hline={0} xLabel="Fitted" yLabel="Standardised residuals" label="Residuals vs fitted" />
      </Card>
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 5 Inference
function Inference({ d, resp: responses }) {
  const [review, setReview] = useState('endpoint');
  const [resp, setResp] = useState('soc_pct');
  const rows = d.contrasts.filter((c) => c.review === review && c.response === resp);
  const label = (c) => `${TRT_LABEL[c.contrast.split(' - ')[0]]}${c.depth && c.depth !== 'trees (plot means)' ? ` · ${c.depth}` : ''}`;
  return <>
    <div className="an-grid">
      <Card title="Multiplicity & decision rules" wide>
        <div className="an-rules"><div><b>Within response</b><span>{d.multiplicity.within}</span></div><div><b>Across responses</b><span>{d.multiplicity.across}</span></div><div><b>Interim review</b><span>{d.multiplicity.mid_trial}</span></div></div>
      </Card>
    </div>
    <div className="an-toolbar"><Select label="Review" value={review} onChange={setReview} options={[{ value: 'endpoint', label: 'Endpoint (T4)' }, { value: 'mid-trial', label: 'Mid-trial (T2, interim)' }]} />
      <Select label="Response" value={resp} onChange={setResp} options={[...new Set(d.contrasts.map((c) => c.response))].map((k) => ({ value: k, label: responses?.[k]?.label || k }))} /></div>
    <div className="an-grid">
      <Card title="Contrasts vs control" sub="Mixed-model Wald-t, RCBD error df (15), Holm within family.">
        <Forest rows={rows.map((c) => ({ label: label(c), est: c.estimate, lo: c.lo, hi: c.hi, color: TRT_COLOR[c.contrast.split(' - ')[0]], note: `p(Holm)=${fmtP(c.p_holm)}` }))} xLabel="Difference vs control" label="Contrast forest" />
      </Card>
      <Card title="Test table" sub={review === 'endpoint' && resp === 'soc_pct' ? 'SOC at the endpoint (0–15 cm) is the pre-specified primary outcome.' : 'Secondary / exploratory result.'}>
        <Table dense cols={[{ h: 'Contrast', f: (c) => <>{c.contrast} {c.is_primary && <Badge tone="good">primary</Badge>}</> }, { h: 'Depth', f: (c) => c.depth }, { h: 'Estimate', f: (c) => fmt(c.estimate), right: true }, { h: 'p', f: (c) => fmtP(c.p), right: true }, { h: 'p (Holm)', f: (c) => <Sig p={c.p_holm} />, right: true }]} rows={rows} />
      </Card>
      <Card title="Design-based check: within-block randomisation test" sub="Treatment labels permuted within blocks (the actual randomisation). Compare with the model-based decision." wide>
        <Table dense cols={[{ h: 'Response', f: (r) => r.response }, { h: 'Statistic', f: (r) => <small>{r.statistic}</small> }, { h: 'Contrast', f: (r) => r.contrast }, { h: 'Estimate', f: (r) => fmt(r.estimate), right: true }, { h: 'p perm (Holm)', f: (r) => <Sig p={r.p_perm_holm} />, right: true },
          { h: 'p model (Holm)', f: (r) => { const a = d.agreement.find((x) => x.response === r.response && x.contrast === r.contrast); return a ? <Sig p={a.p_model_holm} /> : '—'; }, right: true },
          { h: 'Same decision', f: (r) => { const a = d.agreement.find((x) => x.response === r.response && x.contrast === r.contrast); return a ? (a.same_decision ? <Badge tone="good">agree</Badge> : <Badge tone="warn">differ</Badge>) : '—'; } }]} rows={d.randomisation_tests} />
        <p className="an-hint">Where decisions differ, the borderline result is fragile: report both, and prefer the more conservative reading.</p>
      </Card>
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 6 Power
function Power({ d }) {
  const scen = [...new Set(d.mdd.map((m) => m.scenario))];
  const sc = ['#315e49', '#2f6f8f', '#b44f48'];
  const mdd = scen.map((s, i) => ({ key: s, label: s, color: sc[i % 3], points: d.mdd.filter((m) => m.scenario === s).map((m) => ({ x: m.n_blocks, y: m.mdd_80 })) }));
  const blocks = [...new Set(d.power_curves.map((p) => p.n_blocks))];
  const pc = blocks.map((b, i) => ({ key: `b${b}`, label: `${b} blocks`, color: ['#9aa39c', '#315e49', '#2f6f8f', '#c27c2c'][i % 4], points: d.power_curves.filter((p) => p.n_blocks === b).map((p) => ({ x: p.delta, y: p.power })) }));
  return <>
    <div className="an-stats">{Object.entries(d.sigma).map(([k, v]) => <Stat key={k} label={`σ · ${k}`} value={fmt(v, 4)} note="plot-level SD of SOC (%)" />)}<Stat label="Design blocks" value={d.design_blocks} note="planned replication" /></div>
    <div className="an-grid">
      <Card title="Minimum detectable difference (80 % power)" sub="Per treatment-vs-control contrast, Bonferroni for 3 comparisons. Linear in σ, so variance uncertainty matters most.">
        <LineChart series={mdd} xLabel="Number of blocks" yLabel="MDD (SOC %-points)" xTicks={[3, 4, 5, 6, 7, 8, 9, 10, 11, 12]} label="MDD vs blocks" />
        <Legend items={mdd.map((s) => ({ key: s.key, color: s.color, label: s.label }))} />
      </Card>
      <Card title="Power curve vs true effect" sub="Analytic (non-central t) at the design σ. Dashed line = 80 %.">
        <LineChart series={pc} xLabel="True difference (SOC %-points)" yLabel="Power" yDomain={[0, 1.02]} refY={0.8} markers={false} label="Power curves" />
        <Legend items={pc.map((s) => ({ key: s.key, color: s.color, label: s.label }))} />
      </Card>
      <Card title="Blocks needed for 80 % power" sub="Smallest number of blocks reaching 80 % per effect size and variance scenario.">
        <Table dense cols={[{ h: 'True Δ (%-pts)', f: (r) => r.delta }, { h: 'Scenario', f: (r) => r.scenario }, { h: 'Blocks', f: (r) => <b>{r.blocks}</b>, right: true }]} rows={d.blocks_needed} />
      </Card>
      <Card title="Monte-Carlo power (production mixed model)" sub="Simulated trials analysed with the same model and Holm rule used on the real data.">
        <Table dense cols={[{ h: 'Blocks', f: (r) => r.n_blocks, right: true }, { h: 'Treatment', f: (r) => r.treatment }, { h: 'Power', f: (r) => fmt(r.power, 2), right: true }, { h: 'Fits ok', f: (r) => r.n_sim_ok, right: true }]} rows={d.monte_carlo} />
        <h4 className="an-h4">Assumed true effects (planning values)</h4><div className="an-kv">{Object.entries(d.assumed_effects).map(([k, v]) => <React.Fragment key={k}><span>{k}</span><b>+{fmt(v, 2)} %-pts</b></React.Fragment>)}</div>
      </Card>
      <Card title="Survival power" sub="Simulated Cox analysis, 78 % vs 93 % survival. Few blocks mean low power for binary outcomes.">
        <Bars bars={d.survival.map((s) => ({ key: s.n_blocks, label: `${s.n_blocks} blocks`, value: s.power, color: '#2f6f8f' }))} yMax={1} refY={0.8} yLabel="Power" label="Survival power" />
      </Card>
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 7 Agronomy
const byTrt = (rows) => TREATMENTS.map((t) => ({ key: t, label: TRT_LABEL[t], color: TRT_COLOR[t], points: rows.filter((r) => r.treatment === t).map((r) => ({ x: new Date(r.date).getTime(), y: r.value })) }));
const monthTick = (v) => new Date(v).toLocaleDateString(undefined, { month: 'short', year: '2-digit' });
function Agronomy({ d }) {
  const ir = d.irrigation;
  const boxes = TREATMENTS.map((t) => { const g = ir.plots.filter((p) => p.treatment === t); const s = g.map((p) => p.irrigation_mm).sort((a, b) => a - b); const q = (f) => s[Math.min(s.length - 1, Math.floor(f * (s.length - 1)))];
    return { key: t, label: TRT_LABEL[t], color: TRT_COLOR[t], stats: s.length ? { q1: q(0.25), median: q(0.5), q3: q(0.75), whisker_lo: s[0], whisker_hi: s[s.length - 1] } : null, points: g.map((p) => ({ value: p.irrigation_mm, tip: `${p.plot_id}: ${fmt(p.irrigation_mm, 0)} mm` })) }; });
  const months = d.weather_monthly;
  const wx = [{ key: 'et0', label: 'ET₀ (mm)', color: '#c27c2c', points: months.map((m, i) => ({ x: i, y: m.et0_mm })) }, { key: 'rain', label: 'Rain (mm)', color: '#2f6f8f', points: months.map((m, i) => ({ x: i, y: m.rain_mm })) }];
  const ev = d.calibration.evaluation;
  return <>
    <div className="an-stats">
      <Stat label="Irrigation effect (RCBD ANOVA)" value={`F = ${fmt(ir.anova.F, 2)}`} note={`p ${fmtP(ir.anova.p)}`} tone={ir.anova.p < 0.05 ? 'good' : undefined} />
      <Stat label="Hold-out VWC RMSE" value={fmt(ev.val_vwc_rmse, 3)} note={`calibration ${fmt(ev.cal_vwc_rmse, 3)} m³/m³`} />
      <Stat label="Hold-out seasonal irrigation error" value={`${fmt(ev.val_irr_err_pct_median, 1)} %`} note={`median · worst |err| ${fmt(ev.val_irr_err_pct_abs_max, 1)} %`} />
      <Stat label="Field capacity by treatment" value={`p = ${fmtP(d.calibration.anova_theta_fc.p)}`} note="calibrated θfc, RCBD ANOVA" />
    </div>
    <div className="an-toolbar"><TrtLegend /></div>
    <div className="an-grid">
      <Card title="Seasonal irrigation depth" sub="mm per plot (gap days filled with the plot's own mean).">
        <BoxStrip boxes={boxes} yLabel="mm" label="Irrigation depth by treatment" />
        <Table dense cols={[{ h: 'Contrast', f: (c) => c.contrast }, { h: 'Δ mm', f: (c) => fmt(c.estimate, 1), right: true }, { h: '95 % CI', f: (c) => `[${fmt(c.lo, 1)}, ${fmt(c.hi, 1)}]`, right: true }, { h: 'p (Holm)', f: (c) => <Sig p={c.p_holm} />, right: true }]} rows={ir.contrasts} />
      </Card>
      <Card title="Soil moisture (VWC), weekly" sub="Sensor mean per treatment, QC-filtered."><LineChart series={byTrt(d.vwc_weekly)} xFmt={monthTick} yLabel="VWC (m³/m³)" markers={false} label="VWC" /></Card>
      <Card title="Cumulative irrigation" sub="kL per plot, treatment mean."><LineChart series={byTrt(d.cum_irrigation_weekly)} xFmt={monthTick} yLabel="kL" markers={false} label="Cumulative irrigation" /></Card>
      <Card title="Plot NDVI, weekly" sub="Plot-level NDVI series (synthetic); real Sentinel-2 is block-level background only."><LineChart series={byTrt(d.ndvi_weekly)} xFmt={monthTick} yLabel="NDVI" markers={false} label="NDVI" /></Card>
      <Card title="Weather: ET₀ and rain by month" sub="Station series (QC-filtered)."><LineChart series={wx} xTicks={months.map((_, i) => i)} xFmt={(i) => months[i]?.month?.slice(2) || ''} yLabel="mm / month" label="Monthly ET0 and rain" /><Legend items={wx.map((s) => ({ key: s.key, color: s.color, label: s.label }))} /></Card>
      <Card title="Irrigation vs tree height" sub="Plot-level association at endpoint (exploratory; treatment confounds the relationship).">
        <Scatter points={d.irrigation_vs_growth.map((p) => ({ x: p.irrigation_mm, y: p.height, color: TRT_COLOR[p.treatment], tip: `${p.plot_id}: ${fmt(p.irrigation_mm, 0)} mm, ${fmt(p.height, 1)} cm` }))} xLabel="Irrigation (mm)" yLabel="Height (cm)" label="Irrigation vs height" />
      </Card>
      <Card title="FAO-56 calibration by treatment" sub="Per-plot calibrated parameters (mean ± SD across the 6 plots).">
        <Table dense cols={[{ h: 'Treatment', f: (r) => r.treatment }, { h: 'θfc', f: (r) => `${fmt(r.theta_fc_mean, 3)} ± ${fmt(r.theta_fc_std, 3)}`, right: true }, { h: 'Kc min', f: (r) => fmt(r.kc_min_mean, 2), right: true }, { h: 'Kc max', f: (r) => fmt(r.kc_max_mean, 2), right: true }]} rows={d.calibration.by_treatment} />
      </Card>
      <Card title="AquaCrop scenarios (model output)" sub="Strategy comparison from the scenario model, not measurements.">
        <Table dense cols={[{ h: 'Treatment', f: (r) => r.treatment }, { h: 'Strategy', f: (r) => r.strategy }, { h: 'Irrigation mm', f: (r) => fmt(r.irrigation_mm, 0), right: true }, { h: 'Yield t/ha', f: (r) => fmt(r.dry_yield_t_ha, 1), right: true }, { h: 'Deep perc. mm', f: (r) => fmt(r.deep_perc_mm, 1), right: true }, { h: 'WP kg/m³', f: (r) => fmt(r.wp_kg_m3, 2), right: true }]} rows={d.aquacrop} />
      </Card>
      {d.reanalysis && <Card title="Real reanalysis (ERA5-Land) vs station" sub="Real external reference; used for QC and forcing." wide>
        <Table dense cols={[{ h: 'Variable', f: (r) => r.variable || r.var || Object.values(r)[0] }, ...Object.keys(d.reanalysis.variables[0] || {}).filter((k) => !['variable', 'var'].includes(k)).slice(0, 6).map((k) => ({ h: k, f: (r) => (typeof r[k] === 'number' ? fmt(r[k], 3) : String(r[k] ?? '—')), right: true }))]} rows={d.reanalysis.variables} />
      </Card>}
      {d.ndvi_block_real?.length > 0 && <Card title="Sentinel-2 block NDVI (real)" sub="Block-level background context, not plot-level inference." wide>
        <LineChart series={[...new Set(d.ndvi_block_real.map((r) => r.block))].map((b, i) => ({ key: `b${b}`, label: `Block ${b}`, color: ['#315e49', '#2f6f8f', '#c27c2c', '#b44f48', '#6b7280', '#7c5c9c'][i % 6], points: d.ndvi_block_real.filter((r) => r.block === b).map((r) => ({ x: new Date(r.date).getTime(), y: r.ndvi_mean })) }))} xFmt={monthTick} yLabel="NDVI" label="Block NDVI" />
      </Card>}
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 8 Survival
function Survival({ d }) {
  const km = TREATMENTS.map((t) => ({ key: t, label: TRT_LABEL[t], color: TRT_COLOR[t], points: d.kaplan_meier.filter((k) => k.treatment === t).map((k) => ({ x: k.day, y: k.survival, lo: k.lo, hi: k.hi })) }));
  const end = d.counts.filter((c) => c.campaign === 'T4');
  const ph = d.proportional_hazards;
  const hr = d.cox.map((c) => ({ label: c.contrast, est: c.HR, lo: c.lo, hi: c.hi, color: TRT_COLOR[c.contrast.split(' ')[0]], note: `p(Holm)=${fmtP(c.p_holm)}` }));
  return <>
    <div className="an-stats">
      <Stat label="Log-rank (unstratified)" value={`χ² = ${fmt(d.logrank_unstratified.chi2, 2)}`} note={`df ${d.logrank_unstratified.df} · p ${fmtP(d.logrank_unstratified.p)}`} />
      {d.events.map((e) => <Stat key={e.treatment} label={`${e.treatment} deaths`} value={`${e.deaths}/${e.trees}`} note="trees" />)}
    </div>
    <div className="an-toolbar"><TrtLegend /></div>
    <div className="an-grid">
      <Card title="Kaplan–Meier survival" sub="Tree-level, with 95 % bands. Event time is the campaign when a tree is first recorded dead.">
        <LineChart series={km} step xTicks={campTicks} xFmt={dayTick} xLabel="Days" yLabel="Survival" yDomain={[0, 1.02]} markers={false} label="Kaplan–Meier curves" />
      </Card>
      <Card title="Proportion alive at endpoint" sub="Wilson 95 % interval (ignores plot clustering: use Cox / GLM for inference).">
        <Bars bars={end.map((c) => ({ key: c.treatment, label: TRT_LABEL[c.treatment], value: c.proportion, color: TRT_COLOR[c.treatment], err: [c.lo, c.hi] }))} yMax={1.05} yLabel="Proportion alive" label="Endpoint survival" />
      </Card>
      <Card title="Hazard ratios vs control (Cox, block-stratified)" sub="Plot-clustered robust SE. HR < 1 favours the amendment.">
        <Forest rows={hr} refLine={1} xLog xLabel="Hazard ratio (log scale)" label="Hazard ratios" />
        <Table dense cols={[{ h: 'Contrast', f: (c) => c.contrast }, { h: 'HR', f: (c) => fmt(c.HR, 2), right: true }, { h: '95 % CI', f: (c) => `[${fmt(c.lo, 2)}, ${fmt(c.hi, 2)}]`, right: true }, { h: 'p (Holm)', f: (c) => <Sig p={c.p_holm} />, right: true }]} rows={d.cox} />
      </Card>
      <Card title="Cross-check: plot-level quasi-binomial GLM" sub="Endpoint survival ~ block + treatment (Pearson-scaled).">
        <Table dense cols={[{ h: 'Contrast', f: (c) => c.contrast }, { h: 'Odds ratio', f: (c) => fmt(c.odds_ratio, 2), right: true }, { h: '95 % CI', f: (c) => `[${fmt(c.lo, 2)}, ${fmt(c.hi, 2)}]`, right: true }, { h: 'p (Holm)', f: (c) => <Sig p={c.p_holm} />, right: true }]} rows={d.glm_endpoint} />
        <h4 className="an-h4">Proportional-hazards check</h4>
        {ph.table?.length ? <Table dense cols={[{ h: 'Covariate', f: (r) => r.index || r.covariate || '—' }, { h: 'Test stat', f: (r) => fmt(r.test_statistic, 2), right: true }, { h: 'p', f: (r) => fmtP(r.p), right: true }]} rows={ph.table} /> : null}
        <p className="an-hint">{ph.note}</p>
      </Card>
      <Card title="Interim hazard ratios (T2)" sub="Exploratory, fewer events.">
        <Table dense cols={[{ h: 'Contrast', f: (c) => c.contrast }, { h: 'HR', f: (c) => fmt(c.HR, 2), right: true }, { h: '95 % CI', f: (c) => `[${fmt(c.lo, 2)}, ${fmt(c.hi, 2)}]`, right: true }, { h: 'p (Holm)', f: (c) => <Sig p={c.p_holm} />, right: true }]} rows={d.cox_mid} />
      </Card>
    </div>
    <Notes items={d.notes} />
  </>;
}

// ---------------------------------------------------------------- 9 Evaluation
function Evaluation({ d }) {
  const cov = (rows, name) => <Card title={`${name}: closed-loop coverage`} sub={`${d.n_replicates} re-simulated trials, endpoint contrasts, known truth.`}>
    <Bars bars={rows.map((r) => ({ key: r.treatment, label: r.treatment, value: r.coverage95, color: TRT_COLOR[r.treatment] }))} yMax={1.05} refY={0.95} yLabel="95 % CI coverage" label={`${name} coverage`} />
    <Table dense cols={[{ h: 'Treatment', f: (r) => r.treatment }, { h: 'Truth', f: (r) => fmt(r.true, 4), right: true }, { h: 'Mean est.', f: (r) => fmt(r.mean_est, 4), right: true }, { h: 'Bias', f: (r) => fmt(r.bias, 4), right: true }, { h: 'RMSE', f: (r) => fmt(r.rmse, 4), right: true }, { h: 'Coverage', f: (r) => fmt(r.coverage95, 2), right: true }, { h: 'Power', f: (r) => fmt(r.power_holm, 2), right: true }]} rows={rows} />
  </Card>;
  return <>
    <div className="an-grid">
      {cov(d.coverage_soc, 'SOC')}{cov(d.coverage_ec, 'ECe')}
      {d.qc_recall && <Card title="QC rule recall on injected faults" sub="Faults were injected deliberately; recall = share detected." wide>
        <Table dense cols={[{ h: 'Dataset', f: (r) => r.dataset }, { h: 'Fault', f: (r) => r.fault }, { h: 'Injected', f: (r) => r.n, right: true }, { h: 'Detected', f: (r) => r.detected, right: true }, { h: 'Recall', f: (r) => <Badge tone={r.recall >= 0.99 ? 'good' : r.recall >= 0.9 ? 'neutral' : 'warn'}>{fmt(r.recall, 3)}</Badge>, right: true }]} rows={d.qc_recall} />
      </Card>}
      {d.qc_summary?.pass_rate && <Card title="QC pass rate by dataset"><Bars bars={Object.entries(d.qc_summary.pass_rate).map(([k, v]) => ({ key: k, label: k, value: v, color: '#315e49' }))} yMax={1.05} yLabel="pass rate" label="QC pass rates" /></Card>}
      {d.reconciliation_summary && <Card title="Cross-layer reconciliation"><div className="an-kv">{Object.entries(d.reconciliation_summary).map(([k, v]) => <React.Fragment key={k}><span>{k}</span><b>{String(v)}</b></React.Fragment>)}</div></Card>}
    </div>
    <Notes items={d.notes} />
  </>;
}

const PANELS = { eda: EDA, design: Design, comparison: Comparison, models: Models, inference: Inference, power: Power, agronomy: Agronomy, survival: Survival, evaluation: Evaluation };

// ---------------------------------------------------------------- page
export default function AnalysisPage({ tab, setTab }) {
  const [runs, setRuns] = useState([]);
  const [runId, setRunId] = useState('');
  useEffect(() => { fetch(api('/api/analysis/runs')).then((r) => (r.ok ? r.json() : [])).then(setRuns).catch(() => setRuns([])); }, []);
  const state = useSection(tab, runId || null);
  const Panel = PANELS[tab] || EDA;
  const meta = state.meta;
  const respCtx = useSection('eda', runId || null); // response labels for inference table
  return <>
    <div className="page-heading"><div><div className="eyebrow">SCIENTIFIC & AGRONOMIC ANALYSIS <span className="eyebrow-line" /></div><h1>Trial results & statistical evidence</h1><p>Persisted, versioned results of the RCBD analysis. Nothing on this page is computed in the browser or at request time.</p></div>
      <div className="last-updated"><span>ANALYSIS RUN</span>{runs.length > 0 ? <select value={runId} onChange={(e) => setRunId(e.target.value)} aria-label="Analysis run"><option value="">Latest · {runs[0].run_id}</option>{runs.map((r) => <option key={r.run_id} value={r.run_id}>{r.run_id}</option>)}</select> : <b>—</b>}</div></div>
    {meta && <div className="an-prov" role="note">
      <Badge tone="warn">{meta.data_origin === 'synthetic' ? 'Synthetic data' : meta.data_origin}</Badge>{meta.quick && <Badge tone="warn">quick run (reduced simulations)</Badge>}
      <span>run <code>{meta.run_id}</code></span><span>dataset <code>{meta.dataset_version_id || '—'}</code></span><span>QC run <code>{meta.qc_run_id ?? '—'}</code></span><span>seed <code>{meta.seed}</code></span><span>computed {dt(meta.created_at)}</span><span>payload <code>{state.sha256?.slice(0, 10)}</code></span></div>}
    <div className="an-tabs" role="tablist">{TABS.map((t) => <button key={t.id} role="tab" aria-selected={tab === t.id} className={tab === t.id ? 'active' : ''} onClick={() => setTab(t.id)}>{t.label}</button>)}</div>
    <Gate state={state}>{state.status === 'ok' && <Panel d={state.data} resp={respCtx.status === 'ok' ? respCtx.data.responses : undefined} />}</Gate>
  </>;
}
