import './style.css';
import * as d3 from 'd3';
import { timeChart, scatter, parse, fInt, f1, fDay, color, css } from './charts.js';

const $ = (sel, el = document) => el.querySelector(sel);
const view = $('#view');
const api = async (path, opts) => {
  const r = await fetch(`/api${path}`, { headers: { 'content-type': 'application/json' }, ...opts });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  return r.status === 204 ? null : r.json();
};
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else if (v != null) el.setAttribute(k, v);
  }
  for (const k of kids.flat()) if (k != null) el.append(k.nodeType ? k : String(k));
  return el;
};
const pace = (s) => (s ? `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}` : '');
const hm = (sec) => (sec ? `${Math.floor(sec / 3600)}:${String(Math.floor((sec % 3600) / 60)).padStart(2, '0')}` : '');
const card = (title, note, body) => h('div', { class: 'card' }, h('h3', {}, title), note ? h('p', { class: 'note' }, note) : null, body);
const tile = (label, value, unit, sub, cls) =>
  h('div', { class: 'card tile' }, h('div', { class: 'label' }, label),
    h('div', { class: `value ${cls || ''}` }, value ?? '—', unit ? h('small', {}, ` ${unit}`) : null), sub ? h('div', { class: 'sub' }, sub) : null);
const chartBox = (height) => h('div', { class: 'chart', style: `min-height:${height}px` });
const phaseColor = (ph) => ({ Base: color(5), Build: color(0), Peak: color(3), Taper: color(2), 'Race week': color(1) }[String(ph).replace(' (easier week)', '').split(' ·')[0]] || css('--grey'));
const typeColor = (t) => ({ Run: color(0), Strength: color(1), Walk: color(2) }[t] || color(4));

const state = { days: Number(localStorage.getItem('hc.days')) || 84, cache: {} };
const load = async (key, path) => (state.cache[key] ??= api(path));
const resetCache = () => { state.cache = {}; };

// ---- common pieces ----------------------------------------------------------------------------

function sessionsGrid(list, todayIso) {
  return h('div', { class: 'sessions' }, list.map((s) =>
    h('div', { class: `session ${s.status}${s.day === todayIso ? ' today' : ''}`,
      style: s.status === 'Planned' ? `border-top-color:${/Long/.test(s.session) ? color(0) : /race|Race/.test(s.session) ? color(1) : /session|Threshold|Sharpener|Intervals/.test(s.session) ? color(3) : css('--grey')}` : null },
    h('div', { class: 'day' }, h('span', {}, fDay(parse(s.day))), h('span', {}, s.status === 'Planned' ? '' : s.status)),
    h('div', { class: 'name' }, s.session),
    s.km ? h('div', { class: 'km' }, `${s.km} km`) : null,
    s.pace ? h('div', { class: 'pace' }, s.pace) : null,
    s.detail ? h('div', { class: 'detail' }, s.detail) : null)));
}

function periodSeg(onChange) {
  const opts = [[28, '4 weeks'], [84, '12 weeks'], [182, '6 months'], [365, '1 year'], [3650, 'All']];
  const seg = h('div', { class: 'seg' });
  for (const [d, label] of opts) {
    seg.append(h('button', { class: d === state.days ? 'on' : '', onclick: () => { state.days = d; localStorage.setItem('hc.days', d); resetCache(); onChange(); } }, label));
  }
  return seg;
}

// ---- pages ------------------------------------------------------------------------------------

async function today() {
  const [p, cfg] = await Promise.all([load('plan', '/plan'), load('config', '/config')]);
  const t = cfg.today;
  const r = p.readiness, b = p.baseline, tw = p.this_week, race = p.target_race;
  const upcoming = [...tw.sessions, ...p.next_week].filter((s) => s.day >= t && s.status === 'Planned' && s.session !== 'Strength');
  const todays = [...tw.sessions.filter((s) => s.day === t && s.status !== 'Rest'), ...upcoming.filter((s) => s.day > t).slice(0, 1)];
  const pct = Math.min(100, Math.round((tw.done_km / (tw.target_km || 1)) * 100));
  view.replaceChildren(
    h('h1', {}, `Good ${new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 19 ? 'afternoon' : 'evening'}, ${p.athlete_name}`),
    h('p', { class: 'lede' }, race ? `${race.name} is in ${Math.round((parse(race.day) - parse(t)) / 864e5)} days.` : 'No race in your calendar — add one under Races to get a plan built around it.'),
    h('div', { class: 'grid tiles' },
      tile('Readiness', r.status, null, `Volume at ${Math.round(r.factor * 100)}% of plan`, `status-${r.status.split(' ')[0]}`),
      tile('Load ratio', b.load_ratio?.toFixed(2), null, '0.8–1.3 is the sweet spot'),
      tile('Last night’s HRV', b.hrv, 'ms', b.hrv_lo ? `Normal ${b.hrv_lo}–${b.hrv_hi} ms` : null),
      tile('Resting HR (7 days)', b.rhr7, 'bpm', b.rhr28 ? `vs ${b.rhr28} bpm over 28 days` : null)),
    h('p', { class: 'note' }, `Why: ${r.reasons.join('; ')}.`),
    h('h2', {}, 'Today and next up'),
    todays.length ? sessionsGrid(todays, t) : h('p', { class: 'note' }, 'Nothing planned.'),
    h('h2', {}, 'This week'),
    h('div', { class: 'card' },
      h('div', { class: 'row', style: 'justify-content:space-between;margin:0' }, h('b', {}, `${tw.done_km} of ~${tw.target_km} km`), h('span', { class: 'note', style: 'margin:0' }, `${pct}%`)),
      h('div', { class: 'progress' }, h('div', { style: `width:${pct}%` }))),
  );
}

async function plan() {
  const [p, cfg] = await Promise.all([load('plan', '/plan'), load('config', '/config')]);
  const race = p.target_race;
  const week = state.week || 'this';
  const first = p.race_plan[0];
  const head = race && first
    ? `${p.athlete_name}, you’re ${first.weeks_to_go} week${first.weeks_to_go === 1 ? '' : 's'} from ${race.name}, in the ${first.phase.toLowerCase()} phase.`
    : `${p.athlete_name}, no race yet — this keeps your fitness ticking over with one quality session a week.`;
  const list = week === 'this' ? p.this_week.sessions : p.next_week;
  const roadBox = chartBox(260), tableBox = h('div', { class: 'tablebox' });
  view.replaceChildren(
    h('h1', {}, 'Training plan'),
    h('p', { class: 'lede' }, head, ' ', `Readiness: ${p.readiness.status.toLowerCase()} (${p.readiness.reasons.join('; ')}).`),
    h('div', { class: 'row' }, (() => {
      const seg = h('div', { class: 'seg' });
      for (const [k, label] of [['this', 'This week'], ['next', 'Next week']]) seg.append(h('button', { class: k === week ? 'on' : '', onclick: () => { state.week = k; plan(); } }, label));
      return seg;
    })(), week === 'this' ? h('span', { class: 'note', style: 'margin:0' }, `${p.this_week.done_km} km done of ~${p.this_week.target_km} km`) : null),
    sessionsGrid(list, cfg.today),
    race ? h('h2', {}, 'Road to race day') : null,
    race ? card('Weekly distance', 'Grey: what you actually ran. Colour: the plan by phase. Line: long run.', [
      h('div', { class: 'legend' }, ['Base', 'Build', 'Peak', 'Taper', 'Race week'].map((x) => h('span', {}, h('span', { class: 'sw', style: `background:${phaseColor(x)}` }), x))),
      roadBox]) : null,
    race ? h('div', { style: 'height:16px' }) : null,
    race ? card('Week by week', 'Every fourth week is easier; B and C races reshape their week.', tableBox) : null,
    h('p', { class: 'note' }, 'Rule-based, not a coach. Skip or shorten anything if you feel run-down or sore.'),
  );
  if (race) {
    timeChart(roadBox, p.road, { x: 'week', step: 'week', h: 260, yfmt: f1,
      bars: [{ key: 'total_km', name: 'Total km', color: (r) => (r.kind === 'Actual' ? css('--grey') : phaseColor(r.phase)), opacity: (r) => (/easier/.test(r.phase) ? 0.55 : 1), fmt: f1 }],
      lines: [{ key: 'long_km', name: 'Long run km', color: css('--fg'), dots: true, r: 2.5, fmt: f1 }],
      extra: (r) => [{ name: 'Phase', value: r.phase }] });
    tableBox.append(h('table', {}, h('thead', {}, h('tr', {}, ['Week of', 'To go', 'Phase', 'Total km', 'Long run', 'Key session'].map((x, i) => h('th', { class: i && i !== 2 && i !== 5 ? 'num' : i === 5 ? 'hide-sm' : '' }, x)))),
      h('tbody', {}, p.race_plan.map((w, i) => h('tr', { style: i === 0 ? 'font-weight:600' : null },
        h('td', {}, d3.utcFormat('%b %-d')(parse(w.week))), h('td', { class: 'num' }, w.weeks_to_go),
        h('td', {}, h('span', { class: 'sw', style: `background:${phaseColor(w.phase)};opacity:${w.easier ? 0.55 : 1}` }), w.phase + (w.easier ? ' (easier)' : '')),
        h('td', { class: 'num' }, w.total_km), h('td', { class: 'num' }, w.long_km), h('td', { class: 'hide-sm' }, w.key))))));
  }
}

async function races() {
  const list = await api('/races');
  const form = h('form', { class: 'race', onsubmit: async (e) => {
    e.preventDefault();
    const fd = Object.fromEntries(new FormData(e.target));
    const body = { name: fd.name, date: fd.date, distance_km: Number(fd.distance_km), goal_time: fd.goal_time || null, priority: fd.priority, notes: fd.notes || null };
    try {
      await api(fd.id ? `/races/${fd.id}` : '/races', { method: fd.id ? 'PUT' : 'POST', body: JSON.stringify(body) });
      resetCache(); races();
    } catch (err) { $('.error', form).textContent = err.message; }
  } },
    h('input', { type: 'hidden', name: 'id' }),
    h('label', {}, 'Race', h('input', { name: 'name', required: '', placeholder: 'Lisbon Half Marathon' })),
    h('label', {}, 'Date', h('input', { name: 'date', type: 'date', required: '' })),
    h('label', {}, 'Distance', h('select', { name: 'distance_km', required: '' },
      [[5, '5 km'], [10, '10 km'], [15, '15 km'], [21.0975, 'Half marathon'], [42.195, 'Marathon']].map(([v, l]) => h('option', { value: v }, l)))),
    h('label', {}, 'Goal time', h('input', { name: 'goal_time', placeholder: '49:00 or 1:45:00', pattern: '\\d{1,2}(:\\d{2}){1,2}' })),
    h('label', {}, 'Priority', h('select', { name: 'priority' }, h('option', { value: 'A' }, 'A · main race'), h('option', { value: 'B' }, 'B · tune-up'), h('option', { value: 'C' }, 'C · training race'))),
    h('button', { class: 'primary', type: 'submit' }, 'Save race'),
    h('div', { class: 'error' }));
  const edit = (r) => {
    form.id.value = r.id; form.name.value = r.name; form.date.value = r.date; form.goal_time.value = r.goal_time || ''; form.priority.value = r.priority;
    const opt = [...form.distance_km.options].find((o) => Math.abs(o.value - r.distance_km) < 0.2);
    if (opt) form.distance_km.value = opt.value; else form.distance_km.append(h('option', { value: r.distance_km, selected: '' }, `${r.distance_km} km`));
    form.scrollIntoView({ behavior: 'smooth' });
  };
  view.replaceChildren(
    h('h1', {}, 'Races'),
    h('p', { class: 'lede' }, 'Your training plan is built around these. A = main race (full build and taper), B = tune-up (mini-taper, easy days after), C = training race (replaces that week’s hard session).'),
    card('Add a race', null, form),
    h('h2', {}, 'Upcoming'),
    list.length ? h('div', { class: 'card tablebox' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Date', 'Race', 'Distance', 'Goal', 'Pace', 'Priority', 'Days to go', ''].map((x, i) => h('th', { class: [2, 3, 4, 6].includes(i) ? 'num' : '' }, x)))),
      h('tbody', {}, list.map((r) => h('tr', {},
        h('td', {}, d3.utcFormat('%a %b %-d, %Y')(parse(r.date))), h('td', {}, r.name), h('td', { class: 'num' }, `${+r.distance_km.toFixed(1)} km`),
        h('td', { class: 'num' }, r.goal_time || '—'), h('td', { class: 'num' }, r.goal_pace ? `${r.goal_pace} /km` : '—'),
        h('td', {}, h('span', { class: `pill ${r.priority}` }, r.priority)), h('td', { class: 'num' }, r.days_to_go),
        h('td', {}, h('button', { class: 'link', onclick: () => edit(r) }, 'Edit'),
          h('button', { class: 'link', onclick: async () => { if (confirm(`Delete ${r.name}?`)) { await api(`/races/${r.id}`, { method: 'DELETE' }); resetCache(); races(); } } }, 'Delete'))))))) :
      h('div', { class: 'card empty' }, 'No upcoming races. Add one above.'),
  );
}

async function trends() {
  const q = `?days=${state.days}`;
  const [weekly, daily, acts, sum] = await Promise.all([load('weekly' + q, '/weekly' + q), load('daily' + q, '/daily' + q), load('acts' + q, '/activities' + q), load('sum' + q, '/summary' + q)]);
  const runs = acts.filter((a) => a.type === 'Run' && a.km && a.pace_s).map((a) => ({ ...a, eff: a.avg_hr ? +(a.km * 1000 / (a.duration_s / 60) / a.avg_hr).toFixed(2) : null }));
  const daysRows = daily.map((d, i, arr) => ({ ...d, date: d.day, steps_7d: Math.round(d3.mean(arr.slice(Math.max(0, i - 6), i + 1), (x) => x.steps || 0)) }));
  const boxes = { km: chartBox(220), types: chartBox(220), steps: chartBox(240), pace: chartBox(260), eff: chartBox(220), load: chartBox(220) };
  view.replaceChildren(
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h1', {}, 'Trends'), periodSeg(trends)),
    h('div', { class: 'grid tiles' },
      tile('Running', sum.run_km, 'km', `${sum.runs} runs`), tile('Training', sum.train_h, 'h', `${sum.sessions} sessions`),
      tile('Daily steps', sum.avg_steps ? fInt(Math.round(sum.avg_steps)) : null, null, `${sum.days_10k} days over 10k`),
      tile('VO2max', sum.vo2max, null, sum.threshold_pace ? `Threshold ${sum.threshold_pace} /km` : null)),
    h('h2', {}, 'Training'),
    h('div', { class: 'grid' },
      card('Weekly running distance', 'Kilometres per week', boxes.km),
      card('Weekly training time by type', 'Hours per week', [h('div', { class: 'legend' }, ['Run', 'Strength', 'Walk', 'Other'].map((t) => h('span', {}, h('span', { class: 'sw', style: `background:${typeColor(t)}` }), t))), boxes.types])),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Pace vs heart rate', 'Each dot is a run; bigger = longer, darker = more recent. Faster sits higher.', boxes.pace),
      card('Running efficiency', 'Metres per heartbeat — higher means more speed for the same effort.', boxes.eff)),
    h('div', { style: 'height:16px' }),
    card('Training load', 'Short-term (7-day) vs long-term (28-day) load from your watch.', boxes.load),
    h('h2', {}, 'Movement'),
    card('Daily steps', 'Bars coloured on days at 10,000+; the line is the 7-day average.', boxes.steps),
    h('h2', {}, 'Activities'),
    h('div', { class: 'card tablebox' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Date', 'Type', 'Name', 'Time', 'km', 'Pace', 'Avg HR'].map((x, i) => h('th', { class: i >= 3 ? 'num' : i === 2 ? 'hide-sm' : '' }, x)))),
      h('tbody', {}, acts.map((a) => h('tr', {}, h('td', {}, d3.utcFormat('%b %-d')(parse(a.date))),
        h('td', {}, h('span', { class: 'sw', style: `background:${typeColor(a.type)}` }), a.type), h('td', { class: 'hide-sm' }, a.name || a.sport),
        h('td', { class: 'num' }, hm(a.duration_s)), h('td', { class: 'num' }, a.km ?? ''), h('td', { class: 'num' }, a.type === 'Run' ? pace(a.pace_s) : ''), h('td', { class: 'num' }, a.avg_hr ?? ''))))))
  );
  timeChart(boxes.km, weekly, { x: 'week', step: 'week', h: 220, yfmt: f1, bars: [{ key: 'run_km', name: 'Run km', color: color(0), fmt: f1 }] });
  timeChart(boxes.types, weekly, { x: 'week', step: 'week', h: 220, stack: true, yfmt: f1, bars: [
    { key: 'run_h', name: 'Run', color: typeColor('Run'), fmt: f1 }, { key: 'strength_h', name: 'Strength', color: typeColor('Strength'), fmt: f1 },
    { key: 'walk_h', name: 'Walk', color: typeColor('Walk'), fmt: f1 }, { key: 'other_h', name: 'Other', color: typeColor('Other'), fmt: f1 }] });
  scatter(boxes.pace, runs.filter((r) => r.avg_hr), { x: 'avg_hr', y: 'pace_s', invertY: true, yfmt: pace, xlabel: 'Average heart rate (bpm)', size: (r) => 3 + Math.sqrt(r.km) * 1.6,
    tip: (r) => [{ name: 'Distance', value: `${r.km} km` }, { name: 'Pace', value: `${pace(r.pace_s)} /km` }, { name: 'Avg HR', value: `${r.avg_hr} bpm` }] });
  timeChart(boxes.eff, runs.filter((r) => r.eff), { h: 220, ymin: 0.8, yfmt: d3.format('.2f'), lines: [{ key: 'eff', name: 'Metres per beat', color: color(0), dots: true, fmt: d3.format('.2f') }],
    extra: (r) => [{ name: 'Distance', value: `${r.km} km` }, { name: 'Pace', value: `${pace(r.pace_s)} /km` }] });
  timeChart(boxes.load, daysRows.filter((d) => d.load_short != null), { h: 220, lines: [{ key: 'load_short', name: 'Short-term', color: color(0) }, { key: 'load_long', name: 'Long-term', color: color(1) }],
    extra: (r) => [{ name: 'Ratio', value: r.load_ratio?.toFixed(2) ?? '—' }] });
  timeChart(boxes.steps, daysRows, { h: 240, bars: [{ key: 'steps', name: 'Steps', color: (r) => (r.steps >= 10000 ? color(0) : css('--grey')) }],
    lines: [{ key: 'steps_7d', name: '7-day average', color: color(1) }], refs: [{ v: 10000, label: '10k' }] });
}

async function recovery() {
  const q = `?days=${state.days}`;
  const daily = await load('daily' + q, '/daily' + q);
  const rows = daily.map((d) => ({ ...d, date: d.day, bed_h: d.sleep_start ? (() => { const t = new Date(d.sleep_start); const x = t.getHours() + t.getMinutes() / 60; return x < 12 ? x + 24 : x; })() : null }));
  const hmin = (m) => (m == null ? '—' : `${Math.floor(m / 60)}h ${String(Math.round(m % 60)).padStart(2, '0')}m`);
  const clock = (v) => { const x = ((v % 24) + 24) % 24; return `${String(Math.floor(x)).padStart(2, '0')}:${String(Math.round((x % 1) * 60)).padStart(2, '0')}`; };
  const boxes = { sleep: chartBox(260), hrv: chartBox(220), hr: chartBox(220), stress: chartBox(200), score: chartBox(200), bed: chartBox(220) };
  view.replaceChildren(
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h1', {}, 'Recovery'), periodSeg(recovery)),
    card('Sleep each night', 'Time in each stage, by the day you woke up.', [h('div', { class: 'legend' }, [['Deep', color(4)], ['Light', color(5)], ['REM', color(6)], ['Awake', css('--grey')]].map(([n, c]) => h('span', {}, h('span', { class: 'sw', style: `background:${c}` }), n))), boxes.sleep]),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Overnight HRV', 'Shaded band is your normal range.', boxes.hrv),
      card('Resting heart rate', 'Beats per minute. A rise of several beats often follows hard days, poor sleep or illness.', boxes.hr)),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Sleep score', 'Nights under 70 highlighted.', boxes.score),
      card('When you fell asleep', 'A tighter band means a steadier routine.', boxes.bed)),
    h('div', { style: 'height:16px' }),
    card('Daily stress', 'Average stress score; 40+ highlighted.', boxes.stress),
  );
  timeChart(boxes.sleep, rows.filter((r) => r.sleep_min), { h: 260, stack: true, ystep: 120, yfmt: (m) => `${Math.round(m / 60)}h`, refs: [{ v: 480, label: '8 h' }], bars: [
    { key: 'deep_min', name: 'Deep', color: color(4), fmt: hmin }, { key: 'light_min', name: 'Light', color: color(5), fmt: hmin },
    { key: 'rem_min', name: 'REM', color: color(6), fmt: hmin }, { key: 'awake_min', name: 'Awake', color: css('--grey'), fmt: hmin }],
    extra: (r) => [{ name: 'Asleep', value: hmin(r.sleep_min) }, { name: 'Score', value: r.sleep_score ?? '—' }] });
  timeChart(boxes.hrv, rows.filter((r) => r.hrv_avg), { h: 220, ymin: 15, band: { lo: 'hrv_lo', hi: 'hrv_hi' }, lines: [{ key: 'hrv_avg', name: 'HRV (ms)', color: color(4), dots: true }] });
  timeChart(boxes.hr, rows.filter((r) => r.resting_hr), { h: 220, ymin: 40, lines: [{ key: 'resting_hr', name: 'Resting HR', color: color(0) }] });
  timeChart(boxes.score, rows.filter((r) => r.sleep_score), { h: 200, ymax: 100, bars: [{ key: 'sleep_score', name: 'Score', color: (r) => (r.sleep_score < 70 ? css('--bad') : color(4)) }] });
  timeChart(boxes.bed, rows.filter((r) => r.bed_h), { h: 220, ymin: 21, yfmt: clock, refs: [{ v: 23, label: '23:00' }], lines: [{ key: 'bed_h', name: 'Fell asleep', color: color(4), dots: true, noLine: true, fmt: clock }] });
  timeChart(boxes.stress, rows.filter((r) => r.stress_avg != null), { h: 200, bars: [{ key: 'stress_avg', name: 'Stress', color: (r) => (r.stress_avg >= 40 ? color(3) : css('--grey')) }] });
}

// ---- router -----------------------------------------------------------------------------------

const pages = { today, plan, races, trends, recovery };
async function route() {
  const name = location.hash.slice(1) || 'today';
  document.querySelectorAll('#tabs a').forEach((a) => a.classList.toggle('active', a.getAttribute('href') === `#${name}`));
  view.replaceChildren(h('div', { class: 'empty' }, 'Loading…'));
  try { await (pages[name] || today)(); } catch (e) { view.replaceChildren(h('div', { class: 'empty error' }, `Couldn’t load: ${e.message}`)); }
}
async function syncStatus() {
  try {
    const cfg = await api('/config');
    const st = Object.values(cfg.sync || {})[0];
    $('#sync').textContent = st?.last_sync ? `Synced ${d3.timeFormat('%H:%M')(new Date(st.last_sync))}` : 'Waiting for first sync';
  } catch { $('#sync').textContent = ''; }
}
window.addEventListener('hashchange', route);
let resizeT; window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(route, 250); });
setInterval(() => { resetCache(); syncStatus(); if ((location.hash.slice(1) || 'today') !== 'races') route(); }, 10 * 60 * 1000);
route(); syncStatus();
