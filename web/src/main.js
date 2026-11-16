import './style.css';
import * as d3 from 'd3';
import { timeChart, scatter, parse, fInt, f1, fDay, color, css } from './charts.js';
import { trainingPages } from './training.js';
import { nutritionPages } from './nutrition.js';
import { claudeHandoff } from './handoff.js';

const $ = (sel, el = document) => el.querySelector(sel);
const view = $('#view');
// Render a page; skips null/false parts (conditional sections) instead of printing them as text.
const show = (...parts) => view.replaceChildren(...parts.flat().filter((x) => x != null && x !== false));
const api = async (path, opts) => {
  const r = await fetch(`/api${path}`, { headers: { 'content-type': 'application/json' }, ...opts });
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail;
    throw new Error(Array.isArray(detail) ? detail.map((item) => item.msg).join('; ') : detail || r.statusText);
  }
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
const delta = (cur, prev, goodUp = true) => {
  if (cur == null || prev == null || prev === 0) return null;
  const d = cur - prev, pctv = Math.round((d / Math.abs(prev)) * 100);
  if (!pctv) return h('span', { class: 'delta' }, '±0% vs previous');
  const good = goodUp ? d > 0 : d < 0;
  return h('span', { class: `delta ${good ? 'up' : 'down'}` }, `${d > 0 ? '▲' : '▼'} ${Math.abs(pctv)}% vs previous`);
};
const chartBox = (height) => h('div', { class: 'chart', style: `min-height:${height}px` });
const phaseColor = (ph) => ({ Base: color(5), Build: color(0), Peak: color(3), Taper: color(2), 'Race week': color(1) }[String(ph).replace(' (easier week)', '').split(' ·')[0]] || css('--grey'));
const typeColor = (t) => ({ Run: color(0), Strength: color(1), Walk: color(2) }[t] || color(4));

const state = { days: Number(localStorage.getItem('hc.days')) || 84, cache: {} };
const load = async (key, path) => (state.cache[key] ??= api(path));
const resetCache = () => { state.cache = {}; };

// ---- common pieces ----------------------------------------------------------------------------

// Sessions without weekdays: what to do next (in order) or what was done.
function sessionsGrid(list, empty) {
  if (!list.length) return h('div', { class: 'card empty' }, empty);
  return h('div', { class: 'sessions' }, list.map((s) =>
    h('div', { class: `session ${s.status}`,
      style: s.status === 'Planned' ? `border-top-color:${/Long/.test(s.session) ? color(0) : /race|Race/.test(s.session) ? color(1) : /session|Threshold|Sharpener|Intervals/.test(s.session) ? color(3) : css('--grey')}` : null },
    s.adjusted ? h('div', { class: 'day' }, h('span', { class: 'pill adj', title: s.reason }, 'eased')) : null,
    h('div', { class: 'name' }, s.session),
    s.km ? h('div', { class: 'km' }, `${s.km} km`) : null,
    s.duration_min && (s.status !== 'Done' || !s.km) ? h('div', { class: 'km' }, `${s.duration_min} min`) : null,
    s.pace ? h('div', { class: 'pace' }, s.pace) : null,
    s.detail ? h('div', { class: 'detail' }, s.detail) : null,
    s.adjusted && s.reason ? h('div', { class: 'detail adj-reason' }, s.reason) : null)));
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

const zoneColor = (z) => ({ High: css('--good'), Moderate: css('--c1'), Low: css('--fair'), Poor: css('--poor') }[z] || css('--grey'));
const levelColor = (l) => ({ good: css('--good'), fair: css('--fair'), poor: css('--poor') }[l] || css('--grey'));
const formColor = (z) => ({ Transition: css('--fair'), Fresh: css('--good'), Neutral: css('--muted'), 'Optimal training': css('--c1'), 'High risk': css('--poor') }[z] || css('--grey'));

function ring(score, colr, size = 168) {
  const r = size / 2 - 12, c = 2 * Math.PI * r, frac = Math.max(0, Math.min(1, (score ?? 0) / 100));
  const ns = 'http://www.w3.org/2000/svg';
  const svg = document.createElementNS(ns, 'svg');
  svg.setAttribute('viewBox', `0 0 ${size} ${size}`); svg.setAttribute('width', size); svg.setAttribute('height', size); svg.setAttribute('class', 'ring');
  const mk = (tag, a) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(a)) e.setAttribute(k, v); svg.append(e); return e; };
  mk('circle', { cx: size / 2, cy: size / 2, r, fill: 'none', stroke: css('--line'), 'stroke-width': 12 });
  mk('circle', { cx: size / 2, cy: size / 2, r, fill: 'none', stroke: colr, 'stroke-width': 12, 'stroke-linecap': 'round',
    'stroke-dasharray': `${c * frac} ${c}`, transform: `rotate(-90 ${size / 2} ${size / 2})` });
  const t = mk('text', { x: '50%', y: '50%', 'text-anchor': 'middle', dy: '.1em', class: 'ring-num' }); t.textContent = score ?? '—';
  const u = mk('text', { x: '50%', y: '50%', 'text-anchor': 'middle', dy: '2.2em', class: 'ring-lab' }); u.textContent = 'readiness';
  return svg;
}

async function today() {
  const [p, cfg, fit, ol] = await Promise.all([load('plan', '/plan'), load('config', '/config'), load('fit84', '/fitness?days=84'), load('outlook', '/races/outlook').catch(() => ({ races: [] }))]);
  const t = cfg.today;
  const r = p.readiness, race = p.target_race, shape = p.shape;
  const next = p.next_7_days.todo.slice(0, 2), done7 = p.last_7_days.done_km, todo7 = p.next_7_days.todo_km;
  const form = fit.at(-1);
  const days = race ? Math.round((parse(race.day) - parse(t)) / 864e5) : null;
  const zc = zoneColor(r.zone);
  const ro = race ? ol.races.find((o) => o.race_id === race.id) : null;
  const advice = { High: 'You’re well recovered — a good day for the key session.', Moderate: 'Recovered enough to train as planned.',
    Low: 'Some strain showing — keep today easy or shorten the session.', Poor: 'Your body is asking for rest — easy movement only today.' }[r.zone] || '';
  show(
    h('h1', {}, `Good ${new Date().getHours() < 12 ? 'morning' : new Date().getHours() < 19 ? 'afternoon' : 'evening'}, ${p.athlete_name}`),
    h('p', { class: 'lede' }, advice),
    h('div', { class: 'hero' },
      h('div', { class: 'card hero-score' },
        ring(r.score, zc),
        h('div', {},
          h('div', { class: 'zone', style: `color:${zc}` }, r.zone || r.status),
          h('div', { class: 'note', style: 'margin:2px 0 12px' }, r.status === 'Unknown' ? 'No readiness adjustment: current signals are missing.' : `Plan volume at ${Math.round(r.factor * 100)}%`),
          h('div', { class: 'note' }, r.reasons.join('. ')),
          h('div', { class: 'contribs' }, r.contributors.map((c) => h('div', { class: 'contrib' },
            h('div', { class: 'contrib-top' }, h('span', {}, c.name), h('b', {}, c.value)),
            h('div', { class: 'bar' }, h('div', { style: `width:${c.score}%;background:${levelColor(c.level)}` })),
            h('div', { class: 'note', style: 'margin:2px 0 0' }, `Your normal: ${c.baseline}`)))))),
      h('div', { class: 'hero-side' },
        h('div', { class: 'card' }, h('div', { class: 'label' }, 'Next up'),
          next.length ? next.map((s) => h('div', { class: 'next' },
            h('div', { class: 'next-name' }, s.session, s.km ? h('span', { class: 'muted' }, ` · ${s.km} km`) : s.duration_min ? h('span', { class: 'muted' }, ` · ${s.duration_min} min`) : null),
            s.pace ? h('div', { class: 'note', style: 'margin:0' }, s.pace) : null)) : h('div', { class: 'note' }, 'Nothing planned.')),
        h('div', { class: 'card' }, h('div', { class: 'label' }, 'Running, last 7 days'),
          h('div', { class: 'big' }, `${done7}`, h('small', {}, ' km')),
          h('div', { class: 'note', style: 'margin:0' }, `${todo7} km planned for the next 7 days`)),
        form ? h('div', { class: 'card' }, h('div', { class: 'label' }, 'Form'),
          h('div', { class: 'big', style: `color:${form.zone === 'Neutral' ? css('--fg') : formColor(form.zone)}` }, form.form > 0 ? `+${form.form}` : form.form, h('small', {}, ` ${form.zone}`)),
          h('div', { class: 'note', style: 'margin:0' }, `Fitness ${form.fitness} · fatigue ${form.fatigue} · ramp ${form.ramp > 0 ? '+' : ''}${form.ramp}/wk`)) : null)),
    race ? h('div', { class: 'card race-card' },
      h('div', {}, h('div', { class: 'label' }, 'Next race'), h('div', { class: 'big' }, race.name),
        h('div', { class: 'note', style: 'margin:0' }, `${d3.utcFormat('%a %b %-d')(parse(race.day))} · ${+race.distance_km.toFixed(1)} km${race.goal_time_s ? ` · goal ${race.goal_time_s < 3600 ? `${Math.floor(race.goal_time_s / 60)}:${String(race.goal_time_s % 60).padStart(2, "0")}` : hm(race.goal_time_s)}` : ''}`),
        ro?.verdict ? h('a', { href: '#races', class: 'verdict-line' }, verdictPill(ro), h('span', { class: 'note', style: 'margin:0' }, ` ${gapText(ro)}`)) : null),
      h('div', { class: 'countdown' }, h('b', {}, days), h('span', {}, days === 1 ? 'day' : 'days')),
      shape ? h('div', { class: 'shape' }, h('div', { class: 'label' }, `Endurance shape for ${+race.distance_km.toFixed(1)} km`),
        h('div', { class: 'big' }, `${shape.pct}%`),
        h('div', { class: 'bar' }, h('div', { style: `width:${shape.pct}%;background:${shape.pct >= 90 ? css('--good') : shape.pct >= 70 ? css('--fair') : css('--poor')}` })),
        h('div', { class: 'note', style: 'margin:2px 0 0' }, `${shape.weekly_km} of ${shape.weekly_target} km/week · longest ${shape.longest_km} of ${shape.long_target} km`)) : null)
      : h('div', { class: 'card empty' }, 'No race in your calendar — add one under Races to get a plan built around it.'),
  );
}

// calendar heatmap: weeks as columns, Mon..Sun rows
function calendarChart(el, rows, o) {
  el.replaceChildren();
  if (!rows.length) { el.innerHTML = '<div class="empty">No data for this period yet</div>'; return; }
  const ds = rows.map((r) => parse(r.date));
  const mon = (d) => d3.utcMonday.floor(d);
  const weeks = d3.utcMonday.range(mon(d3.min(ds)), new Date(+mon(d3.max(ds)) + 864e5));
  const w = Math.max(280, el.clientWidth || 600);
  const cell = Math.max(8, Math.min(24, Math.floor((w - 40) / weeks.length) - 3));
  const H = 7 * (cell + 3) + 24, W = 32 + weeks.length * (cell + 3);
  const svg = d3.select(el).append('svg').attr('width', '100%').attr('viewBox', `0 0 ${Math.max(w, W)} ${H}`);
  const tip = d3.select(el).append('div').attr('class', 'tip');
  ['Mon', '', 'Wed', '', 'Fri', '', 'Sun'].forEach((n, i) => n && svg.append('text').attr('x', 26).attr('y', 20 + i * (cell + 3) + cell / 2).attr('dy', '.32em').attr('text-anchor', 'end').text(n));
  weeks.forEach((wk, i) => { if (wk.getUTCDate() <= 7) svg.append('text').attr('x', 32 + i * (cell + 3)).attr('y', 11).text(d3.utcFormat('%b')(wk)); });
  svg.append('g').selectAll('rect').data(rows).join('rect')
    .attr('x', (r) => 32 + Math.round((mon(parse(r.date)) - weeks[0]) / 6048e5) * (cell + 3))
    .attr('y', (r) => 20 + ((parse(r.date).getUTCDay() + 6) % 7) * (cell + 3))
    .attr('width', cell).attr('height', cell).attr('rx', 2).attr('fill', (r) => o.fill(r)).attr('fill-opacity', (r) => o.opacity(r))
    .on('pointermove', (e, r) => { tip.style('opacity', 1).html(''); tip.append('div').attr('class', 't').text(fDay(parse(r.date))); tip.append('div').text(o.label(r));
      const [px, py] = d3.pointer(e, el); tip.style('left', `${Math.min(px + 12, el.clientWidth - 160)}px`).style('top', `${py - 44}px`); })
    .on('pointerleave', () => tip.style('opacity', 0));
}

async function overview() {
  const q = `?days=${state.days}`;
  const [weekly, sum] = await Promise.all([load('weekly' + q, '/weekly' + q), load('sum' + q, '/summary' + q)]);
  const boxes = { steps: chartBox(160), run: chartBox(160), train: chartBox(160), sleep: chartBox(160) };
  show(
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h1', {}, 'Overview'), periodSeg(overview)),
    h('p', { class: 'lede' }, `You averaged ${sum.avg_steps ? fInt(Math.round(sum.avg_steps)) : '—'} steps a day, hit 10,000 on ${sum.days_10k} days, ran ${sum.run_km} km in ${sum.runs} runs and slept ${sum.avg_sleep_h ?? '—'} hours a night.`),
    h('div', { class: 'grid four' },
      tile('Running', sum.run_km, 'km', delta(sum.run_km, (sum.previous || {}).run_km) || `${sum.runs} runs`),
      tile('Training time', sum.train_h, 'h', delta(sum.train_h, (sum.previous || {}).train_h) || 'All recorded workouts'),
      tile('Workouts', sum.sessions, null, delta(sum.sessions, (sum.previous || {}).sessions) || 'Runs, strength and walks'),
      tile('Daily steps', sum.avg_steps ? fInt(Math.round(sum.avg_steps)) : null, null, delta(sum.avg_steps, (sum.previous || {}).avg_steps) || 'Average per day'),
      tile('Sleep', sum.avg_sleep_h, 'h', delta(sum.avg_sleep_h, (sum.previous || {}).avg_sleep_h) || 'Average per night'),
      tile('Resting HR', sum.avg_rhr, 'bpm', delta(sum.avg_rhr, (sum.previous || {}).avg_rhr, false) || 'Average'),
      tile('Overnight HRV', sum.avg_hrv, 'ms', delta(sum.avg_hrv, (sum.previous || {}).avg_hrv) || 'Average'),
      tile('VO2max', sum.vo2max, null, sum.threshold_pace ? `Threshold ${sum.threshold_pace} /km` : 'From your watch')),
    h('h2', {}, 'Week by week'),
    h('div', { class: 'grid four' },
      card('Average daily steps', 'Per week', boxes.steps), card('Running distance', 'Kilometres per week', boxes.run),
      card('Training time', 'Hours per week', boxes.train), card('Average sleep', 'Hours per night, by week', boxes.sleep)),
  );
  timeChart(boxes.steps, weekly.filter((w) => w.avg_steps != null), { x: 'week', step: 'week', h: 160, bars: [{ key: 'avg_steps', name: 'Avg steps', color: color(0) }] });
  timeChart(boxes.run, weekly, { x: 'week', step: 'week', h: 160, yfmt: f1, bars: [{ key: 'run_km', name: 'Run km', color: color(0), fmt: f1 }] });
  timeChart(boxes.train, weekly, { x: 'week', step: 'week', h: 160, yfmt: f1, bars: [{ key: 'train_h', name: 'Training h', color: color(1), fmt: f1 }] });
  timeChart(boxes.sleep, weekly.filter((w) => w.avg_sleep_h != null), { x: 'week', step: 'week', h: 160, yfmt: f1, bars: [{ key: 'avg_sleep_h', name: 'Avg sleep h', color: color(4), fmt: f1 }] });
}

// ---- check-in ----
const FEELINGS = [['good', 'Good'], ['tired', 'Tired'], ['sore', 'Sore'], ['ill', 'Ill'], ['travelling', 'Travelling']];
const FEELING_RULES = { good: 'No change; ends an earlier check-in.', tired: 'Hard sessions become easy, runs 20% shorter, strength 30 min at most.',
  sore: 'Hard sessions become easy, runs 30% shorter, no strength.', ill: 'Rest. Races stay, with a reminder to race only when symptom-free.',
  travelling: 'Easy runs of 10 km at most, 20 min of bodyweight strength.' };
const feelingName = (v) => FEELINGS.find(([k]) => k === v)?.[1] ?? 'Note';
async function checkinCard(onChange) {
  const st = await api('/checkins').catch(() => ({ active: null, recent: [] }));
  const err = h('p', { class: 'note error', role: 'status' });
  const rule = h('p', { class: 'note', style: 'margin:0' }, FEELING_RULES.tired);
  const feeling = h('select', { name: 'feeling', onchange: () => { rule.textContent = FEELING_RULES[feeling.value]; } },
    FEELINGS.map(([v, l]) => h('option', { value: v, selected: v === 'tired' ? '' : null }, l)));
  const form = h('form', { class: 'goal-form', onsubmit: async (e) => {
    e.preventDefault(); err.textContent = '';
    const d = Object.fromEntries(new FormData(form));
    try {
      await api('/checkins', { method: 'POST', body: JSON.stringify({ feeling: d.feeling, days: d.days ? Number(d.days) : null, note: d.note || '' }) });
      resetCache(); onChange();
    } catch (e2) { err.textContent = e2.message; }
  } },
  h('label', {}, h('span', {}, 'How do you feel?'), feeling),
  h('label', {}, h('span', {}, 'For how many days'), h('input', { name: 'days', type: 'number', min: 1, max: 14, placeholder: 'default' })),
  h('label', { style: 'flex:1;min-width:12rem' }, h('span', {}, 'Note (optional)'), h('input', { name: 'note', maxlength: 1000, placeholder: 'e.g. left calf tight' })),
  h('button', { class: 'primary', type: 'submit' }, 'Save'));
  const a = st.active;
  const recent = st.recent.filter((c) => !a || c.id !== a.id).slice(0, 5);
  const line = (c) => `${d3.utcFormat('%b %-d')(parse(c.day))} · ${feelingName(c.feeling)}${c.note ? ` · ${c.note}` : ''}`;
  return card('Check-in', 'Tell the plan how you feel. Fixed rules ease the sessions for a few days; the newest check-in replaces older ones.', [
    a ? h('div', { class: 'coach-note' }, h('div', { class: 'label' }, `Active until ${d3.utcFormat('%b %-d')(parse(a.until))}`), `${line(a)} — ${FEELING_RULES[a.feeling]}`) : null,
    form, rule, err,
    recent.length ? h('details', { style: 'margin-top:8px' }, h('summary', { class: 'note' }, 'Earlier check-ins'),
      recent.map((c) => h('div', { class: 'note' }, line(c), ' ',
        h('button', { class: 'link', onclick: async () => { await api(`/checkins/${c.id}`, { method: 'DELETE' }); resetCache(); onChange(); } }, 'Delete')))) : null,
  ]);
}

async function plan() {
  const p = await load('plan', '/plan');
  const race = p.target_race;
  const first = p.race_plan[0];
  const head = race && first
    ? `${p.athlete_name}, you’re ${first.weeks_to_go} week${first.weeks_to_go === 1 ? '' : 's'} from ${race.name}, in the ${first.phase.toLowerCase()} phase.`
    : `${p.athlete_name}, no race yet — this keeps your fitness ticking over with one quality session a week.`;
  const roadBox = chartBox(260), tableBox = h('div', { class: 'tablebox' });
  show(
    h('h1', {}, 'Training plan'),
    h('p', { class: 'lede' }, head, ' ', `Readiness: ${p.readiness.status.toLowerCase()} (${p.readiness.reasons.join('; ')}).`),
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h2', { style: 'margin:0' }, 'To do · next 7 days'),
      h('span', { class: 'note', style: 'margin:0' }, `~${p.next_7_days.todo_km} km of running, in this order. Fit them around your week.`)),
    sessionsGrid(p.next_7_days.todo, 'Nothing planned for the next 7 days.'),
    h('div', { class: 'row', style: 'justify-content:space-between;margin-top:24px' }, h('h2', { style: 'margin:0' }, 'Done · last 7 days'),
      h('span', { class: 'note', style: 'margin:0' }, `${p.last_7_days.done_km} km of running`)),
    sessionsGrid(p.last_7_days.done, 'Nothing recorded in the last 7 days.'),
    h('div', { style: 'height:16px' }),
    await checkinCard(plan),
    race ? h('h2', {}, 'Road to race day') : null,
    race ? card('Weekly distance', 'Grey: what you actually ran. Colour: the plan by phase. Line: long run.', [
      h('div', { class: 'legend' }, ['Base', 'Build', 'Peak', 'Taper', 'Race week'].map((x) => h('span', {}, h('span', { class: 'sw', style: `background:${phaseColor(x)}` }), x))),
      roadBox]) : null,
    race ? h('div', { style: 'height:16px' }) : null,
    race ? card('Week by week', 'Every fourth week is easier; B and C races reshape their week.', tableBox) : null,
    h('p', { class: 'note' }, 'Everything here comes from fixed rules: your recent training, readiness, races and check-ins. Skip or shorten anything if you feel run-down or sore.'),
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

// ---- race outlook ----
const verdictCls = (v) => ({ 'On track': 'good', 'Within reach': 'reach', Stretch: 'stretch', Ambitious: 'far' }[v] || '');
const clockT = (s) => (s == null ? '—' : s >= 3600 ? `${Math.floor(s / 3600)}:${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}:${String(Math.round(s % 60)).padStart(2, '0')}` : pace(s));
const verdictPill = (o) => (o?.verdict ? h('span', { class: `verdict ${verdictCls(o.verdict)}` }, o.verdict) : null);
const gapT = (s) => (s < 60 ? `${Math.round(s)} s` : clockT(s));
const gapText = (o) => (o.gap_s == null ? null : o.gap_s <= 0 ? `${gapT(-o.gap_s)} faster than goal` : `${gapT(o.gap_s)} (${(o.gap_pct * 100).toFixed(1)}%) slower than goal`);

function outlookCard(o) {
  const fact = (label, value, sub) => h('div', { class: 'fact' }, h('div', { class: 'label' }, label), h('div', { class: 'fact-v' }, value), sub ? h('div', { class: 'note', style: 'margin:0' }, sub) : null);
  const b = o.best_effort;
  const facts = h('div', { class: 'facts' },
    fact('Goal', clockT(o.goal_time_s), o.goal_pace_s ? `${pace(o.goal_pace_s)} /km` : 'No goal set'),
    fact('Estimate today', clockT(o.estimate_s), o.predicted_s ? `Watch prediction (from ${o.predicted_from})` : b ? 'From your best recent run' : 'Not enough data'),
    fact('Best recent run', b ? `${b.km} km · ${pace(b.pace_s)} /km` : '—', b ? `${d3.utcFormat('%b %-d')(parse(b.day))} · ≈ ${clockT(b.equivalent_s)} for the race` : 'Last 8 weeks'),
    o.goal_pace_s ? fact('At goal pace', `${o.goal_pace_km_4w} km`, `Last 4 weeks · ${o.runs_4w} runs`) : null,
    fact('Endurance shape', `${o.shape.pct}%`, `${o.shape.weekly_km} of ${o.shape.weekly_target} km/week`));
  const sum = o.summary;
  const reading = sum ? h('div', { class: 'coach-note' }, h('div', { class: 'label' }, 'Reading'), sum.text,
    h('ul', { class: 'fuel' }, sum.focus.map((f) => h('li', {}, f)))) : null;
  return h('div', { class: 'card outlook' },
    h('div', { class: 'row', style: 'justify-content:space-between;margin:0 0 4px' },
      h('h3', {}, o.race, ' ', h('span', { class: `pill ${o.priority}` }, o.priority)), verdictPill(o)),
    h('p', { class: 'note' }, `${d3.utcFormat('%a %b %-d')(parse(o.day))} · ${+o.distance_km.toFixed(1)} km · ${o.weeks_to_go} weeks to go${gapText(o) ? ` · ${gapText(o)}` : ''}`),
    facts,
    reading || h('p', { class: 'note' }, 'Add a goal time (Edit) to see whether you’re on track for it.'));
}

// ---- races via Claude ----
function raceClaudeCard(list) {
  const proposalCard = (p) => {
    const existing = p.race_id ? list.find((r) => r.id === p.race_id) : null;
    const btn = h('button', { class: 'primary', onclick: async () => {
      btn.disabled = true;
      try {
        const body = { name: p.name, date: p.date, distance_km: Number(p.distance_km), goal_time: p.goal_time || null, priority: p.priority || 'A',
          notes: [p.location, p.start_time && `start ${p.start_time}`, p.notes, p.source_url].filter(Boolean).join(' · ') || null };
        await api(existing ? `/races/${existing.id}` : '/races', { method: existing ? 'PUT' : 'POST', body: JSON.stringify(body) });
        resetCache(); races();
      } catch (e) { btn.disabled = false; btn.textContent = `Failed: ${e.message}`; }
    } }, existing ? 'Update race' : 'Add to calendar');
    return h('div', { class: 'proposal' },
      h('div', {}, h('div', { class: 'next-name' }, p.name),
        h('div', { class: 'note', style: 'margin:2px 0' }, `${d3.utcFormat('%a %b %-d, %Y')(parse(p.date))} · ${+Number(p.distance_km).toFixed(2)} km · goal ${p.goal_time || '—'} · priority ${p.priority}`),
        p.location || p.start_time ? h('div', { class: 'note', style: 'margin:0' }, [p.location, p.start_time && `start ${p.start_time}`].filter(Boolean).join(' · ')) : null,
        p.source_url ? h('a', { href: p.source_url, target: '_blank', rel: 'noopener', class: 'note' }, 'Source') : null),
      btn);
  };
  return card('Add a race with Claude', 'Describe the race, copy the prompt into Claude (with web search on), then paste its answer back. Nothing is saved until you click Add.',
    claudeHandoff({ h, api, promptPath: '/races/prompt', importPath: '/races/import', renderProposal: proposalCard,
      placeholder: 'e.g. Lisbon Half Marathon, goal 1:45:00' }));
}

async function races() {
  const [list, ol] = await Promise.all([api('/races'), api('/races/outlook').catch(() => ({ races: [] }))]);
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
  show(
    h('h1', {}, 'Races'),
    h('p', { class: 'lede' }, 'Your training plan is built around these. A = main race (full build and taper), B = tune-up (mini-taper, easy days after), C = training race (replaces that week’s hard session).'),
    ol.races.length ? [h('h2', { style: 'margin-top:0' }, 'Am I on track?'),
      h('p', { class: 'note' }, 'Your goal pace against what your watch predicts and what your recent runs show. Verdicts: on track, within reach (≤2% off), stretch (≤5%), ambitious.'),
      h('div', { class: 'stack' }, ol.races.map(outlookCard)),
      h('div', { style: 'height:24px' })] : null,
    raceClaudeCard(list),
    h('div', { style: 'height:16px' }),
    card('Add a race manually', null, form),
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
  const [weekly, daily, acts, sum, fit] = await Promise.all([load('weekly' + q, '/weekly' + q), load('daily' + q, '/daily' + q), load('acts' + q, '/activities' + q), load('sum' + q, '/summary' + q), load('fit' + q, '/fitness' + q)]);
  const runs = acts.filter((a) => a.type === 'Run' && a.km && a.pace_s).map((a) => ({ ...a, eff: a.avg_hr ? +(a.km * 1000 / (a.duration_s / 60) / a.avg_hr).toFixed(2) : null }));
  const daysRows = daily.map((d, i, arr) => ({ ...d, date: d.day, steps_7d: Math.round(d3.mean(arr.slice(Math.max(0, i - 6), i + 1), (x) => x.steps || 0)) }));
  const boxes = { pmc: chartBox(280), km: chartBox(220), types: chartBox(220), steps: chartBox(240), pace: chartBox(260), eff: chartBox(220), load: chartBox(220), ratio: chartBox(170), actcal: chartBox(120), bytype: chartBox(120), stepcal: chartBox(120), weekday: chartBox(200), kcal: chartBox(220) };
  show(
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h1', {}, 'Trends'), periodSeg(trends)),
    h('div', { class: 'grid tiles' },
      tile('Running', sum.run_km, 'km', `${sum.runs} runs`), tile('Training', sum.train_h, 'h', `${sum.sessions} sessions`),
      tile('Daily steps', sum.avg_steps ? fInt(Math.round(sum.avg_steps)) : null, null, `${sum.days_10k} days over 10k`),
      tile('VO2max', sum.vo2max, null, sum.threshold_pace ? `Threshold ${sum.threshold_pace} /km` : null)),
    h('h2', {}, 'Fitness, fatigue and form'),
    card('Performance', 'Fitness = 42-day average training load; fatigue = 7-day average; form = fitness − fatigue. Bars show form, coloured by zone.', [
      h('div', { class: 'legend' }, [['Fitness', css('--c1')], ['Fatigue', css('--c4')]].map(([n, c]) => h('span', {}, h('span', { class: 'sw', style: `background:${c}` }), n)),
        ['Fresh', 'Neutral', 'Optimal training', 'High risk', 'Transition'].map((z) => h('span', {}, h('span', { class: 'sw', style: `background:${formColor(z)}` }), z))),
      boxes.pmc]),
    h('h2', {}, 'Training'),
    h('div', { class: 'grid' },
      card('Weekly running distance', 'Kilometres per week', boxes.km),
      card('Weekly training time by type', 'Hours per week', [h('div', { class: 'legend' }, ['Run', 'Strength', 'Walk', 'Other'].map((t) => h('span', {}, h('span', { class: 'sw', style: `background:${typeColor(t)}` }), t))), boxes.types])),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Pace vs heart rate', 'Each dot is a run; bigger = longer, darker = more recent. Faster sits higher.', boxes.pace),
      card('Running efficiency', 'Metres per heartbeat — higher means more speed for the same effort.', boxes.eff)),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Training load', 'Short-term vs long-term load from your watch. COROS shares the last 31 days, so this builds up over time; the fitness chart above covers the long view.', boxes.load),
      card('Load ratio', 'Short over long-term load. Under 0.8 detraining, over 1.5 excessive.', boxes.ratio)),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Activity calendar', 'Coloured by the day’s main activity; darker = longer.', boxes.actcal),
      card('Time by activity type', 'Total hours in the period.', boxes.bytype)),
    h('h2', {}, 'Movement'),
    card('Daily steps', 'Bars coloured on days at 10,000+; the line is the 7-day average.', boxes.steps),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Step calendar', 'Darker = more steps.', boxes.stepcal),
      card('Steps by day of the week', 'Average steps per weekday.', boxes.weekday)),
    h('div', { style: 'height:16px' }),
    card('Active calories', 'Calories burned through activity each day.', boxes.kcal),
    h('h2', {}, 'Activities'),
    h('div', { class: 'card tablebox' }, h('table', {},
      h('thead', {}, h('tr', {}, ['Date', 'Type', 'Name', 'Time', 'km', 'Pace', 'Avg HR'].map((x, i) => h('th', { class: i >= 3 ? 'num' : i === 2 ? 'hide-sm' : '' }, x)))),
      h('tbody', {}, acts.map((a) => h('tr', {}, h('td', {}, d3.utcFormat('%b %-d')(parse(a.date))),
        h('td', {}, h('span', { class: 'sw', style: `background:${typeColor(a.type)}` }), a.type), h('td', { class: 'hide-sm' }, a.name || a.sport),
        h('td', { class: 'num' }, hm(a.duration_s)), h('td', { class: 'num' }, a.km ?? ''), h('td', { class: 'num' }, a.type === 'Run' ? pace(a.pace_s) : ''), h('td', { class: 'num' }, a.avg_hr ?? ''))))))
  );
  timeChart(boxes.pmc, fit, { h: 280, ymin: Math.min(0, d3.min(fit, (r) => r.form) ?? 0), yfmt: d3.format('d'),
    bars: [{ key: 'form', name: 'Form', color: (r) => formColor(r.zone), opacity: () => 0.55 }],
    lines: [{ key: 'fitness', name: 'Fitness', color: css('--c1') }, { key: 'fatigue', name: 'Fatigue', color: css('--c4') }],
    refs: [{ v: 0 }], extra: (r) => [{ name: 'Zone', value: r.zone }, { name: 'Day load', value: r.load }] });
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
  timeChart(boxes.ratio, daysRows.filter((d) => d.load_ratio != null), { h: 170, ymin: 0.4, yfmt: d3.format('.1f'), refs: [{ v: 0.8, label: '0.8' }, { v: 1.5, label: '1.5' }],
    bars: [{ key: 'load_ratio', name: 'Ratio', fmt: d3.format('.2f'), color: (r) => (r.load_ratio > 1.5 ? css('--bad') : r.load_ratio < 0.8 ? css('--grey') : color(0)) }] });
  const byDay = d3.rollups(acts, (v) => ({ minutes: d3.sum(v, (a) => a.duration_s || 0) / 60, main: d3.greatest(d3.rollups(v, (x) => d3.sum(x, (a) => a.duration_s || 0), (a) => a.type), (x) => x[1])[0], types: [...new Set(v.map((a) => a.type))].join(' + ') }), (a) => a.date).map(([date, x]) => ({ date, ...x }));
  calendarChart(boxes.actcal, byDay, { fill: (r) => typeColor(r.main), opacity: (r) => 0.35 + 0.65 * Math.min(1, r.minutes / 90), label: (r) => `${r.types} · ${Math.round(r.minutes)} min` });
  const types = d3.rollups(acts, (v) => d3.sum(v, (a) => a.duration_s || 0) / 3600, (a) => a.type).sort((a, b) => b[1] - a[1]);
  boxes.bytype.replaceChildren(...types.map(([t, hrs]) => h('div', { style: 'display:flex;align-items:center;gap:8px;margin:6px 0' }, h('span', { style: 'width:72px;font-size:13px' }, t),
    h('div', { style: `height:14px;border-radius:3px;background:${typeColor(t)};width:${Math.max(2, (hrs / (types[0]?.[1] || 1)) * 70)}%` }), h('span', { class: 'note', style: 'margin:0' }, `${f1(hrs)} h`))));
  const stepDays = daysRows.filter((d) => d.steps != null);
  const maxSteps = d3.max(stepDays, (d) => d.steps) || 1;
  calendarChart(boxes.stepcal, stepDays, { fill: () => color(0), opacity: (r) => 0.12 + 0.88 * Math.min(1, r.steps / maxSteps), label: (r) => `${fInt(r.steps)} steps` });
  const wd = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map((day, i) => ({ day, avg: Math.round(d3.mean(stepDays.filter((d) => (parse(d.date).getUTCDay() + 6) % 7 === i), (d) => d.steps) || 0) }));
  boxes.weekday.replaceChildren(...(stepDays.length ? wd.map((x) => h('div', { style: 'display:flex;align-items:center;gap:8px;margin:5px 0' }, h('span', { style: 'width:36px;font-size:13px' }, x.day),
    h('div', { style: `height:14px;border-radius:3px;background:${x.day[0] === 'S' ? color(1) : color(0)};width:${(x.avg / (d3.max(wd, (y) => y.avg) || 1)) * 70}%` }), h('span', { class: 'note', style: 'margin:0' }, fInt(x.avg)))) : [h('div', { class: 'empty' }, 'No step data yet')]));
  timeChart(boxes.kcal, daysRows.filter((d) => d.active_kcal != null), { h: 220, bars: [{ key: 'active_kcal', name: 'Active kcal', color: color(2) }], extra: (r) => [{ name: 'Exercise', value: `${r.exercise_min ?? 0} min` }] });
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
  show(
    h('div', { class: 'row', style: 'justify-content:space-between' }, h('h1', {}, 'Recovery'), periodSeg(recovery)),
    card('Sleep each night', 'Time in each stage, by the day you woke up.', [h('div', { class: 'legend' }, [['Deep', color(4)], ['Light', color(5)], ['REM', color(6)], ['Awake', css('--grey')]].map(([n, c]) => h('span', {}, h('span', { class: 'sw', style: `background:${c}` }), n))), boxes.sleep]),
    h('div', { style: 'height:16px' }),
    h('div', { class: 'grid' },
      card('Overnight HRV', 'Shaded band is your normal range.', boxes.hrv),
      card('Resting heart rate', 'Grey line is your 28-day baseline. Several beats above it often follows hard days, poor sleep or illness.', boxes.hr)),
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
  const rhrRows = rows.filter((r) => r.resting_hr).map((r, i, arr) => ({ ...r, rhr_base: +d3.mean(arr.slice(Math.max(0, i - 27), i + 1), (x) => x.resting_hr).toFixed(1) }));
  timeChart(boxes.hr, rhrRows, { h: 220, ymin: 40, lines: [{ key: 'resting_hr', name: 'Resting HR', color: color(0) }, { key: 'rhr_base', name: '28-day baseline', color: css('--muted') }] });
  timeChart(boxes.score, rows.filter((r) => r.sleep_score), { h: 200, ymax: 100, bars: [{ key: 'sleep_score', name: 'Score', color: (r) => (r.sleep_score < 70 ? css('--bad') : color(4)) }] });
  timeChart(boxes.bed, rows.filter((r) => r.bed_h), { h: 220, ymin: 21, yfmt: clock, refs: [{ v: 23, label: '23:00' }], lines: [{ key: 'bed_h', name: 'Fell asleep', color: color(4), dots: true, noLine: true, fmt: clock }] });
  timeChart(boxes.stress, rows.filter((r) => r.stress_avg != null), { h: 200, bars: [{ key: 'stress_avg', name: 'Stress', color: (r) => (r.stress_avg >= 40 ? color(3) : css('--grey')) }] });
}

// ---- COROS connection ----
async function corosBanner() {
  const el = $('#banner');
  let st;
  try { st = await api('/coros'); } catch { return; }
  state.coros = st;
  if (st.provider !== 'coros-mcp' || st.connected) { el.replaceChildren(); return; }
  const err = h('div', { class: 'error', style: 'font-size:13px' });
  const pasteIn = h('input', { placeholder: 'http://localhost/homecoach-coros-callback?code=…', style: 'flex:1' });
  const pasteForm = h('form', { class: 'chat-form', onsubmit: async (e) => {
    e.preventDefault();
    try { await api('/coros/paste', { method: 'POST', body: JSON.stringify({ url: pasteIn.value.trim() }) }); resetCache(); await corosBanner(); syncStatus(); route(); }
    catch (e2) { err.textContent = e2.message; }
  } }, pasteIn, h('button', { class: 'primary', type: 'submit' }, 'Finish'));
  const pasteSteps = (url) => el.replaceChildren(h('div', { class: 'banner' },
    h('div', { style: 'flex-basis:100%' }, h('b', {}, 'Connect your COROS account'),
      h('ol', { class: 'steps' },
        h('li', {}, h('a', { href: url, target: '_blank', rel: 'noopener' }, 'Sign in with COROS ↗'), ' (opens a new tab).'),
        h('li', {}, 'After you sign in, the tab shows an error page at ', h('code', {}, 'localhost/homecoach-coros-callback…'), '. That’s expected: COROS only sends logins back to https or to localhost.'),
        h('li', {}, 'Copy the full address from that tab’s address bar and paste it here:'))),
    pasteForm, err));
  const begin = async (paste) => {
    err.textContent = '';
    try {
      const out = await api('/coros/connect', { method: 'POST', body: JSON.stringify({ paste }) });
      if (out.paste) pasteSteps(out.url); else location.href = out.url;
    } catch (e) { err.textContent = e.message; }
  };
  const connect = h('button', { class: 'primary', onclick: async () => { connect.disabled = true; await begin(false); connect.disabled = false; } }, 'Connect COROS');
  const alt = h('button', { class: 'link', onclick: () => begin(true) }, 'Login doesn’t come back here?');
  el.replaceChildren(h('div', { class: 'banner' },
    h('div', {}, h('b', {}, 'Connect your COROS account'),
      h('div', { class: 'note', style: 'margin:2px 0 0' }, st.error ? `Last attempt failed: ${st.error}` : 'Sign in once with COROS. homecoach keeps a token, not your password, and syncs every 10 minutes.')),
    h('div', { class: 'row', style: 'margin:0;gap:4px' }, connect, alt),
    err));
}

// ---- full resync ----
async function resyncWatch() {
  const btn = $('#resync'), el = $('#notice');
  let st;
  try { st = await api('/resync'); } catch { return; }
  btn.disabled = !!st.running;
  btn.textContent = st.running ? '↻ Resyncing…' : '↻ Full resync';
  if (st.running) {
    el.replaceChildren(h('div', { class: 'banner' }, h('div', {}, h('b', {}, 'Full resync running'),
      h('div', { class: 'note', style: 'margin:2px 0 0' }, `${st.step || 'Working'}… This can take a few minutes; you can keep using homecoach.`))));
    setTimeout(resyncWatch, 5000);
    state.resyncSeen = true;
    return;
  }
  if (!state.resyncSeen || !st.finished_at) return;  // only report a resync started in this tab
  state.resyncSeen = false;
  const s = st.stats || {};
  resetCache(); syncStatus(); route();
  const partial = Object.values(st.stages || {}).includes('failed');
  el.replaceChildren(h('div', { class: `banner${st.error || partial ? ' bad' : ''}` },
    h('div', {}, h('b', {}, st.error ? 'Full resync failed' : partial ? 'Full resync finished with issues' : 'Full resync finished'),
      h('div', { class: 'note', style: 'margin:2px 0 0' }, st.error ? st.error
        : `${st.stats ? `${s.activities_seen ?? 0} activities (${s.activities_new ?? 0} new) and ${s.days ?? 0} days of health data.` : 'FIT inbox checked; no provider data synced.'} Training plan rebuilt.`),
      (st.warnings || []).map((warning) => h('p', { class: 'note' }, warning))),
    h('button', { class: 'link', onclick: () => el.replaceChildren() }, 'Dismiss')));
}
$('#resync').addEventListener('click', async () => {
  if (!confirm('Run a full resync?\n\nThis re-reads your history and rebuilds the training plan. Races and check-ins are kept. It can take a few minutes.')) return;
  try { await api('/resync', { method: 'POST' }); state.resyncSeen = true; resyncWatch(); }
  catch (e) { $('#notice').replaceChildren(h('div', { class: 'banner bad' }, h('div', {}, h('b', {}, 'Couldn’t start the resync'), h('div', { class: 'note' }, e.message)))); }
});

// ---- router -----------------------------------------------------------------------------------

const pages = { today, overview, plan, races, trends, recovery, ...trainingPages({ api, h, show, card, resetCache, pace, hm }), ...nutritionPages({ api, h, show, card, resetCache }) };
async function route() {
  const name = location.hash.slice(1) || 'today';
  document.querySelectorAll('#tabs a').forEach((a) => a.classList.toggle('active', a.getAttribute('href') === `#${name}`));
  show(h('div', { class: 'empty' }, 'Loading…'));
  try { await (pages[name] || today)(); } catch (e) { show(h('div', { class: 'empty error' }, `Couldn’t load: ${e.message}`)); }
}
async function syncStatus() {
  try {
    const cfg = await api('/config');
    const st = Object.values(cfg.sync || {}).filter((x) => x?.last_sync).sort((a, b) => (a.last_sync < b.last_sync ? 1 : -1))[0];
    const status = cfg.sync?.['collector-status'];
    $('#sync').replaceChildren(status?.error ? `Last sync failed (${status.error}) · ` : '', st?.last_sync ? `Synced ${d3.timeFormat('%H:%M')(new Date(st.last_sync))}` : 'Waiting for first sync',
      state.coros?.connected ? h('button', { class: 'link', title: `Connected to COROS since ${state.coros.connected_at ? new Date(state.coros.connected_at).toLocaleDateString() : '?'}. Click to disconnect.`,
        onclick: async () => { if (confirm('Disconnect COROS? Syncing stops until you connect again.')) { await api('/coros/disconnect', { method: 'POST' }); corosBanner().then(syncStatus); } } }, '· COROS ✓') : '');
  } catch { $('#sync').textContent = ''; }
}
try { if (localStorage.getItem('hc.theme') === 'light') document.documentElement.dataset.theme = 'light'; } catch { /* storage blocked: stay dark */ }
$('#theme').addEventListener('click', () => {
  const light = document.documentElement.dataset.theme !== 'light';
  if (light) document.documentElement.dataset.theme = 'light'; else delete document.documentElement.dataset.theme;
  try { localStorage.setItem('hc.theme', light ? 'light' : 'dark'); } catch { /* storage blocked: theme lasts this visit */ }
  route();
});
window.addEventListener('hashchange', route);
let resizeT; window.addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(route, 250); });
setInterval(() => { resetCache(); syncStatus(); if ((location.hash.slice(1) || 'today') !== 'races') route(); }, 10 * 60 * 1000);
route(); corosBanner().then(syncStatus); resyncWatch();
