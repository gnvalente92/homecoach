// Goals and history use the same DOM helpers and visual conventions as the other pages.
export function trainingPages({ api, h, show, card, resetCache, pace, hm }) {
  const sports = ['All', 'Run', 'Ride', 'Swim', 'Strength', 'Walk', 'Other'];
  const metrics = { distance_km: ['Distance', 'km'], duration_h: ['Time', 'h'], sessions: ['Sessions', 'sessions'] };
  const select = (name, options, value) => h('select', { name, 'aria-label': name },
    options.map(([key, label]) => h('option', { value: key, selected: key === value ? '' : null }, label)));
  const labelled = (text, field) => h('label', {}, h('span', {}, text), field);
  const number = (value) => Number(value.toFixed(2)).toLocaleString();
  // 217.39 h -> "217 h 23 min"; 131.468 km -> "131.5 km"; one formatter per measure so every view reads the same
  const hours = (h) => { const m = Math.round(h * 60); const hh = Math.floor(m / 60), mm = m % 60; return mm ? `${hh.toLocaleString()} h ${mm} min` : `${hh.toLocaleString()} h`; };
  const km = (v) => `${Number(v.toFixed(1)).toLocaleString()} km`;
  const amount = (metric, v) => (metric === 'duration_h' ? hours(v) : metric === 'distance_km' ? km(v) : `${v} ${v === 1 ? 'session' : 'sessions'}`);
  const feedback = () => h('p', { class: 'note error', role: 'status', 'aria-live': 'polite' });
  const asDate = (iso) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, { day: 'numeric', month: 'short' });

  async function goals() {
    const items = await api('/goals');
    const error = feedback();
    const period = select('period', [['weekly', 'Weekly'], ['monthly', 'Monthly'], ['yearly', 'Yearly']], 'weekly');
    const sport = select('sport', sports.map((s) => [s, s]), 'Run');
    const metric = select('metric', Object.entries(metrics).map(([key, [name, unit]]) => [key, `${name} (${unit})`]), 'distance_km');
    const target = h('input', { name: 'target', type: 'number', min: '0.01', max: '1000000', step: '0.01', required: '', placeholder: 'e.g. 30' });
    const save = h('button', { class: 'primary', type: 'submit' }, 'Add goal');
    let editing = null;
    const form = h('form', { class: 'goal-form', onsubmit: async (event) => {
      event.preventDefault(); error.textContent = ''; save.disabled = true;
      try {
        await api(editing ? `/goals/${editing}` : '/goals', { method: editing ? 'PUT' : 'POST', body: JSON.stringify({ period: period.value, sport: sport.value, metric: metric.value, target: Number(target.value) }) });
        resetCache(); await goals();
      } catch (err) { error.textContent = err.message; save.disabled = false; }
    } }, labelled('Repeats', period), labelled('Sport', sport), labelled('Measure', metric), labelled('Target', target), save,
    h('button', { type: 'button', class: 'link', onclick: () => { editing = null; save.textContent = 'Add goal'; form.reset(); } }, 'Clear'));
    metric.addEventListener('change', () => { const whole = metric.value === 'sessions'; target.step = whole ? '1' : '0.01'; target.min = whole ? '1' : '0.01'; });
    const cards = items.map((goal) => {
      return card(`${goal.sport === 'All' ? 'All sports' : goal.sport} · ${goal.period}`, `${asDate(goal.start)} – ${asDate(goal.end)}`,
        [h('div', { class: 'big' }, amount(goal.metric, goal.value), h('small', {}, ` of ${amount(goal.metric, goal.target)}`)),
          h('progress', { class: 'goal-progress', value: Math.min(100, goal.percent), max: 100, 'aria-label': `${goal.sport} ${goal.period} ${metrics[goal.metric][0]} progress` }),
          h('p', { class: 'note' }, goal.complete ? `Goal reached · ${number(goal.percent)}%`
            : `${amount(goal.metric, goal.remaining)} to go · ${goal.days_left === 0 ? 'last day today' : `${goal.days_left} ${goal.days_left === 1 ? 'day' : 'days'} left`}`),
          goal.missing_sessions ? h('p', { class: 'note' }, `${goal.missing_sessions} activity/activities have no recorded ${metrics[goal.metric][0].toLowerCase()}; progress may be incomplete.`) : null,
          h('div', { class: 'row' },
            h('button', { class: 'link', onclick: () => { editing = goal.id; period.value = goal.period; sport.value = goal.sport; metric.value = goal.metric; target.value = goal.target; metric.dispatchEvent(new Event('change')); save.textContent = 'Save goal'; target.focus(); } }, 'Edit'),
            h('button', { class: 'link', onclick: async () => {
              if (!confirm('Delete this goal? Your activities will be kept.')) return;
              try { await api(`/goals/${goal.id}`, { method: 'DELETE' }); resetCache(); await goals(); }
              catch (err) { error.textContent = err.message; }
            } }, 'Delete'))]);
    });
    show(h('h1', {}, 'Goals'), h('p', { class: 'lede' }, 'Track recurring distance, time or session goals. Goals do not change your training plan or ask you to make up missed training.'),
      h('div', { class: 'grid goal-cards' }, cards.length ? cards : h('div', { class: 'card empty' }, 'No goals yet. Set one below; activities already recorded in this period count too.')),
      card('Set a goal', 'Weeks start on Monday. Months and years use calendar dates in your configured timezone.', [form, error]));
  }

  let filters = { sport: 'All', q: '', start: '', end: '', offset: 0, year: null };
  async function log() {
    const config = await api('/config');
    const currentYear = Number(config.today.slice(0, 4));
    filters.year ??= currentYear;
    const params = new URLSearchParams({ sport: filters.sport, q: filters.q, limit: 50, offset: filters.offset });
    if (filters.start) params.set('start', filters.start);
    if (filters.end) params.set('end', filters.end);
    const [data, monthly] = await Promise.all([api(`/training/log?${params}`), api(`/training/monthly?year=${filters.year}&sport=${filters.sport}`)]);
    const error = feedback();
    const sport = select('sport', sports.map((s) => [s, s]), filters.sport);
    const year = h('input', { type: 'number', name: 'year', min: 1971, max: currentYear, value: filters.year, required: '' });
    const historyControls = h('form', { class: 'row', onsubmit: (event) => { event.preventDefault(); filters = { ...filters, sport: sport.value, year: Number(year.value), offset: 0 }; refresh(); } },
      labelled('Sport', sport), labelled('Year', year), h('button', { class: 'primary', type: 'submit' }, 'Update'));
    const q = h('input', { name: 'q', value: filters.q, maxlength: 100, placeholder: 'Activity or sport name' });
    const start = h('input', { name: 'start', type: 'date', value: filters.start, max: config.today });
    const end = h('input', { name: 'end', type: 'date', value: filters.end, max: config.today });
    const search = h('form', { class: 'goal-form', onsubmit: (event) => {
      event.preventDefault(); filters = { ...filters, q: q.value, start: start.value, end: end.value, offset: 0 }; refresh();
    } }, labelled('Search', q), labelled('From', start), labelled('To', end), h('button', { class: 'primary', type: 'submit' }, 'Search'));
    const table = (headings, rows) => h('div', { class: 'tablebox' }, h('table', { class: 'training-table' },
      h('thead', {}, h('tr', {}, headings.map((text) => h('th', { scope: 'col' }, text)))), h('tbody', {}, rows)));
    // every column reads "this year / last year"; "—" means no data yet (before your first activity), not zero
    const both = (month, fmt) => h('td', { class: 'num' }, month.current ? fmt(month.current) : '—', h('span', { class: 'muted' }, ` / ${month.previous ? fmt(month.previous) : '—'}`));
    const y = filters.year;
    const monthTable = table(['Month', `Distance (${y} / ${y - 1})`, `Time (${y} / ${y - 1})`, `Sessions (${y} / ${y - 1})`], monthly.months.map((month) =>
      h('tr', {}, h('td', {}, `${new Date(`${month.month}T12:00:00`).toLocaleDateString(undefined, { month: 'short' })}${month.partial ? ' (so far)' : ''}`),
        both(month, (x) => km(x.distance_km)), both(month, (x) => hours(x.duration_h)), both(month, (x) => String(x.sessions)))));
    monthTable.querySelector('table').classList.add('months');
    const rows = data.items.map((a) => h('tr', {}, h('td', {}, a.date), h('td', {}, a.type), h('td', {}, a.name),
      h('td', {}, a.km == null ? '—' : `${a.km} km`), h('td', {}, a.duration_s == null ? '—' : hm(a.duration_s)),
      h('td', {}, a.pace_s ? `${pace(a.pace_s)} /km` : '—'), h('td', {}, a.avg_hr ? `${a.avg_hr} bpm` : '—')));
    const refresh = async () => { try { await log(); } catch (err) { error.textContent = err.message; } };
    const pageButton = (label, next, disabled) => h('button', { class: 'link', disabled: disabled ? '' : null, onclick: () => { filters.offset = next; refresh(); } }, label);
    show(h('h1', {}, 'Training log'), h('p', { class: 'lede' }, 'Browse your recorded activities and compare calendar months with last year.'), historyControls,
      card(`Monthly training · ${filters.year}`, 'Each column shows this year / last year for the selected sport. The current month is compared with the same number of days last year. “—” means there is no data yet for that month.', monthTable),
      h('h2', {}, 'Activities'), search, error,
      data.total ? table(['Date', 'Sport', 'Activity', 'Distance', 'Time', 'Pace', 'Heart rate'], rows) : h('div', { class: 'card empty' }, 'No activities match these filters.'),
      h('div', { class: 'row' }, pageButton('Previous', Math.max(0, filters.offset - 50), filters.offset === 0),
        h('span', { class: 'note' }, data.total ? `${filters.offset + 1}–${Math.min(filters.offset + 50, data.total)} of ${data.total}` : '0 activities'),
        pageButton('Next', filters.offset + 50, filters.offset + 50 >= data.total)));
  }
  return { goals, log };
}
