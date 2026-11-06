import React from 'react';

// Small dependency-free SVG chart kit. Every chart scales via viewBox, carries <title> tooltips and an aria-label.
export const TRT_COLOR = { CTRL: '#6b7280', COMP: '#c27c2c', BIOC: '#2f6f8f', COMP_BIOC: '#2e7d5b' };
export const TRT_LABEL = { CTRL: 'Control', COMP: 'Compost', BIOC: 'Biochar', COMP_BIOC: 'Compost + biochar' };
export const TREATMENTS = ['CTRL', 'COMP', 'BIOC', 'COMP_BIOC'];
const AX = '#77827b';
const GRID = '#e8ebe4';
const M = { l: 56, r: 16, t: 14, b: 42 };

export const fmt = (v, d = 3) => (v == null || Number.isNaN(v) ? '—' : Math.abs(v) >= 1000 ? Number(v).toLocaleString('en-US', { maximumFractionDigits: 0 }) : Number(v).toFixed(d));
export const fmtP = (p) => (p == null ? '—' : p < 0.001 ? '<0.001' : p.toFixed(3));

export function niceTicks(lo, hi, n = 5) {
  if (!Number.isFinite(lo) || !Number.isFinite(hi)) return [0, 1];
  if (lo === hi) { lo -= 0.5; hi += 0.5; }
  const span = hi - lo, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw) || mag * 10;
  const start = Math.ceil(lo / step - 1e-9) * step, out = [];
  for (let v = start; v <= hi + step * 1e-9; v += step) out.push(Math.abs(v) < step * 1e-9 ? 0 : +v.toPrecision(12));
  return out;
}

function frame({ W, H, xDomain, yDomain, xLog = false, yLog = false }) {
  const iw = W - M.l - M.r, ih = H - M.t - M.b;
  const tx = (v) => (xLog ? Math.log(v) : v), ty = (v) => (yLog ? Math.log(v) : v);
  const [x0, x1] = xDomain.map(tx), [y0, y1] = yDomain.map(ty);
  return {
    iw, ih,
    X: (v) => M.l + ((tx(v) - x0) / (x1 - x0 || 1)) * iw,
    Y: (v) => M.t + ih - ((ty(v) - y0) / (y1 - y0 || 1)) * ih,
  };
}

function Axes({ W, H, F, xTicks, yTicks, xFmt = (v) => v, yFmt = (v) => v, xLabel, yLabel, xGrid = false }) {
  return <g fontSize="11" fill={AX}>
    {yTicks.map((t) => <g key={`y${t}`}><line x1={M.l} x2={W - M.r} y1={F.Y(t)} y2={F.Y(t)} stroke={GRID} /><text x={M.l - 8} y={F.Y(t) + 4} textAnchor="end">{yFmt(t)}</text></g>)}
    {xTicks.map((t) => <g key={`x${t}`}>{xGrid && <line x1={F.X(t)} x2={F.X(t)} y1={M.t} y2={H - M.b} stroke={GRID} />}<text x={F.X(t)} y={H - M.b + 17} textAnchor="middle">{xFmt(t)}</text></g>)}
    <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} stroke="#cfd5cc" />
    {xLabel && <text x={M.l + (W - M.l - M.r) / 2} y={H - 5} textAnchor="middle">{xLabel}</text>}
    {yLabel && <text transform={`rotate(-90)`} x={-(M.t + (H - M.t - M.b) / 2)} y={13} textAnchor="middle">{yLabel}</text>}
  </g>;
}

export function Legend({ items }) {
  return <div className="an-legend">{items.map((it) => <span key={it.key}><i style={{ background: it.color }} />{it.label}</span>)}</div>;
}
export const TrtLegend = () => <Legend items={TREATMENTS.map((t) => ({ key: t, color: TRT_COLOR[t], label: TRT_LABEL[t] }))} />;

const pad = (lo, hi, f = 0.08) => { const d = (hi - lo) || Math.abs(hi) || 1; return [lo - d * f, hi + d * f]; };

/** series: [{key,color,dash,points:[{x,y,lo?,hi?}], step?}] */
export function LineChart({ series, W = 640, H = 300, xLabel, yLabel, xFmt, yFmt, xTicks, yDomain, label, refY, markers = true, step = false }) {
  const pts = series.flatMap((s) => s.points);
  if (!pts.length) return <div className="an-empty-chart">No data</div>;
  const xs = pts.map((p) => p.x), ys = pts.flatMap((p) => [p.y, p.lo ?? p.y, p.hi ?? p.y]).concat(refY != null ? [refY] : []);
  const xd = [Math.min(...xs), Math.max(...xs)];
  const yd = yDomain || pad(Math.min(...ys), Math.max(...ys));
  const F = frame({ W, H, xDomain: xd, yDomain: yd });
  const yT = niceTicks(yd[0], yd[1]), xT = xTicks || niceTicks(xd[0], xd[1], 6);
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="an-svg">
    <Axes {...{ W, H, F, xTicks: xT, yTicks: yT, xFmt, yFmt, xLabel, yLabel }} />
    {refY != null && <line x1={M.l} x2={W - M.r} y1={F.Y(refY)} y2={F.Y(refY)} stroke="#b44f48" strokeDasharray="4 4" />}
    {series.map((s) => {
      const p = [...s.points].sort((a, b) => a.x - b.x);
      const d = p.map((q, i) => (step && i ? `H${F.X(q.x)}V${F.Y(q.y)}` : `${i ? 'L' : 'M'}${F.X(q.x)},${F.Y(q.y)}`)).join('');
      const band = p.every((q) => q.lo != null) && `${p.map((q, i) => `${i ? 'L' : 'M'}${F.X(q.x)},${F.Y(q.hi)}`).join('')}${[...p].reverse().map((q) => `L${F.X(q.x)},${F.Y(q.lo)}`).join('')}Z`;
      return <g key={s.key}>
        {band && <path d={band} fill={s.color} opacity="0.12" />}
        <path d={d} fill="none" stroke={s.color} strokeWidth="2" strokeDasharray={s.dash} strokeLinejoin="round" />
        {markers && p.map((q, i) => <circle key={i} cx={F.X(q.x)} cy={F.Y(q.y)} r="3.2" fill="#fff" stroke={s.color} strokeWidth="1.8"><title>{`${s.label || s.key}: x=${fmt(q.x, 2)}, y=${fmt(q.y)}${q.lo != null ? ` [${fmt(q.lo)}, ${fmt(q.hi)}]` : ''}`}</title></circle>)}
      </g>;
    })}
  </svg>;
}

/** boxes: [{key,color,label,stats:{q1,median,q3,whisker_lo,whisker_hi},points:[{value,tip}]}] */
export function BoxStrip({ boxes, W = 640, H = 300, yLabel, label }) {
  const vals = boxes.flatMap((b) => [...(b.points || []).map((p) => p.value), b.stats?.whisker_lo, b.stats?.whisker_hi].filter((v) => v != null));
  if (!vals.length) return <div className="an-empty-chart">No data</div>;
  const yd = pad(Math.min(...vals), Math.max(...vals));
  const F = frame({ W, H, xDomain: [0, boxes.length], yDomain: yd });
  const bw = Math.min(64, F.iw / boxes.length * 0.5);
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="an-svg">
    <Axes {...{ W, H, F, xTicks: [], yTicks: niceTicks(yd[0], yd[1]), yLabel }} />
    {boxes.map((b, i) => {
      const cx = F.X(i + 0.5), s = b.stats || {};
      return <g key={b.key}>
        {s.q1 != null && <><line x1={cx} x2={cx} y1={F.Y(s.whisker_lo)} y2={F.Y(s.whisker_hi)} stroke={b.color} />
          <rect x={cx - bw / 2} y={F.Y(s.q3)} width={bw} height={Math.max(1, F.Y(s.q1) - F.Y(s.q3))} fill={b.color} opacity="0.16" stroke={b.color} />
          <line x1={cx - bw / 2} x2={cx + bw / 2} y1={F.Y(s.median)} y2={F.Y(s.median)} stroke={b.color} strokeWidth="2.4" /></>}
        {(b.points || []).map((p, j) => <circle key={j} cx={cx + ((j % 7) - 3) * 4} cy={F.Y(p.value)} r="3.3" fill={b.color} opacity="0.85"><title>{p.tip}</title></circle>)}
        <text x={cx} y={H - M.b + 17} textAnchor="middle" fontSize="11" fill={AX}>{b.label}</text>
      </g>;
    })}
  </svg>;
}

/** rows: [{label,est,lo,hi,color?,note?}] — horizontal forest plot, ref line at `ref`. */
export function Forest({ rows, refLine = 0, W = 640, xLabel, xLog = false, label, rowH = 30 }) {
  if (!rows.length) return <div className="an-empty-chart">No data</div>;
  const left = Math.max(M.l + 60, 12 + Math.max(...rows.map((r) => String(r.label).length), 4) * 6.4);
  const H = M.t + rows.length * rowH + M.b;
  const all = rows.flatMap((r) => [r.lo, r.hi, r.est]).concat([refLine]).filter((v) => v != null && (!xLog || v > 0));
  const xd = pad(Math.min(...all), Math.max(...all), 0.1);
  if (xLog) { xd[0] = Math.max(xd[0], Math.min(...all) * 0.8); }
  const F = frame({ W: W, H: 100, xDomain: xd, yDomain: [0, 1], xLog });
  const Xs = (v) => left + ((xLog ? Math.log(v) : v) - (xLog ? Math.log(xd[0]) : xd[0])) / ((xLog ? Math.log(xd[1]) : xd[1]) - (xLog ? Math.log(xd[0]) : xd[0]) || 1) * (W - left - M.r);
  const ticks = xLog ? [0.1, 0.25, 0.5, 1, 2, 4, 10].filter((t) => t >= xd[0] && t <= xd[1]) : niceTicks(xd[0], xd[1], 6);
  void F;
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="an-svg">
    <g fontSize="11" fill={AX}>
      {ticks.map((t) => <g key={t}><line x1={Xs(t)} x2={Xs(t)} y1={M.t} y2={H - M.b} stroke={GRID} /><text x={Xs(t)} y={H - M.b + 17} textAnchor="middle">{t}</text></g>)}
      {xLabel && <text x={(W + left) / 2} y={H - 5} textAnchor="middle">{xLabel}</text>}
    </g>
    <line x1={Xs(refLine)} x2={Xs(refLine)} y1={M.t} y2={H - M.b} stroke="#b44f48" strokeDasharray="4 4" />
    {rows.map((r, i) => {
      const y = M.t + i * rowH + rowH / 2, c = r.color || '#315e49';
      return <g key={i}>
        <text x={4} y={y + 4} fontSize="11.5" fill="#202a25">{r.label}</text>
        <line x1={Xs(r.lo)} x2={Xs(r.hi)} y1={y} y2={y} stroke={c} strokeWidth="2.4" strokeLinecap="round" />
        <circle cx={Xs(r.est)} cy={y} r="4.6" fill={c}><title>{`${r.label}: ${fmt(r.est)} [${fmt(r.lo)}, ${fmt(r.hi)}]${r.note ? ` · ${r.note}` : ''}`}</title></circle>
      </g>;
    })}
  </svg>;
}

/** matrix heat-map. values[r][c]; domain symmetrical for diverging. */
export function Heatmap({ rows, cols: colsIn, values: valuesIn, diverging = false, W = 640, cell: cellIn, label, digits = 2, rowLabel = (r) => r }) {
  const pref = TREATMENTS.filter((t) => colsIn.includes(t));
  const order = pref.length === colsIn.length ? pref.map((t) => colsIn.indexOf(t)) : colsIn.map((_, i) => i);
  const cols = order.map((i) => colsIn[i]);
  const values = valuesIn.map((r) => order.map((i) => r[i]));
  const cell = cellIn || Math.max(44, Math.max(...cols.map((c) => String(c).length)) * 7 + 10);
  const flat = values.flat().filter((v) => v != null);
  if (!flat.length) return <div className="an-empty-chart">No data</div>;
  const lo = diverging ? -1 : Math.min(...flat), hi = diverging ? 1 : Math.max(...flat);
  const left = 72, top = 38, H = top + rows.length * cell + 8, w = Math.max(W, left + cols.length * cell + 8);
  const color = (v) => {
    if (v == null) return '#f1f3ee';
    const t = (v - lo) / (hi - lo || 1);
    if (diverging) return t >= 0.5 ? `rgba(46,125,91,${(t - 0.5) * 1.7 + 0.05})` : `rgba(180,79,72,${(0.5 - t) * 1.7 + 0.05})`;
    return `rgba(47,111,143,${0.1 + t * 0.75})`;
  };
  return <svg viewBox={`0 0 ${w} ${H}`} role="img" aria-label={label} className="an-svg" style={{ maxWidth: w }}>
    {cols.map((c, j) => <text key={c} x={left + j * cell + cell / 2} y={top - 12} textAnchor="middle" fontSize="11" fill={AX}>{c}</text>)}
    {rows.map((r, i) => <g key={i}>
      <text x={left - 8} y={top + i * cell + cell / 2 + 4} textAnchor="end" fontSize="11" fill={AX}>{rowLabel(r)}</text>
      {cols.map((c, j) => { const v = values[i][j]; const dark = v != null && ((v - lo) / (hi - lo || 1) > 0.7 || (diverging && Math.abs(v) > 0.7)); return <g key={j}>
        <rect x={left + j * cell + 1} y={top + i * cell + 1} width={cell - 2} height={cell - 2} rx="4" fill={color(v)}><title>{`${rowLabel(r)} × ${c}: ${v == null ? 'n/a' : v.toFixed(digits + 1)}`}</title></rect>
        <text x={left + j * cell + cell / 2} y={top + i * cell + cell / 2 + 4} textAnchor="middle" fontSize="10.5" fill={dark ? '#fff' : '#202a25'}>{v == null ? '' : v.toFixed(digits)}</text></g>; })}
    </g>)}
  </svg>;
}

/** points: [{x,y,color,tip}] with optional 45° reference line. */
export function Scatter({ points, W = 640, H = 300, xLabel, yLabel, label, diagonal = false, hline = null }) {
  if (!points.length) return <div className="an-empty-chart">No data</div>;
  const xd = pad(Math.min(...points.map((p) => p.x)), Math.max(...points.map((p) => p.x)));
  const yd = pad(Math.min(...points.map((p) => p.y)), Math.max(...points.map((p) => p.y)));
  const F = frame({ W, H, xDomain: xd, yDomain: yd });
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="an-svg">
    <Axes {...{ W, H, F, xTicks: niceTicks(xd[0], xd[1], 6), yTicks: niceTicks(yd[0], yd[1]), xLabel, yLabel, xGrid: true, xFmt: (v) => +v.toFixed(2), yFmt: (v) => +v.toFixed(2) }} />
    {diagonal && <line x1={F.X(Math.max(xd[0], yd[0]))} y1={F.Y(Math.max(xd[0], yd[0]))} x2={F.X(Math.min(xd[1], yd[1]))} y2={F.Y(Math.min(xd[1], yd[1]))} stroke="#b44f48" strokeDasharray="4 4" />}
    {hline != null && <line x1={M.l} x2={W - M.r} y1={F.Y(hline)} y2={F.Y(hline)} stroke="#b44f48" strokeDasharray="4 4" />}
    {points.map((p, i) => <circle key={i} cx={F.X(p.x)} cy={F.Y(p.y)} r="3.6" fill={p.color || '#315e49'} opacity="0.8"><title>{p.tip}</title></circle>)}
  </svg>;
}

/** counts + edges histogram */
export function Histogram({ counts, edges, W = 640, H = 240, xLabel, label }) {
  const F = frame({ W, H, xDomain: [edges[0], edges[edges.length - 1]], yDomain: [0, Math.max(...counts, 1) * 1.1] });
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="an-svg">
    <Axes {...{ W, H, F, xTicks: niceTicks(edges[0], edges[edges.length - 1], 6), yTicks: niceTicks(0, Math.max(...counts, 1), 4), xLabel, yLabel: 'plots', xFmt: (v) => +v.toFixed(3) }} />
    {counts.map((c, i) => <rect key={i} x={F.X(edges[i]) + 1} y={F.Y(c)} width={Math.max(1, F.X(edges[i + 1]) - F.X(edges[i]) - 2)} height={F.Y(0) - F.Y(c)} fill="#2f6f8f" opacity="0.75"><title>{`${fmt(edges[i], 3)}–${fmt(edges[i + 1], 3)}: ${c}`}</title></rect>)}
  </svg>;
}

/** grouped/simple vertical bars: bars [{key,label,value,color,err?:[lo,hi]}] */
export function Bars({ bars, W = 640, H = 280, yLabel, label, yMax, refY }) {
  if (!bars.length) return <div className="an-empty-chart">No data</div>;
  const top = yMax ?? Math.max(...bars.map((b) => b.err?.[1] ?? b.value), refY ?? 0) * 1.12;
  const F = frame({ W, H, xDomain: [0, bars.length], yDomain: [0, top || 1] });
  const bw = Math.min(70, F.iw / bars.length * 0.55);
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="an-svg">
    <Axes {...{ W, H, F, xTicks: [], yTicks: niceTicks(0, top, 5), yLabel }} />
    {refY != null && <line x1={M.l} x2={W - M.r} y1={F.Y(refY)} y2={F.Y(refY)} stroke="#b44f48" strokeDasharray="4 4" />}
    {bars.map((b, i) => { const cx = F.X(i + 0.5); return <g key={b.key}>
      <rect x={cx - bw / 2} y={F.Y(b.value)} width={bw} height={F.Y(0) - F.Y(b.value)} fill={b.color} opacity="0.85" rx="3"><title>{`${b.label}: ${fmt(b.value)}`}</title></rect>
      {b.err && <g stroke="#202a25"><line x1={cx} x2={cx} y1={F.Y(b.err[0])} y2={F.Y(b.err[1])} /><line x1={cx - 5} x2={cx + 5} y1={F.Y(b.err[0])} y2={F.Y(b.err[0])} /><line x1={cx - 5} x2={cx + 5} y1={F.Y(b.err[1])} y2={F.Y(b.err[1])} /></g>}
      <text x={cx} y={H - M.b + 17} textAnchor="middle" fontSize="11" fill={AX}>{b.label}</text></g>; })}
  </svg>;
}

/** stacked horizontal share bar */
export function ShareBar({ parts }) {
  const tot = parts.reduce((a, p) => a + p.value, 0) || 1;
  return <div className="an-share"><div className="an-share-bar">{parts.map((p) => <span key={p.key} style={{ width: `${(p.value / tot) * 100}%`, background: p.color }} title={`${p.label}: ${(100 * p.value / tot).toFixed(1)}%`} />)}</div>
    <div className="an-share-legend">{parts.map((p) => <span key={p.key}><i style={{ background: p.color }} />{p.label} {(100 * p.value / tot).toFixed(1)}%</span>)}</div></div>;
}

/** field layout: plots [{plot_id,block,treatment,x,y}] 12×20 m rectangles, block labels on the left */
export function LayoutMap({ plots, W = 640, H = 460, plotW = 12, plotL = 20 }) {
  if (!plots.length) return <div className="an-empty-chart">No data</div>;
  const xs = plots.map((p) => p.x), ys = plots.map((p) => p.y);
  const x0 = Math.min(...xs) - 2, x1 = Math.max(...xs) + plotW + 2, y0 = Math.min(...ys) - 2, y1 = Math.max(...ys) + plotL + 2;
  const left = 64;
  const s = Math.min((W - left - 10) / (x1 - x0), (H - 20) / (y1 - y0));
  const X = (v) => left + (v - x0) * s, Y = (v) => H - 10 - (v - y0) * s;
  const blocks = [...new Set(plots.map((p) => p.block))];
  return <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Field layout" className="an-svg" style={{ maxWidth: 520 }}>
    {blocks.map((b) => { const g = plots.filter((p) => p.block === b); const yc = Y(Math.min(...g.map((p) => p.y)) + plotL / 2); return <text key={b} x={left - 10} y={yc + 4} textAnchor="end" fontSize="11" fill={AX}>Block {b}</text>; })}
    {plots.map((p) => <g key={p.plot_id}>
      <rect x={X(p.x)} y={Y(p.y + plotL)} width={plotW * s} height={plotL * s} fill={TRT_COLOR[p.treatment]} opacity="0.85" rx="3"><title>{`${p.plot_id} · block ${p.block} · ${TRT_LABEL[p.treatment]}`}</title></rect>
      <text x={X(p.x) + plotW * s / 2} y={Y(p.y + plotL) + plotL * s / 2 + 4} textAnchor="middle" fontSize="11" fill="#fff" fontWeight="600">{String(p.plot_id).split('-').pop()}</text>
    </g>)}
  </svg>;
}
