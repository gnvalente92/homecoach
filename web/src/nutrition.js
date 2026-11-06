// Nutrition: daily targets from training, today's food log and the food chat.
export function nutritionPages({ api, h, show, card, resetCache }) {
  const MEALS = ['breakfast', 'lunch', 'dinner', 'snack'];
  const LOAD = { Rest: 'var(--grey)', Light: 'var(--c6)', Moderate: 'var(--c1)', High: 'var(--c2)', 'Carb loading': 'var(--c5)', Race: 'var(--c4)' };
  const cap = (s) => s.charAt(0).toUpperCase() + s.slice(1);
  const fmtDay = (iso, opts = { weekday: 'short', day: 'numeric', month: 'short' }) => new Date(`${iso}T12:00:00`).toLocaleDateString(undefined, opts);
  const n = (v) => Math.round(v).toLocaleString();
  const state = { week: 0, chat: [], day: null };

  const loadPill = (load) => h('span', { class: 'load-pill', style: `--lc:${LOAD[load] || 'var(--grey)'}` }, load);

  function macroBar(label, eaten, target, unit, color) {
    const pct = target ? Math.min(100, (100 * eaten) / target) : 0;
    const over = target && eaten > target * 1.1;
    return h('div', { class: 'macro' },
      h('div', { class: 'contrib-top' }, h('span', {}, label), h('span', {}, h('b', {}, `${n(eaten)}`), ` / ${n(target)} ${unit}`)),
      h('div', { class: 'bar' }, h('div', { style: `width:${pct}%;background:${over ? 'var(--fair)' : color}` })));
  }

  function profileForm(p, onSaved) {
    const err = h('p', { class: 'note error', role: 'status' });
    const field = (label, input) => h('label', {}, h('span', {}, label), input);
    const f = h('form', { class: 'goal-form', onsubmit: async (e) => {
      e.preventDefault(); err.textContent = '';
      const d = Object.fromEntries(new FormData(f));
      try {
        await api('/nutrition/profile', { method: 'PUT', body: JSON.stringify({ weight_kg: Number(d.weight_kg), height_cm: Number(d.height_cm), birth_date: d.birth_date,
          sex: d.sex, goal: d.goal, protein_g_per_kg: Number(d.protein_g_per_kg), follow_provider: d.follow_provider === 'on' }) });
        resetCache(); onSaved();
      } catch (e2) { err.textContent = e2.message; }
    } },
    field('Weight (kg)', h('input', { name: 'weight_kg', type: 'number', step: '0.1', min: 25, max: 300, required: '', value: p.weight_kg ?? '' })),
    field('Height (cm)', h('input', { name: 'height_cm', type: 'number', step: '1', min: 100, max: 250, required: '', value: p.height_cm ?? '' })),
    field('Birth date', h('input', { name: 'birth_date', type: 'date', required: '', value: p.birth_date ?? '' })),
    field('Sex', h('select', { name: 'sex', required: '' }, ['male', 'female'].map((s) => h('option', { value: s, selected: p.sex === s ? '' : null }, cap(s))))),
    field('Goal', h('select', { name: 'goal' }, [['maintain', 'Maintain weight'], ['lose', 'Lose weight gently'], ['gain', 'Gain weight']].map(([v, l]) => h('option', { value: v, selected: (p.goal || 'maintain') === v ? '' : null }, l)))),
    field('Protein (g/kg)', h('input', { name: 'protein_g_per_kg', type: 'number', step: '0.1', min: 1.2, max: 2.4, value: p.protein_g_per_kg ?? 1.8 })),
    h('label', { class: 'check' }, h('input', { name: 'follow_provider', type: 'checkbox', checked: p.follow_provider === false ? null : '' }), h('span', {}, 'Keep in sync with my watch')),
    h('button', { class: 'primary', type: 'submit' }, 'Save'));
    return [f, err];
  }

  function todayCard(t) {
    const e = t.eaten || { kcal: 0, protein_g: 0, carbs_g: 0, fat_g: 0 };
    return h('div', { class: 'card nutri-today' },
      h('div', { class: 'row', style: 'justify-content:space-between;margin:0' }, h('div', { class: 'label', style: 'margin:0' }, `Today · ${fmtDay(t.day)}`), loadPill(t.load)),
      h('div', { class: 'big', style: 'margin:6px 0 2px' }, `${n(t.kcal)}`, h('small', {}, ' kcal target')),
      h('p', { class: 'note', style: 'margin:0 0 12px' }, `${n(e.kcal)} eaten · ${n(Math.max(0, t.kcal - e.kcal))} to go · includes ~${n(t.training_kcal)} kcal of training`),
      h('div', { class: 'contribs' },
        macroBar('Carbs', e.carbs_g, t.carbs_g, 'g', 'var(--c2)'),
        macroBar('Protein', e.protein_g, t.protein_g, 'g', 'var(--c3)'),
        macroBar('Fat', e.fat_g, t.fat_g, 'g', 'var(--c5)')),
      h('p', { class: 'note', style: 'margin:12px 0 0' }, `Fluids: about ${t.fluid_l} L. ${t.reasons.join('. ')}.`),
      t.fuelling.length ? h('ul', { class: 'fuel' }, t.fuelling.map((x) => h('li', {}, x))) : null);
  }

  function dayCard(t, today) {
    const e = t.eaten;
    return h('div', { class: `session nutri-day${t.day === today ? ' today' : ''}`, style: `border-top-color:${LOAD[t.load] || 'var(--grey)'}` },
      h('div', { class: 'day' }, h('span', {}, fmtDay(t.day)), loadPill(t.load)),
      h('div', { class: 'km' }, `${n(t.kcal)}`, h('small', { class: 'muted' }, ' kcal')),
      h('div', { class: 'macros-line' }, h('span', {}, h('b', {}, t.carbs_g), ' C'), h('span', {}, h('b', {}, t.protein_g), ' P'), h('span', {}, h('b', {}, t.fat_g), ' F'), h('span', { class: 'muted' }, `${t.fluid_l} L`)),
      h('div', { class: 'detail' }, t.sessions.length ? t.sessions.map((s) => `${s.name}${s.km ? ` ${s.km} km` : ''}${s.planned ? '' : ' ✓'}`).join(' · ') : 'Rest'),
      t.fuelling[0] ? h('div', { class: 'detail' }, t.fuelling[0]) : null,
      e ? h('div', { class: 'detail' }, `Logged ${n(e.kcal)} kcal · ${e.carbs_g} C · ${e.protein_g} P · ${e.fat_g} F`) : null);
  }

  function foodList(entries, refresh) {
    if (!entries.length) return h('p', { class: 'note' }, 'Nothing logged today yet.');
    return MEALS.filter((m) => entries.some((x) => x.meal === m)).map((m) => h('div', { class: 'meal' },
      h('div', { class: 'label' }, cap(m)),
      entries.filter((x) => x.meal === m).map((x) => h('div', { class: 'food-row' },
        h('div', {}, h('div', {}, x.name, x.quantity ? h('span', { class: 'muted' }, ` · ${x.quantity}`) : null, x.source === 'ai' ? h('span', { class: 'pill adj', style: 'margin-left:6px' }, 'AI estimate') : null),
          h('div', { class: 'note', style: 'margin:0' }, `${n(x.kcal)} kcal · ${x.carbs_g} g carbs · ${x.protein_g} g protein · ${x.fat_g} g fat`)),
        h('button', { class: 'link', title: 'Remove', onclick: async () => { await api(`/nutrition/food/${x.id}`, { method: 'DELETE' }); resetCache(); refresh(); } }, 'Remove')))));
  }

  function chatCard(llm, day, refresh) {
    const log = h('div', { class: 'chat-log' });
    const proposalCard = (p) => {
      const btn = h('button', { class: 'primary', onclick: async () => {
        btn.disabled = true;
        try {
          await api('/nutrition/food', { method: 'POST', body: JSON.stringify({ day: p.day, meal: p.meal, items: p.items, source: 'ai' }) });
          p.saved = true; resetCache(); refresh();
        } catch (e) { btn.disabled = false; btn.textContent = `Failed: ${e.message}`; }
      } }, p.saved ? 'Added ✓' : 'Add to log');
      if (p.saved) btn.disabled = true;
      return h('div', { class: 'proposal' },
        h('div', {}, h('div', { class: 'next-name' }, `${cap(p.meal)} · ${p.total.kcal} kcal`),
          h('div', { class: 'note', style: 'margin:2px 0' }, `${p.total.carbs_g} g carbs · ${p.total.protein_g} g protein · ${p.total.fat_g} g fat${p.day !== day ? ` · ${fmtDay(p.day)}` : ''}`),
          h('ul', { class: 'fuel' }, p.items.map((i) => h('li', {}, `${i.name}${i.quantity ? ` (${i.quantity})` : ''}: ${n(i.kcal)} kcal`))),
          p.assumptions ? h('div', { class: 'note', style: 'margin:0' }, `Assumed: ${p.assumptions}`) : null),
        btn);
    };
    const render = () => {
      log.replaceChildren(...state.chat.map((m) => h('div', { class: `msg ${m.role}` }, h('div', { class: 'bubble' }, m.content), ...(m.proposals || []).map(proposalCard))));
      log.scrollTop = log.scrollHeight;
    };
    const input = h('input', { placeholder: llm.enabled ? 'e.g. “porridge with a banana and a coffee with milk”' : 'Food chat needs the local AI', disabled: llm.enabled ? null : '' });
    const send = h('button', { class: 'primary', type: 'submit', disabled: llm.enabled ? null : '' }, 'Send');
    const form = h('form', { class: 'chat-form', onsubmit: async (e) => {
      e.preventDefault();
      const text = input.value.trim(); if (!text) return;
      state.chat.push({ role: 'user', content: text }); input.value = ''; send.disabled = true;
      state.chat.push({ role: 'assistant', content: 'Estimating… (a local model can take a minute)', pending: true }); render();
      try {
        const out = await api('/nutrition/chat', { method: 'POST', body: JSON.stringify({ day, messages: state.chat.filter((m) => !m.pending).slice(-10).map(({ role, content }) => ({ role, content })) }) });
        state.chat.pop(); state.chat.push({ role: 'assistant', content: out.reply, proposals: out.proposals });
      } catch (err) { state.chat.pop(); state.chat.push({ role: 'assistant', content: `Sorry — ${err.message}` }); }
      send.disabled = false; render(); input.focus();
    } }, input, send);
    if (!state.chat.length) state.chat.push({ role: 'assistant', content: llm.enabled
      ? 'Tell me what you ate and I’ll estimate it. You confirm before anything is logged. You can also ask what to eat with what’s left today.'
      : 'The food chat needs the local AI (HC_LLM_PROVIDER, see the README). You can still add food by hand below.' });
    render();
    return [log, form];
  }

  function manualForm(day, refresh) {
    const err = h('p', { class: 'note error', role: 'status' });
    const num = (name, label, step = '1') => h('label', {}, h('span', {}, label), h('input', { name, type: 'number', min: 0, step, required: '' }));
    const f = h('form', { class: 'goal-form', onsubmit: async (e) => {
      e.preventDefault(); err.textContent = '';
      const d = Object.fromEntries(new FormData(f));
      try {
        await api('/nutrition/food', { method: 'POST', body: JSON.stringify({ day, meal: d.meal, source: 'manual', items: [{ name: d.name, quantity: d.quantity || null,
          kcal: Number(d.kcal), protein_g: Number(d.protein_g), carbs_g: Number(d.carbs_g), fat_g: Number(d.fat_g) }] }) });
        resetCache(); refresh();
      } catch (e2) { err.textContent = e2.message; }
    } },
    h('label', {}, h('span', {}, 'Meal'), h('select', { name: 'meal' }, MEALS.map((m) => h('option', { value: m }, cap(m))))),
    h('label', {}, h('span', {}, 'Food'), h('input', { name: 'name', required: '', maxlength: 120, placeholder: 'e.g. Chicken wrap' })),
    h('label', {}, h('span', {}, 'Amount'), h('input', { name: 'quantity', maxlength: 60, placeholder: 'optional' })),
    num('kcal', 'kcal'), num('carbs_g', 'Carbs g', '0.1'), num('protein_g', 'Protein g', '0.1'), num('fat_g', 'Fat g', '0.1'),
    h('button', { class: 'primary', type: 'submit' }, 'Add'));
    return h('details', { class: 'manual' }, h('summary', { class: 'note' }, 'Add food by hand'), f, err);
  }

  async function nutrition() {
    const [o, llm] = await Promise.all([api('/nutrition'), api('/assistant').catch(() => ({ enabled: false }))]);
    const p = o.profile;
    const intro = h('p', { class: 'lede' }, 'Daily targets from your training: carbs follow the load, protein stays steady, fat fills the rest. Estimates for healthy adults in training, not medical advice.');
    if (p.missing.length) {
      return show(h('h1', {}, 'Nutrition'), intro,
        card('Your profile', 'Needed for energy and g/kg targets. With the official COROS connection this fills in from your watch on the next sync.', profileForm(p, nutrition)));
    }
    const today = o.today;
    state.day ??= today;
    const t = o.days.find((x) => x.day === today);
    const entries = await api(`/nutrition/food?day=${today}`);
    const weeks = [o.days.slice(0, 7), o.days.slice(7, 14)];
    const seg = h('div', { class: 'seg' }, ['This week', 'Next week'].map((label, i) => h('button', { class: state.week === i ? 'on' : null, onclick: () => { state.week = i; nutrition(); } }, label)));
    const avg = (key) => Math.round(weeks[state.week].reduce((s, d) => s + d[key], 0) / 7);
    const profileLine = h('p', { class: 'note' },
      `${p.weight_kg} kg · ${p.height_cm} cm · ${cap(p.sex)} · goal: ${p.goal} · protein ${p.protein_g_per_kg} g/kg · ${p.source === 'manual' ? 'entered by hand' : 'from your watch'} `,
      h('button', { class: 'link', onclick: (e) => { e.currentTarget.replaceWith(...profileForm(p, nutrition)); } }, 'Edit'));
    show(h('h1', {}, 'Nutrition'), intro,
      h('div', { class: 'hero' }, todayCard(t),
        card('What I ate today', 'Tell the chat or add by hand. AI amounts are estimates: check the card before adding.', [foodList(entries, nutrition), chatCard(llm, today, nutrition), manualForm(today, nutrition)].flat(2))),
      h('h2', {}, 'Day by day'),
      h('div', { class: 'row' }, seg, h('span', { class: 'note', style: 'margin:0' }, `Average ${avg('kcal').toLocaleString()} kcal · ${avg('carbs_g')} g carbs · ${avg('protein_g')} g protein · ${avg('fat_g')} g fat a day`)),
      h('div', { class: 'sessions' }, weeks[state.week].map((d) => dayCard(d, today))),
      h('h2', {}, 'Profile'), profileLine);
  }
  return { nutrition };
}
