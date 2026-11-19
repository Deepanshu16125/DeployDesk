// Kestrel Help front end. Plain JS, no build step, so the customer's IT team can read it.
const $ = (sel, el = document) => el.querySelector(sel);
const $$ = (sel, el = document) => [...el.querySelectorAll(sel)];
const main = $('#main');

const state = {
  config: null,
  key: localStorage.getItem('dd_key') || '',
  conversationId: null,
  turns: [],          // [{question, answer, retrieved, citations, highlights, meta, messageId, rating}]
  activeTurn: null,
  activeRef: null,
  streaming: false,
  mode: 'hybrid',
};

const ICON = {
  send: '<svg viewBox="0 0 20 20"><path d="M10 16V4M5 9l5-5 5 5"/></svg>',
  up: '<svg viewBox="0 0 20 20"><path d="M6.5 9.5V16H4V9.5zM6.5 9.5l3-6c1.4 0 2.2.9 2 2.2l-.5 2.8h4.2a1.5 1.5 0 0 1 1.5 1.8l-1 4.6a1.5 1.5 0 0 1-1.5 1.1H6.5"/></svg>',
  down: '<svg viewBox="0 0 20 20"><path d="M6.5 10.5V4H4v6.5zM6.5 10.5l3 6c1.4 0 2.2-.9 2-2.2l-.5-2.8h4.2a1.5 1.5 0 0 0 1.5-1.8l-1-4.6A1.5 1.5 0 0 0 14.2 4H6.5"/></svg>',
  copy: '<svg viewBox="0 0 20 20"><rect x="6.5" y="6.5" width="9.5" height="9.5" rx="1.5"/><path d="M13.5 6.5V5A1.5 1.5 0 0 0 12 3.5H5A1.5 1.5 0 0 0 3.5 5v7A1.5 1.5 0 0 0 5 13.5h1.5"/></svg>',
  person: '<svg viewBox="0 0 20 20"><circle cx="10" cy="7" r="3"/><path d="M4 16.5c.8-3 3.2-4.5 6-4.5s5.2 1.5 6 4.5"/></svg>',
  close: '<svg viewBox="0 0 20 20"><path d="M5 5l10 10M15 5L5 15"/></svg>',
  book: '<svg viewBox="0 0 20 20"><path d="M4 4.5h4.5A1.5 1.5 0 0 1 10 6v10a1.5 1.5 0 0 0-1.5-1.5H4zM16 4.5h-4.5A1.5 1.5 0 0 0 10 6v10a1.5 1.5 0 0 1 1.5-1.5H16z"/></svg>',
  spark: '<svg viewBox="0 0 20 20"><path d="M10 3v4M10 13v4M3 10h4M13 10h4"/></svg>',
  arrow: '<svg viewBox="0 0 20 20"><path d="M5 10h10M11 6l4 4-4 4"/></svg>',
  upload: '<svg viewBox="0 0 20 20"><path d="M10 13V4M6 8l4-4 4 4M4 13v2.5h12V13"/></svg>',
  trash: '<svg viewBox="0 0 20 20"><path d="M4.5 6h11M8 6V4.5h4V6M6 6l.7 9.5h6.6L14 6"/></svg>',
  chat: '<svg viewBox="0 0 20 20"><path d="M3.5 5.5a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v6a2 2 0 0 1-2 2H9l-3.5 3v-3h0a2 2 0 0 1-2-2z"/></svg>',
  ticket: '<svg viewBox="0 0 20 20"><path d="M3.5 7V5.5h13V7a2 2 0 0 0 0 4v2.5h-13V11a2 2 0 0 0 0-4z"/></svg>',
  doc: '<svg viewBox="0 0 20 20"><path d="M5 3.5h7l3 3v10H5z"/><path d="M12 3.5v3h3"/></svg>',
  search: '<svg viewBox="0 0 20 20"><circle cx="9" cy="9" r="5"/><path d="M13 13l3.5 3.5"/></svg>',
  warn: '<svg viewBox="0 0 20 20"><path d="M10 4l7 12H3z"/><path d="M10 9v3M10 14.2v.1"/></svg>',
};

// utils
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const fmtNum = (n) => (n == null ? '–' : Number(n).toLocaleString('en-IN'));
const fmtBytes = (b) => (b > 1e6 ? (b / 1e6).toFixed(1) + ' MB' : Math.max(1, Math.round(b / 1e3)) + ' KB');
function ago(ts) {
  const s = Date.now() / 1000 - ts;
  if (s < 60) return 'just now';
  if (s < 3600) return Math.floor(s / 60) + ' min ago';
  if (s < 86400) return Math.floor(s / 3600) + ' h ago';
  return new Date(ts * 1000).toLocaleDateString('en-IN', { day: 'numeric', month: 'short' });
}
const fileName = (src) => src.split('/').pop();
const kindOf = (src) => { const e = src.split('.').pop().toLowerCase(); return { htm: 'html', markdown: 'md' }[e] || e; };
const initials = (name) => (name || '?').split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase();

function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.innerHTML = msg;
  $('#toasts').appendChild(t);
  setTimeout(() => { t.style.transition = 'opacity .3s'; t.style.opacity = 0; setTimeout(() => t.remove(), 300); }, 3200);
}

async function api(path, opts = {}) {
  const headers = { 'X-API-Key': state.key, ...(opts.body && !(opts.body instanceof FormData) ? { 'Content-Type': 'application/json' } : {}), ...opts.headers };
  const r = await fetch(path, { ...opts, headers, body: opts.body && !(opts.body instanceof FormData) && typeof opts.body !== 'string' ? JSON.stringify(opts.body) : opts.body });
  if (r.status === 401) { askForKey(); throw new Error('unauthorised'); }
  const data = r.headers.get('content-type')?.includes('json') ? await r.json() : await r.text();
  if (!r.ok) throw new Error(typeof data === 'object' ? (typeof data.detail === 'string' ? data.detail : JSON.stringify(data.detail)) : data);
  return data;
}

// Tiny markdown: paragraphs, lists, `code`, **bold**, and [n] citations. Everything is escaped first.
function renderAnswer(text, turnIdx) {
  const inline = (s) => esc(s)
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/\[(\d+)\]/g, (_, n) => `<button class="cite" data-turn="${turnIdx}" data-ref="${n}" title="Show source ${n}">${n}</button>`);
  const blocks = text.replace(/```[\s\S]*?```/g, (m) => m.replace(/```\w*/g, '')).split(/\n{2,}/);
  return blocks.map((b) => {
    const lines = b.split('\n').filter((l) => l.trim());
    if (lines.length && lines.every((l) => /^\s*([-*]|\d+[.)])\s+/.test(l))) {
      const ordered = /^\s*\d/.test(lines[0]);
      const items = lines.map((l) => `<li>${inline(l.replace(/^\s*([-*]|\d+[.)])\s+/, ''))}</li>`).join('');
      return ordered ? `<ol>${items}</ol>` : `<ul>${items}</ul>`;
    }
    return `<p>${lines.map(inline).join('<br>')}</p>`;
  }).join('');
}

// shell
function setActiveNav(view) {
  $$('#nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === view));
}

async function refreshSidebar() {
  try {
    const [health, convs, tickets] = await Promise.all([
      fetch('/health').then((r) => r.json()),
      api('/api/conversations'),
      api('/api/tickets'),
    ]);
    const st = $('#status');
    st.className = 'status ' + (health.status === 'ok' ? 'ok' : 'warn');
    $('#statusModel').textContent = health.generator === 'ollama' ? `${health.model} · local` : health.generator;
    $('#statusIndex').textContent = `${fmtNum(health.chunks)} passages indexed`;
    const open = tickets.filter((t) => t.status !== 'resolved').length;
    const b = $('#ticketBadge');
    b.hidden = !open; b.textContent = open;
    const list = $('#recentList');
    list.innerHTML = convs.length
      ? convs.slice(0, 12).map((c) => `<a href="#/c/${c.id}" class="${c.id === state.conversationId ? 'active' : ''}" title="${esc(c.title)}">${esc(c.title)}</a>`).join('')
      : '<div class="empty-mini">Nothing yet.</div>';
  } catch (e) { /* sidebar is best-effort */ }
}

function askForKey() {
  if ($('.modal')) return;
  openModal(`
    <div class="modal-head"><h2>Workspace key</h2><p>This deployment needs an API key. Ask your Kestrel admin for one.</p></div>
    <div class="modal-body"><label class="field"><span>X-API-Key</span><input class="input mono" id="keyInput" placeholder="paste key"></label></div>
    <div class="modal-foot"><button class="btn primary" id="keySave">Continue</button></div>`);
  $('#keySave').onclick = () => { state.key = $('#keyInput').value.trim(); localStorage.setItem('dd_key', state.key); closeModal(); route(); };
}

function openModal(html, cls = 'modal') {
  closeModal();
  $('#modalBack').hidden = false;
  const m = document.createElement('div');
  m.className = cls;
  m.innerHTML = html;
  document.body.appendChild(m);
  $('#modalBack').onclick = closeModal;
  const first = m.querySelector('input, textarea, select');
  if (first) setTimeout(() => first.focus(), 50);
  return m;
}
function closeModal() {
  $$('.modal, .drawer').forEach((m) => m.remove());
  $('#modalBack').hidden = true;
}
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeModal();
  if (e.key === '/' && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) {
    e.preventDefault();
    if (!location.hash.startsWith('#/ask')) location.hash = '#/ask'; else $('#composer')?.focus();
  }
});
$('#newChat').onclick = () => {
  state.conversationId = null; state.turns = []; state.ticket = null;
  // let the hashchange handler render, otherwise it fires later and wipes a question typed in between
  if (location.hash !== '#/ask') location.hash = '#/ask'; else route(true);
};

// router
async function route(force) {
  const hash = location.hash || '#/ask';
  const [, view, arg] = hash.split('/');
  setActiveNav(view === 'c' ? 'ask' : view);
  closeModal();
  if (view === 'c' && arg) return viewAsk(arg.split('?')[0]);
  if (view === 'conversations') return viewConversations();
  if (view === 'tickets') return viewTickets(arg);
  if (view === 'kb') return viewKB();
  if (view === 'analytics') return viewAnalytics();
  if (view === 'settings') return viewSettings();
  return viewAsk(null, force);
}
window.addEventListener('hashchange', () => route());

// Ask
function viewAsk(conversationId, fresh) {
  if (conversationId === null && !fresh && state.turns.length && !state.conversationId) { /* keep draft thread */ }
  main.innerHTML = `
    <div class="view ask" id="askView">
      <div class="thread-wrap">
        <div class="thread-top" id="threadTop"></div>
        <div class="thread" id="thread"><div class="thread-inner" id="threadInner"></div></div>
        <div class="composer-wrap">
          <div class="composer">
            <textarea id="composer" rows="1" placeholder="Ask about deployments, settings, security policy…"></textarea>
            <div class="composer-bar">
              <div class="seg" id="modeSeg" title="Retrieval mode">
                ${['hybrid', 'vector', 'bm25'].map((m) => `<button data-m="${m}" class="${state.mode === m ? 'on' : ''}">${m}</button>`).join('')}
              </div>
              <span class="muted" style="font-size:12px;margin-left:6px">Answers only from your docs</span>
              <button class="send" id="sendBtn" disabled title="Send (Enter)">${ICON.send}</button>
            </div>
          </div>
          <div class="hint">Enter to send · Shift+Enter for a new line · <kbd>/</kbd> to focus</div>
        </div>
      </div>
      <aside class="panel" id="panel"></aside>
    </div>`;
  const ta = $('#composer');
  ta.addEventListener('input', () => { ta.style.height = 'auto'; ta.style.height = Math.min(180, ta.scrollHeight) + 'px'; $('#sendBtn').disabled = !ta.value.trim() || state.streaming; });
  ta.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } });
  $('#sendBtn').onclick = submit;
  $$('#modeSeg button').forEach((b) => b.onclick = () => { state.mode = b.dataset.m; $$('#modeSeg button').forEach((x) => x.classList.toggle('on', x === b)); });
  $('#thread').addEventListener('click', onThreadClick);

  if (conversationId) {
    loadConversation(conversationId);
  } else {
    if (fresh || state.conversationId) { state.conversationId = null; state.turns = []; state.ticket = null; }
    renderThread();
    const q = new URLSearchParams(location.hash.split('?')[1] || '').get('q');
    if (q) { ta.value = q; submit(); }
    else ta.focus();
  }
}

async function loadConversation(id) {
  $('#threadInner').innerHTML = '<div class="skel" style="width:60%;height:22px"></div><div class="skel"></div><div class="skel" style="width:90%"></div><div class="skel" style="width:75%"></div>';
  try {
    const conv = await api(`/api/conversations/${id}`);
    state.conversationId = id;
    state.turns = [];
    let pending = null;
    for (const m of conv.messages) {
      if (m.role === 'user') pending = { question: m.content };
      else if (pending) {
        state.turns.push({ ...pending, answer: m.content, retrieved: m.retrieved || [], citations: m.citations || [],
          meta: m.meta || {}, messageId: m.id, rating: m.rating, done: true, highlights: null });
        pending = null;
      }
    }
    if (pending) state.turns.push({ ...pending, answer: '', retrieved: [], citations: [], meta: {}, done: true });
    state.ticket = conv.tickets?.[0] || null;
    renderThread();
    // stored messages keep snippets only; fetch full passages lazily for the panel
    const last = state.turns.length - 1;
    if (last >= 0 && state.turns[last].citations.length) openPanel(last, state.turns[last].citations[0].ref);
    refreshSidebar();
  } catch (e) {
    $('#threadInner').innerHTML = `<div class="empty">${ICON.chat}<h3>Conversation not found</h3><p>${esc(e.message)}</p><a class="btn" href="#/ask">Start a new one</a></div>`;
  }
}

function renderTop() {
  const top = $('#threadTop');
  if (!top) return;
  if (!state.turns.length) { top.innerHTML = `<span class="title muted" style="font-family:var(--sans);font-size:13px">New conversation</span>`; return; }
  const t = state.ticket;
  top.innerHTML = `<span class="title">${esc(state.turns[0].question)}</span>
    ${t ? `<a class="pill amber" href="#/tickets/${t.id}" style="text-decoration:none"><span class="d"></span>Ticket #${t.id} · ${t.status.replace('_', ' ')}</a>` : ''}
    <span style="margin-left:auto"></span>
    <button class="btn sm ghost" id="escTop">${ICON.person}Talk to a person</button>`;
  $('#escTop').onclick = () => openEscalate(state.turns.length - 1);
}

function renderThread() {
  renderTop();
  const inner = $('#threadInner');
  if (!inner) return;
  if (!state.turns.length) {
    const c = state.config || {};
    inner.innerHTML = `
      <div class="hero">
        <div class="eyebrow">${esc(c.customer_name || '')} · Support assistant</div>
        <h1>What can I help you <em>find</em>?</h1>
        <p>${esc(c.greeting || '')}</p>
        <div class="suggest">${(c.suggested_questions || []).map((q) => `<button data-q="${esc(q)}">${ICON.arrow}<span>${esc(q)}</span></button>`).join('')}</div>
        <div class="hero-stats" id="heroStats"></div>
      </div>`;
    $$('.suggest button', inner).forEach((b) => b.onclick = () => { $('#composer').value = b.dataset.q; submit(); });
    fetch('/health').then((r) => r.json()).then((h) => {
      const hs = $('#heroStats');
      if (hs) hs.innerHTML = `<span><b>${fmtNum(h.chunks)}</b> passages</span><span>model <b>${esc(h.model || h.generator)}</b></span><span>runs <b>on-prem</b>, no data leaves the network</span>`;
    });
    closePanel();
    return;
  }
  inner.innerHTML = state.turns.map((t, i) => turnHTML(t, i)).join('');
  scrollThread();
}

function turnHTML(t, i) {
  const sources = t.retrieved?.length
    ? `<div class="label-row">${ICON.book}Sources</div>
       <div class="sources-row ${t._srcShown ? '' : 'enter'}">${t.retrieved.map((s, k) => `
         <button class="src-card ${state.activeTurn === i && state.activeRef === s.ref ? 'on' : ''}" data-turn="${i}" data-ref="${s.ref}" style="animation-delay:${k * 50}ms">
           <div class="n">[${s.ref}] ${esc(kindOf(s.source)).toUpperCase()}</div>
           <div class="f">${esc(fileName(s.source))}</div>
           <div class="l">${esc(s.location)}</div>
         </button>`).join('')}</div>`
    : (t.done ? '' : `<div class="stage"><span class="spinner"></span>Searching ${fmtNum(state.chunks || '')} passages…</div>`);
  let body = '';
  const notFound = t.done && /^I couldn.t find this/.test(t.answer || '');
  if (notFound) {
    body = `<div class="notfound"><div style="flex:1"><p>I couldn't find this in the knowledge base.</p>
      <small>Nothing in the indexed docs matched closely enough, so I didn't guess.</small></div>
      <button class="btn rust" data-escalate="${i}">${ICON.person}Ask a person</button></div>`;
  } else if (t.answer) {
    body = `<div class="answer">${renderAnswer(t.answer, i)}${t.done ? '' : '<span class="caret"></span>'}</div>`;
  } else if (t.retrieved?.length && !t.done) {
    body = `<div class="stage"><span class="spinner"></span>Writing an answer from ${t.retrieved.length} sources…</div>`;
  }
  const m = t.meta || {};
  const conf = m.confidence;
  const low = t.done && !notFound && conf === 'low';
  const foot = t.done && !notFound && t.answer ? `
    ${low ? `<div class="lowconf">${ICON.warn}<span>The closest passages were a weak match. Worth double-checking, or hand it to the team.</span><button class="btn sm" data-escalate="${i}">Escalate</button></div>` : ''}
    <div class="answer-foot">
      <button class="icon-btn ${t.rating === 1 ? 'on' : ''}" data-rate="1" data-turn="${i}" title="Helpful">${ICON.up}</button>
      <button class="icon-btn down ${t.rating === -1 ? 'on' : ''}" data-rate="-1" data-turn="${i}" title="Not helpful">${ICON.down}</button>
      <button class="icon-btn" data-copy="${i}" title="Copy answer">${ICON.copy}</button>
      <button class="btn sm ghost" data-escalate="${i}">${ICON.person}Escalate</button>
      <span class="meta">
        ${conf && conf !== 'unknown' ? `<span class="pill ${conf === 'high' ? 'teal' : conf === 'medium' ? '' : 'amber'}"><span class="d"></span>${conf} match</span>` : ''}
        ${m.generator ? `<span>${esc(m.generator.replace('ollama:', '').replace('-local', ''))}</span>` : ''}
        ${m.ttft_ms ? `<span>first token ${(m.ttft_ms / 1000).toFixed(1)}s</span>` : ''}
        ${m.tokens_per_s ? `<span>${Math.round(m.tokens_per_s)} tok/s</span>` : ''}
        ${m.total_ms ? `<span>${(m.total_ms / 1000).toFixed(1)}s</span>` : ''}
      </span>
    </div>
    <div id="reason-${i}"></div>` : '';
  const enter = t._shown ? '' : 'enter';
  t._shown = true;
  if (t.retrieved?.length) t._srcShown = true;
  return `<div class="msg ${enter}" data-i="${i}">
    <div class="user-q"><h2 class="q">${esc(t.question)}</h2></div>
    ${sources}${body}${foot}</div>`;
}

function updateTurn(i) {
  const el = $(`.msg[data-i="${i}"]`);
  if (!el) return renderThread();
  el.outerHTML = turnHTML(state.turns[i], i);
}

function scrollThread() {
  const th = $('#thread');
  if (th) th.scrollTop = th.scrollHeight;
}

async function submit() {
  const ta = $('#composer');
  const q = ta.value.trim();
  if (!q || state.streaming) return;
  ta.value = ''; ta.style.height = 'auto';
  $('#sendBtn').disabled = true;
  state.streaming = true;
  const turn = { question: q, answer: '', retrieved: [], citations: [], meta: {}, done: false };
  state.turns.push(turn);
  const i = state.turns.length - 1;
  renderThread();
  try {
    const r = await fetch('/api/ask/stream', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-API-Key': state.key },
      body: JSON.stringify({ question: q, conversation_id: state.conversationId, mode: state.mode }),
    });
    if (r.status === 401) { askForKey(); throw new Error('API key needed'); }
    if (!r.ok) throw new Error((await r.json()).detail || r.statusText);
    const reader = r.body.getReader();
    const dec = new TextDecoder();
    let buf = '';
    let pendingRender = false;
    const flush = () => {
      pendingRender = false;
      // while tokens stream, only touch the answer text so nothing above it re-renders or flickers
      const ans = $(`.msg[data-i="${i}"] .answer`);
      if (ans && turn.answer && !turn.done) ans.innerHTML = renderAnswer(turn.answer, i) + '<span class="caret"></span>';
      else updateTurn(i);
      scrollThread();
    };
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf('\n\n')) >= 0) {
        const raw = buf.slice(0, idx); buf = buf.slice(idx + 2);
        const ev = /^event: (.*)$/m.exec(raw)?.[1];
        const data = JSON.parse(/^data: (.*)$/m.exec(raw)?.[1] || '{}');
        if (ev === 'meta') {
          turn.retrieved = data.retrieved; turn.meta.confidence = data.confidence;
          if (!state.conversationId) { state.conversationId = data.conversation_id; history.replaceState(null, '', `#/c/${data.conversation_id}`); }
          flush();
          openPanel(i, null);
        } else if (ev === 'token') {
          turn.answer += data.t;
          if (!pendingRender) { pendingRender = true; requestAnimationFrame(flush); }
        } else if (ev === 'replace') {
          turn.answer = data.text;
        } else if (ev === 'done') {
          Object.assign(turn, { answer: data.answer, citations: data.citations, highlights: data.highlights, messageId: data.message_id, done: true });
          turn.meta = { ...data };
        }
      }
    }
    turn.done = true;
    updateTurn(i);
    if (turn.citations?.length) openPanel(i, turn.citations[0].ref); else openPanel(i, null);
    refreshSidebar();
  } catch (e) {
    turn.done = true;
    turn.answer = turn.answer || '';
    updateTurn(i);
    toast('Could not get an answer: ' + esc(e.message), true);
  } finally {
    state.streaming = false;
    const ta2 = $('#composer');
    if (ta2) { $('#sendBtn').disabled = !ta2.value.trim(); ta2.focus(); }
  }
}

function onThreadClick(e) {
  const cite = e.target.closest('.cite, .src-card');
  if (cite) return openPanel(+cite.dataset.turn, +cite.dataset.ref);
  const rate = e.target.closest('[data-rate]');
  if (rate) return rateTurn(+rate.dataset.turn, +rate.dataset.rate);
  const copy = e.target.closest('[data-copy]');
  if (copy) {
    const t = state.turns[+copy.dataset.copy];
    navigator.clipboard?.writeText(t.answer).then(() => toast('Answer copied'), () => toast('Copy blocked by the browser', true));
    return;
  }
  const escBtn = e.target.closest('[data-escalate]');
  if (escBtn) return openEscalate(+escBtn.dataset.escalate);
}

async function rateTurn(i, rating) {
  const t = state.turns[i];
  if (!t.messageId) return;
  const newRating = t.rating === rating ? 0 : rating;
  try {
    await api('/api/feedback', { method: 'POST', body: { message_id: t.messageId, rating: newRating } });
    t.rating = newRating;
    updateTurn(i);
    if (newRating === -1) {
      $(`#reason-${i}`).innerHTML = `<div class="reason"><input class="input" id="reasonIn-${i}" placeholder="What was wrong? (optional, helps us fix the docs)"><button class="btn sm" id="reasonBtn-${i}">Send</button></div>`;
      $(`#reasonIn-${i}`).focus();
      const send = async () => {
        const c = $(`#reasonIn-${i}`).value.trim();
        if (c) await api('/api/feedback', { method: 'POST', body: { message_id: t.messageId, rating: -1, comment: c } });
        $(`#reason-${i}`).innerHTML = '';
        toast('Thanks, logged for the docs team');
      };
      $(`#reasonBtn-${i}`).onclick = send;
      $(`#reasonIn-${i}`).onkeydown = (e) => { if (e.key === 'Enter') send(); };
    } else if (newRating === 1) toast('Marked as helpful');
  } catch (e) { toast(esc(e.message), true); }
}

// source panel
function closePanel() {
  $('#askView')?.classList.remove('with-panel');
  state.activeTurn = null; state.activeRef = null;
}

async function openPanel(i, ref) {
  const t = state.turns[i];
  if (!t || !t.retrieved?.length) return;
  state.activeTurn = i; state.activeRef = ref;
  $('#askView').classList.add('with-panel');
  // stored conversations only keep snippets, pull the full passage text once
  if (t.retrieved.some((s) => !s.text) && !t._loading) {
    t._loading = true;
    try {
      const bySource = {};
      for (const s of t.retrieved) {
        if (!bySource[s.source]) bySource[s.source] = await api(`/api/documents/chunks?source=${encodeURIComponent(s.source)}`).catch(() => []);
        const hit = bySource[s.source].find((c) => c.id === s.chunk_id);
        s.text = hit ? hit.text : (s.snippet || '');
      }
    } finally { t._loading = false; }
  }
  const usedRefs = new Set((t.citations || []).map((c) => c.ref));
  const hl = t.highlights || computeHighlights(t);
  $('#panel').innerHTML = `
    <div class="panel-head"><h3>Sources</h3><span class="muted" style="font-size:12px">${usedRefs.size} cited of ${t.retrieved.length} retrieved</span>
      <button class="icon-btn" style="margin-left:auto" id="panelClose" title="Close">${ICON.close}</button></div>
    <div class="panel-body">${t.retrieved.map((s) => {
      const text = s.text || s.snippet || '';
      return `<div class="p-src ${s.ref === ref ? 'on' : ''}" id="psrc-${s.ref}">
        <div class="p-src-head" data-ref="${s.ref}">
          <span class="ftype ${esc(kindOf(s.source))}">${esc(kindOf(s.source))}</span>
          <div style="min-width:0"><div class="f">${esc(fileName(s.source))}</div><div class="l">${esc(s.location)}</div></div>
          <span class="n">[${s.ref}]</span>
        </div>
        <div class="p-src-text">${markSpans(text, hl[s.ref] || [])}</div>
        <div class="p-src-foot">${usedRefs.has(s.ref) ? '<span class="pill rust">cited</span>' : '<span class="pill">retrieved</span>'}
          <span>${esc(s.source)}</span></div>
      </div>`;
    }).join('')}</div>`;
  $('#panelClose').onclick = closePanel;
  $$('.p-src-head', $('#panel')).forEach((h) => h.onclick = () => openPanel(i, +h.dataset.ref === state.activeRef ? null : +h.dataset.ref));
  $$('.cite, .src-card').forEach((c) => c.classList.toggle('on', +c.dataset.turn === i && +c.dataset.ref === ref));
  if (ref) {
    const el = $(`#psrc-${ref}`);
    el?.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
    el?.querySelector('mark')?.scrollIntoView({ block: 'center', behavior: 'smooth' });
  }
}

function computeHighlights(t) {
  // fallback for conversations loaded from history: match answer sentences to passage sentences by word overlap
  const out = {};
  const words = (s) => new Set(s.toLowerCase().match(/[a-z0-9_]{3,}/g) || []);
  for (const sent of (t.answer || '').split(/(?<=[.!?])\s+|\n+/)) {
    const refs = [...sent.matchAll(/\[(\d+)\]/g)].map((m) => +m[1]);
    const w = words(sent.replace(/\[\d+\]/g, ''));
    if (w.size < 2) continue;
    for (const ref of refs) {
      const s = t.retrieved.find((x) => x.ref === ref);
      if (!s?.text) continue;
      let best = null, bestScore = 0;
      for (const m of s.text.matchAll(/[^.!?\n]+(?:[.!?]+|\n|$)/g)) {
        const ws = words(m[0]);
        if (!ws.size) continue;
        const inter = [...w].filter((x) => ws.has(x)).length;
        const score = inter / Math.sqrt(new Set([...w, ...ws]).size);
        if (score > bestScore) { bestScore = score; best = [m.index, m.index + m[0].length]; }
      }
      if (best && bestScore >= 0.6) (out[ref] ||= []).push(best);
    }
  }
  return out;
}

function markSpans(text, spans) {
  if (!spans.length) return esc(text);
  const sorted = [...spans].sort((a, b) => a[0] - b[0]);
  let out = '', pos = 0;
  for (const [a, b] of sorted) {
    if (a < pos) continue;
    out += esc(text.slice(pos, a)) + '<mark>' + esc(text.slice(a, b)) + '</mark>';
    pos = b;
  }
  return out + esc(text.slice(pos));
}

// escalation
function openEscalate(i) {
  const t = state.turns[i];
  if (!t) return;
  openModal(`
    <div class="modal-head"><h2>Hand this to a person</h2><p>Creates a ticket for the support team with the question, the bot's answer and its sources attached.</p></div>
    <div class="modal-body">
      <div class="quote">${esc(t.question)}</div>
      <label class="field"><span>Subject</span><input class="input" id="tkSubject" value="${esc(t.question.slice(0, 120))}"></label>
      <div class="row2">
        <label class="field"><span>Your email</span><input class="input" id="tkEmail" placeholder="you@company.com" type="email"></label>
        <label class="field"><span>Priority</span><select class="input" id="tkPrio"><option value="low">Low</option><option value="normal" selected>Normal</option><option value="high">High</option><option value="urgent">Urgent</option></select></label>
      </div>
      <label class="field"><span>Anything else the team should know?</span><textarea class="input" id="tkDetails" rows="3" placeholder="e.g. we're on the Pro plan, region ap-south-1"></textarea></label>
    </div>
    <div class="modal-foot"><button class="btn ghost" id="tkCancel">Cancel</button><button class="btn rust" id="tkCreate">${ICON.ticket}Create ticket</button></div>`);
  $('#tkCancel').onclick = closeModal;
  $('#tkCreate').onclick = async () => {
    const subject = $('#tkSubject').value.trim();
    if (subject.length < 3) { $('#tkSubject').focus(); return toast('Add a short subject', true); }
    $('#tkCreate').disabled = true;
    try {
      const tk = await api('/api/tickets', { method: 'POST', body: { conversation_id: state.conversationId, message_id: t.messageId,
        subject, requester: $('#tkEmail').value.trim(), priority: $('#tkPrio').value, details: $('#tkDetails').value.trim() } });
      closeModal();
      state.ticket = tk;
      renderTop();
      toast(`Ticket #${tk.id} created · <a href="#/tickets/${tk.id}">open</a>`);
      refreshSidebar();
    } catch (e) { $('#tkCreate').disabled = false; toast(esc(e.message), true); }
  };
}

// Conversations
async function viewConversations() {
  main.innerHTML = `<div class="view"><div class="page">
    <div class="page-head"><div><h1>Conversations</h1><p>Everything people have asked, with how it went.</p></div>
      <div class="actions"><a class="btn primary" href="#/ask" onclick="state.conversationId=null;state.turns=[]">${ICON.chat}New question</a></div></div>
    <div class="toolbar"><input class="input" id="convSearch" placeholder="Search questions…"></div>
    <div class="card" id="convTable"><div style="padding:16px"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div></div>
  </div></div>`;
  let timer;
  const load = async (q = '') => {
    const rows = await api('/api/conversations?q=' + encodeURIComponent(q));
    const el = $('#convTable');
    if (!el) return;
    if (!rows.length) {
      el.innerHTML = `<div class="empty">${ICON.chat}<h3>${q ? 'No matches' : 'No conversations yet'}</h3><p>${q ? 'Try a different word.' : 'Questions show up here once someone asks one.'}</p>${q ? '' : '<a class="btn" href="#/ask">Ask the first one</a>'}</div>`;
      return;
    }
    el.innerHTML = `<table class="t"><thead><tr><th>Question</th><th>Messages</th><th>Feedback</th><th>Ticket</th><th>Last activity</th><th></th></tr></thead><tbody>
      ${rows.map((c) => `<tr class="click" data-id="${c.id}">
        <td class="conv-title">${esc(c.title)}</td>
        <td class="mono muted">${c.n_messages}</td>
        <td>${c.worst_rating === -1 ? '<span class="pill red">not helpful</span>' : c.best_rating === 1 ? '<span class="pill teal">helpful</span>' : '<span class="muted">–</span>'}</td>
        <td>${c.ticket_id ? `<span class="pill amber">#${c.ticket_id}</span>` : '<span class="muted">–</span>'}</td>
        <td class="muted">${ago(c.updated_at)}</td>
        <td style="text-align:right"><button class="icon-btn" data-del="${c.id}" title="Delete">${ICON.trash}</button></td>
      </tr>`).join('')}</tbody></table>`;
    $$('tr.click', el).forEach((tr) => tr.onclick = (e) => {
      if (e.target.closest('[data-del]')) return;
      location.hash = '#/c/' + tr.dataset.id;
    });
    $$('[data-del]', el).forEach((b) => b.onclick = async () => {
      if (!confirm('Delete this conversation?')) return;
      await api('/api/conversations/' + b.dataset.del, { method: 'DELETE' });
      toast('Conversation deleted'); load($('#convSearch').value); refreshSidebar();
    });
  };
  $('#convSearch').oninput = (e) => { clearTimeout(timer); timer = setTimeout(() => load(e.target.value), 200); };
  load().catch((e) => toast(esc(e.message), true));
}

// Tickets
const STATUSES = [['open', 'Open'], ['in_progress', 'In progress'], ['waiting', 'Waiting on customer'], ['resolved', 'Resolved']];
const PRIO = { low: '', normal: '', high: 'amber', urgent: 'red' };

async function viewTickets(openId) {
  main.innerHTML = `<div class="view"><div class="page" style="max-width:1280px">
    <div class="page-head"><div><h1>Tickets</h1><p>Questions the assistant handed to a person. Each one carries the bot's answer and sources.</p></div></div>
    <div class="board" id="board">${STATUSES.map(([k, l]) => `<div class="col"><div class="col-head">${l}<span class="count">…</span></div><div class="skel"></div><div class="skel"></div></div>`).join('')}</div>
  </div></div>`;
  const tickets = await api('/api/tickets');
  const board = $('#board');
  board.innerHTML = STATUSES.map(([k, l]) => {
    const list = tickets.filter((t) => t.status === k);
    return `<div class="col"><div class="col-head">${l}<span class="count">${list.length}</span></div>
      ${list.length ? list.map((t, n) => `<div class="tcard" data-id="${t.id}" style="animation-delay:${n * 30}ms">
        <div class="id">#${t.id} · ${ago(t.created_at)}</div>
        <div class="s">${esc(t.subject)}</div>
        <div class="m">${t.priority !== 'normal' ? `<span class="pill ${PRIO[t.priority]}">${t.priority}</span>` : ''}
          <span style="flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(t.requester || 'no email')}</span>
          ${t.assignee ? `<span class="avatar" title="${esc(t.assignee)}">${initials(t.assignee)}</span>` : ''}</div>
      </div>`).join('') : `<div class="col-empty">${k === 'open' ? 'No open tickets. The assistant is coping.' : 'Nothing here'}</div>`}
    </div>`;
  }).join('');
  $$('.tcard', board).forEach((c) => c.onclick = () => openTicket(+c.dataset.id));
  if (openId) openTicket(+openId);
}

async function openTicket(id) {
  const t = await api('/api/tickets/' + id);
  $('#modalBack').hidden = false;
  $('#modalBack').onclick = closeModal;
  $$('.drawer').forEach((d) => d.remove());
  const d = document.createElement('div');
  d.className = 'drawer';
  const events = [{ text: `Escalated from the assistant${t.requester ? ' by ' + esc(t.requester) : ''}`, at: t.created_at, sys: true }, ...t.notes];
  d.innerHTML = `
    <div class="drawer-head"><span class="mono muted">#${t.id}</span><h2>${esc(t.subject)}</h2><button class="icon-btn" id="dClose">${ICON.close}</button></div>
    <div class="drawer-body">
      <div class="kv">
        <span>Status</span><select class="input" id="tStatus">${STATUSES.map(([k, l]) => `<option value="${k}" ${k === t.status ? 'selected' : ''}>${l}</option>`).join('')}</select>
        <span>Priority</span><select class="input" id="tPrio">${['low', 'normal', 'high', 'urgent'].map((p) => `<option ${p === t.priority ? 'selected' : ''}>${p}</option>`).join('')}</select>
        <span>Assignee</span><input class="input" id="tAssignee" value="${esc(t.assignee || '')}" placeholder="unassigned">
        <span>Requester</span><span>${esc(t.requester || '–')}</span>
      </div>
      <h4 class="sec">Customer question</h4><div class="quote">${esc(t.question || t.subject)}</div>
      ${t.bot_answer ? `<h4 class="sec">What the assistant said</h4><div class="quote" style="font-size:13.5px">${esc(t.bot_answer)}</div>` : ''}
      ${t.conversation_id ? `<a class="btn sm" href="#/c/${t.conversation_id}">${ICON.chat}Open conversation</a>` : ''}
      <h4 class="sec">Activity</h4>
      <div class="timeline">${events.map((e) => `<div class="ev ${e.sys ? '' : 'note'}"><div class="when">${new Date(e.at * 1000).toLocaleString('en-IN', { dateStyle: 'medium', timeStyle: 'short' })}</div>${esc(e.text)}</div>`).join('')}</div>
      <label class="field" style="margin-top:12px"><span>Add an internal note</span><textarea class="input" id="tNote" rows="2" placeholder="e.g. doc page is outdated, raised with docs team"></textarea></label>
      <div style="display:flex;gap:8px"><button class="btn primary" id="tSave">Save changes</button>
        ${t.status !== 'resolved' ? '<button class="btn" id="tResolve">Mark resolved</button>' : ''}</div>
    </div>`;
  document.body.appendChild(d);
  $('#dClose').onclick = () => { closeModal(); if (location.hash.startsWith('#/tickets/')) history.replaceState(null, '', '#/tickets'); };
  const save = async (extra = {}) => {
    try {
      await api('/api/tickets/' + id, { method: 'PATCH', body: { status: $('#tStatus').value, priority: $('#tPrio').value,
        assignee: $('#tAssignee').value.trim(), note: $('#tNote').value.trim() || null, ...extra } });
      toast(`Ticket #${id} updated`);
      closeModal();
      if (location.hash.startsWith('#/tickets')) viewTickets();
      refreshSidebar();
    } catch (e) { toast(esc(e.message), true); }
  };
  $('#tSave').onclick = () => save();
  if ($('#tResolve')) $('#tResolve').onclick = () => save({ status: 'resolved' });
}

// Knowledge base
async function viewKB() {
  main.innerHTML = `<div class="view"><div class="page">
    <div class="page-head"><div><h1>Knowledge base</h1><p>The documents the assistant answers from. Upload new ones and they're searchable in seconds.</p></div>
      <div class="actions"><button class="btn" id="reindex">Rebuild index</button></div></div>
    <div class="kb-stats" id="kbStats"></div>
    <div class="drop" id="drop">
      <div class="ic">${ICON.upload}</div>
      <div><h3>Drop PDFs, Markdown or HTML here</h3><p>Files are parsed, chunked and embedded on this server. Nothing is sent to a third party.</p></div>
      <label class="btn primary">Browse files<input type="file" id="fileIn" multiple accept=".pdf,.md,.markdown,.html,.htm,.txt" hidden></label>
    </div>
    <div class="uploads" id="uploads"></div>
    <div class="toolbar"><input class="input" id="docSearch" placeholder="Filter documents…">
      <div class="tabs" id="kindTabs">${['all', 'pdf', 'md', 'html', 'txt'].map((k) => `<button data-k="${k}" class="${k === 'all' ? 'on' : ''}">${k === 'all' ? 'All' : k.toUpperCase()}</button>`).join('')}</div></div>
    <div class="card" id="docTable"><div style="padding:16px"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div></div>
  </div></div>`;
  let docs = [];
  let kind = 'all';
  const renderTable = () => {
    const q = $('#docSearch').value.toLowerCase();
    const rows = docs.filter((d) => (kind === 'all' || d.kind === kind) && d.source.toLowerCase().includes(q));
    const el = $('#docTable');
    if (!docs.length) { el.innerHTML = `<div class="empty">${ICON.doc}<h3>No documents yet</h3><p>Upload the customer's docs to get started.</p></div>`; return; }
    if (!rows.length) { el.innerHTML = `<div class="empty">${ICON.search}<h3>No documents match</h3><p>Try another filter.</p></div>`; return; }
    el.innerHTML = `<table class="t"><thead><tr><th>Document</th><th>Type</th><th>Passages</th><th>Size</th><th>Status</th><th>Indexed</th><th></th></tr></thead><tbody>
      ${rows.map((d) => `<tr class="click" data-src="${esc(d.source)}">
        <td><div style="display:flex;gap:10px;align-items:center"><span class="ftype ${esc(d.kind)}" style="width:26px;height:30px">${esc(d.kind)}</span>
          <div style="min-width:0"><div class="conv-title">${esc(fileName(d.source))}</div><div class="muted mono" style="font-size:11px">${esc(d.source)}</div></div></div></td>
        <td class="mono muted">${esc(d.kind)}</td>
        <td class="mono">${fmtNum(d.chunks)}</td>
        <td class="mono muted">${fmtBytes(d.bytes)}</td>
        <td>${d.status === 'indexed' ? '<span class="pill teal"><span class="d"></span>indexed</span>' : `<span class="pill red" title="${esc(d.error)}"><span class="d"></span>${esc(d.status)}</span>`}</td>
        <td class="muted">${ago(d.ingested_at)}</td>
        <td style="text-align:right"><button class="icon-btn" data-del="${esc(d.source)}" title="Remove from index">${ICON.trash}</button></td>
      </tr>`).join('')}</tbody></table>`;
    $$('tr.click', el).forEach((tr) => tr.onclick = (e) => { if (!e.target.closest('[data-del]')) openDoc(tr.dataset.src); });
    $$('[data-del]', el).forEach((b) => b.onclick = async () => {
      if (!confirm(`Remove ${b.dataset.del} from the knowledge base?`)) return;
      const r = await api('/api/documents?source=' + encodeURIComponent(b.dataset.del), { method: 'DELETE' });
      toast(`Removed ${esc(fileName(b.dataset.del))} (${r.chunks_removed} passages)`);
      load();
    });
  };
  const renderStats = () => {
    const by = (k) => docs.filter((d) => d.kind === k).length;
    const total = docs.reduce((a, d) => a + d.chunks, 0);
    $('#kbStats').innerHTML = [['Documents', fmtNum(docs.length), `${by('pdf')} PDF · ${by('md')} MD · ${by('html')} HTML`],
      ['Passages', fmtNum(total), '800 chars, 150 overlap'],
      ['Embedding model', 'MiniLM-L6', '384 dims, runs on CPU'],
      ['Last update', docs.length ? ago(Math.max(...docs.map((d) => d.ingested_at))) : '–', 'incremental, no full rebuild']]
      .map(([k, v, s]) => `<div class="card kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');
  };
  const load = async () => { docs = await api('/api/documents'); renderStats(); renderTable(); refreshSidebar(); };
  $('#docSearch').oninput = renderTable;
  $$('#kindTabs button').forEach((b) => b.onclick = () => { kind = b.dataset.k; $$('#kindTabs button').forEach((x) => x.classList.toggle('on', x === b)); renderTable(); });

  const upload = async (files) => {
    if (!files.length) return;
    const box = $('#uploads');
    for (const f of files) {
      const row = document.createElement('div');
      row.className = 'up-row';
      row.innerHTML = `<span class="ftype ${esc(kindOf(f.name))}" style="width:24px;height:28px">${esc(kindOf(f.name))}</span><span style="flex:1">${esc(f.name)}</span><span class="bar"><i></i></span><span class="muted mono" style="font-size:11.5px">embedding…</span>`;
      box.prepend(row);
      const fd = new FormData();
      fd.append('files', f);
      try {
        const r = await api('/api/documents', { method: 'POST', body: fd });
        const res = r.results[0];
        row.querySelector('.bar').remove();
        row.lastElementChild.outerHTML = res.status === 'indexed'
          ? `<span class="pill teal"><span class="d"></span>${res.chunks} passages · ${res.seconds}s</span>`
          : `<span class="pill red">${esc(res.error || res.status)}</span>`;
      } catch (e) {
        row.querySelector('.bar')?.remove();
        row.lastElementChild.outerHTML = `<span class="pill red">${esc(e.message)}</span>`;
      }
    }
    load();
  };
  $('#fileIn').onchange = (e) => upload([...e.target.files]);
  const drop = $('#drop');
  ['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
  ['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
  drop.addEventListener('drop', (e) => upload([...e.dataTransfer.files]));
  $('#reindex').onclick = async () => {
    if (!confirm('Re-read every file and rebuild the whole index? Takes about 20 seconds for the demo set.')) return;
    const b = $('#reindex'); b.disabled = true; b.innerHTML = '<span class="spinner"></span>Rebuilding…';
    try { const r = await api('/ingest', { method: 'POST', body: {} }); toast(`Rebuilt: ${r.documents} docs, ${fmtNum(r.chunks)} passages in ${r.seconds}s`); load(); }
    catch (e) { toast(esc(e.message), true); }
    finally { b.disabled = false; b.textContent = 'Rebuild index'; }
  };
  load().catch((e) => toast(esc(e.message), true));
}

async function openDoc(source) {
  $('#modalBack').hidden = false;
  $('#modalBack').onclick = closeModal;
  const d = document.createElement('div');
  d.className = 'drawer';
  d.innerHTML = `<div class="drawer-head"><span class="ftype ${esc(kindOf(source))}" style="width:26px;height:30px">${esc(kindOf(source))}</span><h2>${esc(fileName(source))}</h2><button class="icon-btn" id="dClose">${ICON.close}</button></div>
    <div class="drawer-body" id="docBody"><div class="skel"></div><div class="skel"></div><div class="skel"></div></div>`;
  document.body.appendChild(d);
  $('#dClose').onclick = closeModal;
  const chunks = await api('/api/documents/chunks?source=' + encodeURIComponent(source));
  $('#docBody').innerHTML = `<p class="muted" style="margin-top:0">${chunks.length} passages · this is exactly what the retriever sees.</p>` +
    chunks.map((c) => `<div class="chunk"><div class="loc">${esc(c.location)} · ${c.id}</div>${esc(c.text)}</div>`).join('');
}

// Analytics
const C = { ink: '#1f1d19', rust: '#b4532a', teal: '#1f5f5b', amber: '#c98a1b', line: '#e1dbcd', sand: '#d3ccbb' };

function barChart(data, { w = 560, h = 190, color = C.rust, label = (d) => d.label, value = (d) => d.value, fmt = (v) => v, xEvery = 1 } = {}) {
  if (!data.length) return '<div class="empty" style="padding:30px">No data yet</div>';
  const pad = { l: 34, r: 6, t: 10, b: 24 };
  const max = Math.max(1, ...data.map(value));
  const nice = Math.ceil(max / 4) * 4 || 4;
  const bw = (w - pad.l - pad.r) / data.length;
  const y = (v) => pad.t + (h - pad.t - pad.b) * (1 - v / nice);
  let g = '<g class="grid">';
  for (let i = 0; i <= 4; i++) { const v = (nice / 4) * i; g += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${pad.l - 6}" y="${y(v) + 3}" text-anchor="end">${fmt(v)}</text>`; }
  g += '</g>';
  const bars = data.map((d, i) => {
    const v = value(d); const x = pad.l + i * bw + bw * 0.16; const bh = h - pad.b - y(v);
    return `<rect class="bar" x="${x}" y="${y(v)}" width="${bw * 0.68}" height="${Math.max(0, bh)}" rx="2.5" fill="${color}"><title>${esc(label(d))}: ${v}</title></rect>` +
      (i % xEvery === 0 ? `<text x="${x + bw * 0.34}" y="${h - 8}" text-anchor="middle">${esc(label(d))}</text>` : '');
  }).join('');
  return `<svg viewBox="0 0 ${w} ${h}">${g}${bars}</svg>`;
}

function groupedBars(groups, series, { w = 560, h = 210 } = {}) {
  const pad = { l: 34, r: 6, t: 10, b: 26 };
  const y = (v) => pad.t + (h - pad.t - pad.b) * (1 - v);
  const gw = (w - pad.l - pad.r) / groups.length;
  let g = '<g class="grid">';
  for (let i = 0; i <= 4; i++) { const v = i / 4; g += `<line x1="${pad.l}" x2="${w - pad.r}" y1="${y(v)}" y2="${y(v)}"/><text x="${pad.l - 6}" y="${y(v) + 3}" text-anchor="end">${v.toFixed(2)}</text>`; }
  g += '</g>';
  const bw = (gw * 0.78) / series.length;
  const bars = groups.map((grp, gi) => series.map((s, si) => {
    const v = grp.values[si] ?? 0; const x = pad.l + gi * gw + gw * 0.11 + si * bw;
    return `<rect class="bar" x="${x}" y="${y(v)}" width="${bw - 2}" height="${h - pad.b - y(v)}" rx="2" fill="${s.color}"><title>${esc(grp.label)} · ${s.name}: ${v}</title></rect>`;
  }).join('') + `<text x="${pad.l + gi * gw + gw / 2}" y="${h - 8}" text-anchor="middle">${esc(grp.label)}</text>`).join('');
  return `<svg viewBox="0 0 ${w} ${h}">${g}${bars}</svg>`;
}

async function viewAnalytics() {
  main.innerHTML = `<div class="view"><div class="page" style="max-width:1240px">
    <div class="page-head"><div><h1>Analytics</h1><p>How the assistant is doing for ${esc(state.config?.customer_name || 'this customer')}: usage, speed, quality and where the docs fall short.</p></div>
      <div class="actions"><span class="pill"><span class="d" style="color:#6fbf8e"></span>live from this server</span></div></div>
    <div class="kpis" id="kpis">${'<div class="card kpi"><div class="skel"></div><div class="skel" style="height:24px"></div></div>'.repeat(6)}</div>
    <div id="charts"></div>
  </div></div>`;
  const a = await api('/api/analytics');
  const c = a.counts;
  const rated = c.thumbs_up + c.thumbs_down;
  const llm = a.eval.results_llm;
  const answerRate = llm ? Math.round((llm.answered / llm.questions) * 100) + '%' : '–';
  $('#kpis').innerHTML = [
    ['Questions asked', fmtNum(c.questions), `${fmtNum(c.conversations)} conversations`],
    ['Helpful rate', rated ? Math.round((c.thumbs_up / rated) * 100) + '%' : '–', `${c.thumbs_up} up · ${c.thumbs_down} down`],
    ['Escalated', fmtNum(c.tickets_total), `${c.tickets_open} still open`],
    ['Median answer time', a.latency.p50_s != null ? a.latency.p50_s.toFixed(1) + 's' : '–', `p95 ${a.latency.p95_s != null ? a.latency.p95_s.toFixed(1) + 's' : '–'} · n=${a.latency.n}`],
    ['First token', a.latency.ttft_p50_ms ? (a.latency.ttft_p50_ms / 1000).toFixed(1) + 's' : '–', a.latency.tokens_per_s_p50 ? `${Math.round(a.latency.tokens_per_s_p50)} tok/s on CPU` : 'streamed'],
    ['Knowledge base', fmtNum(c.documents), `${fmtNum(c.chunks)} passages`],
  ].map(([k, v, s]) => `<div class="card kpi"><div class="k">${k}</div><div class="v">${v}</div><div class="s">${s}</div></div>`).join('');

  const ev = a.eval.results?.modes || {};
  const evr = a.eval.results_rerank?.modes || {};
  const groups = [['vector', ev.vector], ['BM25', ev.bm25], ['hybrid', ev.hybrid], ['hybrid + rerank', evr.hybrid]]
    .filter(([, r]) => r).map(([label, r]) => ({ label, values: [r['hit@1'], r['hit@5'], r['mrr@10']] }));
  const series = [{ name: 'hit@1', color: C.rust }, { name: 'hit@5', color: C.teal }, { name: 'MRR@10', color: C.sand }];
  const quality = llm ? [
    ['Answers with a citation', llm.answers_with_citation],
    ['Cited sentences supported (NLI)', llm.cited_sentences_supported_nli],
    ['Cites the right document', llm.cites_correct_source],
    ['Right passage in context', llm.gold_passage_in_context],
    ['Off-topic questions refused', llm.off_topic_refused / llm.off_topic_questions],
  ] : [];
  $('#charts').innerHTML = `
    <div class="grid2">
      <div class="card chart-card"><h3>Questions per ${a.volume_unit}</h3><div class="sub">${a.volume_unit === 'hour' ? 'Last 24 hours' : 'Last 14 days'}, from the request log</div>
        <div class="chart">${barChart(a.volume, { label: (d) => d.day, value: (d) => d.count, xEvery: a.volume_unit === 'hour' ? 4 : 2, color: C.ink })}</div></div>
      <div class="card chart-card"><h3>Answer latency</h3><div class="sub">Full streamed answer, seconds (n=${a.latency.n})</div>
        <div class="chart">${barChart(a.latency.histogram_s, { label: (d) => d.from + 's', value: (d) => d.count, color: C.rust, xEvery: 2 })}</div></div>
    </div>
    <div class="grid2">
      <div class="card chart-card"><h3>Retrieval quality by mode</h3><div class="sub">${a.eval.results?.questions || 0} hand-labelled questions · a hit needs the right file and the key phrase</div>
        <div class="chart">${groups.length ? groupedBars(groups, series) : '<div class="empty">Run eval/run_eval.py</div>'}</div>
        <div class="legend">${series.map((s) => `<span><i style="background:${s.color}"></i>${s.name}</span>`).join('')}</div></div>
      <div class="card chart-card"><h3>Answer quality</h3><div class="sub">${llm ? `${esc(llm.model.replace('-local', ''))} on ${llm.questions} questions + ${llm.off_topic_questions} off-topic` : 'Run eval/answer_eval.py'}</div>
        ${quality.map(([k, v]) => `<div class="hbar"><span>${k}</span><span class="track"><i style="width:${Math.round(v * 100)}%"></i></span><span class="val">${Math.round(v * 100)}%</span></div>`).join('')}</div>
    </div>
    <div class="grid2 even">
      <div class="card chart-card"><h3>Most cited documents</h3><div class="sub">What people actually need</div>
        ${a.top_sources.length ? a.top_sources.map((s) => `<div class="hbar"><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">${esc(fileName(s.source))}</span><span class="track"><i style="width:${Math.round((s.count / a.top_sources[0].count) * 100)}%;background:${C.rust}"></i></span><span class="val">${s.count}</span></div>`).join('') : '<p class="muted">No answers yet.</p>'}</div>
      <div class="card chart-card"><h3>Gaps in the docs</h3><div class="sub">Questions the assistant couldn't answer, and recent thumbs-down</div>
        <ul class="list-mini">
          ${a.unanswered.map((u) => `<li><span class="pill amber">no answer</span><a href="#/c/${u.conversation_id}">${esc(u.question)}</a><span class="muted" style="margin-left:auto;white-space:nowrap">${ago(u.created_at)}</span></li>`).join('')}
          ${a.feedback.filter((f) => f.rating < 0).map((f) => `<li><span class="pill red">down</span><a href="#/c/${f.conversation_id}">${esc(f.question)}</a>${f.comment ? `<span class="muted">“${esc(f.comment)}”</span>` : ''}</li>`).join('')}
          ${!a.unanswered.length && !a.feedback.some((f) => f.rating < 0) ? '<li class="muted">Nothing flagged yet.</li>' : ''}
        </ul></div>
    </div>`;
}

// Settings
async function viewSettings() {
  main.innerHTML = `<div class="view"><div class="page"><div class="page-head"><div><h1>Settings</h1><p>Everything that changes per customer lives here and in <span class="mono">config/customer.yaml</span>.</p></div></div><div id="settingsBody"><div class="skel"></div></div></div></div>`;
  const [s, llm] = await Promise.all([api('/api/settings'), api('/api/llm/status')]);
  const models = [...new Set([...(llm.models || []).map((m) => m.replace(':latest', '')), s.ollama_model])];
  const origin = location.origin;
  $('#settingsBody').innerHTML = `
    <div class="settings-grid">
      <div class="card section">
        <h3>Customer</h3><p>Branding and what people see first.</p>
        <div class="row2"><label class="field"><span>Customer name</span><input class="input" data-k="customer_name" value="${esc(s.customer_name)}"></label>
        <label class="field"><span>Assistant name</span><input class="input" data-k="assistant_name" value="${esc(s.assistant_name)}"></label></div>
        <label class="field"><span>Greeting</span><textarea class="input" data-k="greeting" rows="3">${esc(s.greeting)}</textarea></label>
        <label class="field"><span>Suggested questions</span><textarea class="input" data-k="suggested_questions" rows="4">${esc(s.suggested_questions.join('\n'))}</textarea><small>One per line. Shown on the empty Ask screen.</small></label>
      </div>
      <div class="card section">
        <h3>Answering</h3><p>Retrieval and the model. Changes apply to the next question.</p>
        <label class="field"><span>Retrieval mode</span><select class="input" data-k="retrieval_mode">${['hybrid', 'vector', 'bm25'].map((m) => `<option ${m === s.retrieval_mode ? 'selected' : ''}>${m}</option>`).join('')}</select>
          <small>Hybrid scored best in our eval: hit@5 0.92 and MRR 0.77, vs 0.86 / 0.75 vector-only.</small></label>
        <label class="toggle"><input type="checkbox" data-k="rerank" ${s.rerank ? 'checked' : ''}><span class="sw"></span><span>Re-order with the cross-encoder<br><small class="muted">Off by default: on our set it added ~155 ms and didn't beat plain hybrid. The cross-encoder still scores the top passages to decide when to say "I couldn't find this".</small></span></label>
        <div class="row2"><label class="field"><span>Passages retrieved</span><input class="input" type="number" min="1" max="20" data-k="top_k" value="${s.top_k}"></label>
        <label class="field"><span>Passages sent to the model</span><input class="input" type="number" min="1" max="10" data-k="context_k" value="${s.context_k}"></label></div>
        <label class="field"><span>Refuse below relevance score</span><input class="input" type="number" step="0.5" data-k="min_rerank_score" value="${s.min_rerank_score}">
          <small>Cross-encoder logit. In testing, real questions scored ≥ −1.1 and off-topic ones ≤ −7.2.</small></label>
        <div class="row2"><label class="field"><span>Answer generator</span><select class="input" data-k="generator">${[['ollama', 'Local LLM (Ollama)'], ['extractive', 'Extractive (no LLM)'], ['gemini', 'Gemini API (stub)']].map(([v, l]) => `<option value="${v}" ${v === s.generator ? 'selected' : ''}>${l}</option>`).join('')}</select></label>
        <label class="field"><span>Model</span><select class="input" data-k="ollama_model">${models.map((m) => `<option ${m === s.ollama_model ? 'selected' : ''}>${esc(m)}</option>`).join('')}</select>
          <small>${llm.reachable ? `Ollama reachable · ${llm.models.length} models` : 'Ollama not reachable, answers fall back to extractive'}</small></label></div>
      </div>
      <div class="card section" style="grid-column:1 / -1">
        <h3>Integrate</h3><p>The same API the UI uses. Plug it into a helpdesk, Slack bot or the product's own docs site.</p>
<pre class="code"><span class="c"># one-shot JSON answer</span>
curl -s ${esc(origin)}/ask \\
  -H <span class="s">"X-API-Key: $DEPLOYDESK_KEY"</span> -H <span class="s">"Content-Type: application/json"</span> \\
  -d <span class="s">'{"question": "how do I enable CORS?"}'</span>

<span class="c"># streamed tokens (server-sent events) + saved to history</span>
curl -N ${esc(origin)}/api/ask/stream -H <span class="s">"X-API-Key: $DEPLOYDESK_KEY"</span> \\
  -H <span class="s">"Content-Type: application/json"</span> -d <span class="s">'{"question": "how many workers?"}'</span></pre>
        <p class="muted" style="font-size:12.5px">Full reference at <a href="/docs" target="_blank">/docs</a>. Keys are set with the <span class="mono">DEPLOYDESK_API_KEYS</span> env var, never in this page.</p>
      </div>
    </div>
    <div class="save-bar"><button class="btn primary" id="saveSettings">Save settings</button><span class="muted" id="saveNote" style="font-size:12.5px">Saved to ${esc(s.config_path || 'memory only')}</span></div>`;
  $('#saveSettings').onclick = async () => {
    const body = {};
    $$('[data-k]').forEach((el) => {
      const k = el.dataset.k;
      if (el.type === 'checkbox') body[k] = el.checked;
      else if (el.type === 'number') body[k] = Number(el.value);
      else if (k === 'suggested_questions') body[k] = el.value.split('\n').map((x) => x.trim()).filter(Boolean);
      else body[k] = el.value;
    });
    try {
      await api('/api/settings', { method: 'PUT', body });
      await loadConfig();
      toast('Settings saved');
      refreshSidebar();
    } catch (e) { toast(esc(e.message), true); }
  };
}

// boot
async function loadConfig() {
  state.config = await fetch('/api/config').then((r) => r.json());
  if (!state.key && state.config.demo_key) state.key = state.config.demo_key;
  $('#brandName').textContent = state.config.assistant_name;
  $('#brandSub').textContent = state.config.customer_name + ' · support';
  document.title = state.config.assistant_name;
  state.mode = state.config.retrieval_mode || 'hybrid';
}

(async () => {
  await loadConfig();
  if (!state.key) askForKey();
  const h = await fetch('/health').then((r) => r.json());
  state.chunks = h.chunks;
  route();
  refreshSidebar();
})();
