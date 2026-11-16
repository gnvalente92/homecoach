// Claude hand-off: describe something, copy the prompt into Claude, paste its answer back, check it, then add.
// The app never calls a model; the server writes the prompt and validates the pasted JSON.
export function claudeHandoff({ h, api, promptPath, importPath, extra = {}, placeholder, renderProposal }) {
  const ask = h('textarea', { rows: 2, placeholder, class: 'handoff-input' });
  const prompt = h('textarea', { rows: 8, readonly: '', class: 'handoff-prompt' });
  const promptBox = h('details', { class: 'handoff-step', style: 'display:none' }, h('summary', { class: 'note' }, 'Show the prompt'), prompt);
  const copyNote = h('span', { class: 'note', role: 'status', style: 'margin:0' });
  const copy = h('button', { class: 'primary', type: 'button', onclick: async () => {
    copyNote.textContent = '';
    try {
      const qs = new URLSearchParams({ request: ask.value.trim(), ...extra });
      prompt.value = (await api(`${promptPath}?${qs}`)).prompt;
      promptBox.style.display = '';
      try { await navigator.clipboard.writeText(prompt.value); }
      catch { prompt.select(); document.execCommand('copy'); } // plain http on the LAN has no clipboard API
      copyNote.textContent = 'Copied. Paste it into Claude, then paste Claude’s answer below.';
    } catch (e) { copyNote.textContent = `Couldn’t build the prompt: ${e.message}`; }
  } }, 'Copy prompt for Claude');
  const answer = h('textarea', { rows: 5, placeholder: 'Paste Claude’s whole answer here', class: 'handoff-input' });
  const err = h('p', { class: 'note error', role: 'status' });
  const results = h('div');
  const check = h('button', { class: 'primary', type: 'button', onclick: async () => {
    err.textContent = ''; results.replaceChildren();
    if (!answer.value.trim()) { err.textContent = 'Paste Claude’s answer first.'; return; }
    check.disabled = true;
    try {
      const out = await api(importPath, { method: 'POST', body: JSON.stringify({ text: answer.value, ...extra }) });
      results.replaceChildren(...out.proposals.map(renderProposal));
    } catch (e) { err.textContent = e.message; }
    check.disabled = false;
  } }, 'Check answer');
  return h('div', { class: 'handoff' },
    h('div', { class: 'label' }, '1 · Describe it and copy the prompt'), ask,
    h('div', { class: 'row', style: 'margin:8px 0 0' }, copy, copyNote), promptBox,
    h('div', { class: 'label', style: 'margin-top:14px' }, '2 · Paste Claude’s answer'), answer,
    h('div', { class: 'row', style: 'margin:8px 0 0' }, check), err, results);
}
