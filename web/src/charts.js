import * as d3 from 'd3';

const DAY = 864e5;
export const parse = (s) => new Date(String(s).slice(0, 10) + 'T00:00:00Z');
export const fInt = d3.format(',');
export const f1 = d3.format('.1f');
export const fDay = d3.utcFormat('%a %b %-d');
const fWeek = d3.utcFormat('Week of %b %-d');
const fAxis = d3.utcFormat('%b %-d');
export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
export const color = (i) => css(`--c${(i % 7) + 1}`);

function frame(el, h) {
  el.replaceChildren();
  const w = Math.max(280, el.clientWidth || 600);
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${w} ${h}`);
  const tip = d3.select(el).append('div').attr('class', 'tip');
  return { svg, w, tip };
}

function textWidth(svg, strings) {
  const t = svg.append('text');
  let m = 0;
  for (const s of strings) { t.text(s); m = Math.max(m, t.node().getComputedTextLength()); }
  t.remove();
  return m;
}

function showTip(tip, el, event, title, items) {
  const n = tip.node();
  n.replaceChildren();
  const t = document.createElement('div'); t.className = 't'; t.textContent = title; n.append(t);
  for (const it of items) {
    const r = document.createElement('div'); r.className = 'r';
    const a = document.createElement('span');
    if (it.color) { const s = document.createElement('span'); s.className = 'sw'; s.style.background = it.color; a.append(s); }
    a.append(it.name);
    const b = document.createElement('b'); b.textContent = it.value;
    r.append(a, b); n.append(r);
  }
  tip.style('opacity', 1);
  const [px, py] = d3.pointer(event, el);
  let left = px + 14; if (left + n.offsetWidth > el.clientWidth) left = Math.max(0, px - n.offsetWidth - 14);
  let top = py - n.offsetHeight - 10; if (top < 0) top = py + 14;
  tip.style('left', `${left}px`).style('top', `${top}px`);
}

/**
 * Time chart. o = { x:'date', step:'day'|'week', h, bars:[{key,name,color,fmt}], stack, lines:[{key,name,color,dots,noLine}],
 *   refs:[{v,label}], band:{lo,hi}, yfmt, ymin, ymax, extra:(row)=>[{name,value}] }
 */
export function timeChart(el, rows, o) {
  const h = o.h || 240;
  const { svg, w, tip } = frame(el, h);
  const xk = o.x || 'date';
  const data = rows.filter((r) => r[xk]).map((r) => ({ ...r, _d: parse(r[xk]) })).sort((a, b) => a._d - b._d);
  if (!data.length) { el.innerHTML = '<div class="empty">No data for this period yet</div>'; return; }
  const step = (o.step === 'week' ? 7 : 1) * DAY;
  const bars = o.bars || [], lines = o.lines || [];
  const v = (r, k) => (r[k] == null ? null : +r[k]);
  const colorOf = (s, r) => (typeof s.color === 'function' ? s.color(r) : s.color);
  const top = d3.max([
    ...data.map((r) => (o.stack ? d3.sum(bars, (b) => v(r, b.key) || 0) : d3.max(bars, (b) => v(r, b.key) || 0) || 0)),
    ...data.flatMap((r) => lines.map((l) => v(r, l.key) || 0)),
    ...(o.refs || []).map((x) => x.v),
    ...(o.band ? data.map((r) => v(r, o.band.hi) || 0) : []),
  ]) || 1;
  const y = d3.scaleLinear([o.ymin ?? 0, o.ymax ?? top * 1.08], [h - 26, 10]).nice(5);
  const yf = o.yfmt || fInt;
  const left = textWidth(svg, y.ticks(5).map(yf)) + 10, right = 8;
  const x = d3.scaleUtc([new Date(+data[0]._d - step / 2), new Date(+data.at(-1)._d + step / 2)], [left, w - right]);
  const bw = Math.max(1, Math.min(32, x(new Date(+data[0]._d + step)) - x(data[0]._d) - (o.step === 'week' ? 6 : 1.5)));
  const yt = o.ystep ? d3.range(Math.ceil(y.domain()[0] / o.ystep) * o.ystep, y.domain()[1] + 1e-9, o.ystep) : y.ticks(5);
  const g = svg.append('g').attr('class', 'grid');
  for (const t of yt) {
    g.append('line').attr('x1', left).attr('x2', w - right).attr('y1', y(t)).attr('y2', y(t));
    svg.append('text').attr('x', left - 6).attr('y', y(t)).attr('dy', '.32em').attr('text-anchor', 'end').text(yf(t));
  }
  const maxTicks = Math.max(2, Math.floor(w / 95));
  const xt = o.step === 'week' ? data.map((r) => r._d).filter((_, i, arr) => i % Math.ceil(arr.length / maxTicks) === 0) : x.ticks(maxTicks);
  for (const t of xt) svg.append('text').attr('x', x(t)).attr('y', h - 8).attr('text-anchor', 'middle').text(fAxis(t));
  if (o.band) {
    svg.append('path').datum(data.filter((r) => v(r, o.band.lo) != null)).attr('fill', css('--grey')).attr('fill-opacity', 0.35)
      .attr('d', d3.area().x((r) => x(r._d)).y0((r) => y(v(r, o.band.lo))).y1((r) => y(v(r, o.band.hi))));
  }
  for (const r of data) {
    let acc = 0;
    for (const b of bars) {
      const val = v(r, b.key);
      if (!val) continue;
      const base = o.stack ? acc : Math.max(0, y.domain()[0]);
      const end = o.stack ? acc + val : val;
      acc += val;
      const [yTop, yBot] = end >= base ? [y(end), y(base)] : [y(base), y(end)];
      svg.append('rect').attr('x', x(r._d) - bw / 2).attr('width', bw).attr('y', yTop).attr('height', Math.max(0, yBot - yTop))
        .attr('rx', Math.min(2, bw / 2)).attr('fill', colorOf(b, r)).attr('fill-opacity', b.opacity ? b.opacity(r) : 1);
    }
  }
  for (const rf of o.refs || []) {
    svg.append('line').attr('class', 'ref').attr('x1', left).attr('x2', w - right).attr('y1', y(rf.v)).attr('y2', y(rf.v));
    if (rf.label) svg.append('text').attr('x', w - right - 4).attr('y', y(rf.v) - 5).attr('text-anchor', 'end').text(rf.label);
  }
  for (const l of lines) {
    const pts = data.filter((r) => v(r, l.key) != null);
    if (!l.noLine) svg.append('path').datum(pts).attr('class', 'line').attr('stroke', l.color).attr('d', d3.line().x((r) => x(r._d)).y((r) => y(v(r, l.key))));
    if (l.dots) svg.append('g').selectAll('circle').data(pts).join('circle').attr('cx', (r) => x(r._d)).attr('cy', (r) => y(v(r, l.key))).attr('r', l.r || 3).attr('fill', l.color);
  }
  const rule = svg.append('line').attr('y1', 10).attr('y2', h - 26).attr('stroke', css('--muted')).style('opacity', 0);
  const bis = d3.bisector((r) => r._d).center;
  svg.append('rect').attr('x', left).attr('width', w - left - right).attr('height', h).attr('fill', 'transparent')
    .on('pointermove', (e) => {
      const r = data[bis(data, x.invert(d3.pointer(e)[0]))];
      rule.attr('x1', x(r._d)).attr('x2', x(r._d)).style('opacity', 0.5);
      const items = [...bars, ...lines].map((s) => ({ color: colorOf(s, r), name: s.name, value: r[s.key] == null ? '—' : (s.fmt || yf)(+r[s.key]) }));
      showTip(tip, el, e, (o.step === 'week' ? fWeek : fDay)(r._d), [...items, ...(o.extra ? o.extra(r) : [])]);
    })
    .on('pointerleave', () => { rule.style('opacity', 0); tip.style('opacity', 0); });
}

export function scatter(el, rows, o) {
  const h = o.h || 260;
  const { svg, w, tip } = frame(el, h);
  if (!rows.length) { el.innerHTML = '<div class="empty">No runs in this period</div>'; return; }
  const y = d3.scaleLinear(d3.extent(rows, (r) => +r[o.y]), o.invertY ? [10, h - 40] : [h - 40, 10]).nice(5);
  const yf = o.yfmt || fInt;
  const left = textWidth(svg, y.ticks(5).map(yf)) + 10;
  const x = d3.scaleLinear(d3.extent(rows, (r) => +r[o.x]), [left + 8, w - 12]).nice(6);
  const g = svg.append('g').attr('class', 'grid');
  for (const t of y.ticks(5)) {
    g.append('line').attr('x1', left).attr('x2', w - 12).attr('y1', y(t)).attr('y2', y(t));
    svg.append('text').attr('x', left - 6).attr('y', y(t)).attr('dy', '.32em').attr('text-anchor', 'end').text(yf(t));
  }
  for (const t of x.ticks(Math.max(3, Math.floor(w / 80)))) svg.append('text').attr('x', x(t)).attr('y', h - 22).attr('text-anchor', 'middle').text(t);
  svg.append('text').attr('x', w - 12).attr('y', h - 4).attr('text-anchor', 'end').text(o.xlabel);
  const ds = rows.map((r) => parse(r.date));
  const t = d3.scaleUtc(d3.extent(ds), [0.15, 1]);
  const ramp = d3.interpolateRgb(css('--grey'), css('--c1'));
  svg.append('g').selectAll('circle').data(rows).join('circle').attr('cx', (r) => x(+r[o.x])).attr('cy', (r) => y(+r[o.y]))
    .attr('r', (r) => (o.size ? o.size(r) : 5)).attr('fill', (r) => ramp(t(parse(r.date)))).attr('fill-opacity', 0.85)
    .on('pointermove', (e, r) => showTip(tip, el, e, fDay(parse(r.date)), o.tip(r)))
    .on('pointerleave', () => tip.style('opacity', 0));
}
