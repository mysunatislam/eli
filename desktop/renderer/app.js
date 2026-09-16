// Renderer: WebSocket to the backend, panel UI, heart interaction, movement requests.
(() => {
  const $ = (s) => document.querySelector(s);
  const heart = new Heart($('#heart'));
  const panel = $('#panel'), transcript = $('#transcript'), input = $('#input');
  const bubble = $('#bubble'), bubbleText = $('#bubble-text'), bubbleActions = $('#bubble-actions'), toastEl = $('#toast');
  const wrap = $('#heart-wrap');
  const quick = $('#quick'), quickInput = $('#quick-input');

  let cfg = { backendUrl: 'ws://127.0.0.1:8790/ws/desktop', httpUrl: 'http://127.0.0.1:8790' };
  let ws = null, status = {}, panelOpen = false, quickOpen = false, reconnect = null;
  let bubbleTimer = null, toastTimer = null, idleDriftTimer = null;
  let pendingConfirm = null, pendingPermission = null, pendingNudge = null;
  let liveLine = null, liveId = null;

  heart.onFirstFrame = () => window.eli.painted();

  // ---------- connection ----------
  function connect() {
    try { ws = new WebSocket(cfg.backendUrl); } catch (e) { scheduleReconnect(); return; }
    ws.onopen = () => { heart.offline = false; heart.setState('idle'); setPill('idle'); heart.wave(); console.debug('[ws] open'); };
    ws.onmessage = (e) => { try { handle(JSON.parse(e.data)); } catch (err) { console.error(err); } };
    ws.onclose = () => { heart.offline = true; setPill('offline'); $('#pill-llm').textContent = 'backend off'; console.debug('[ws] closed'); window.eli.guide({ action: 'clear' }); scheduleReconnect(); };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }
  function scheduleReconnect() { clearTimeout(reconnect); reconnect = setTimeout(connect, 3000); }
  function send(obj) {
    if (ws && ws.readyState === 1) { ws.send(JSON.stringify(obj)); return true; }
    toast('Eli\'s backend is offline. Run start.bat (or python backend/run.py).');
    return false;
  }

  function handle(m) {
    switch (m.type) {
      case 'status': status = m; renderStatus(); if (m.state) { heart.setState(m.state); setPill(m.state); } if (m.guide) window.eli.guideCheck(m.guide); break;
      case 'persona_changed':
        if (m.persona) {
          heart.setPersona(m.persona);
          const sel = $('#select-persona');
          if (sel) sel.value = m.persona.id;
          if (m.persona.color) document.documentElement.style.setProperty('--accent', m.persona.color);
          heart.wave();
          toast('Avatar: ' + m.persona.name + ' (' + m.persona.role + ')');
        }
        break;
      case 'state': heart.setState(m.state); setPill(m.state); $('#btn-stop').hidden = m.state !== 'talking'; $('#quick-dot').className = 'quick-dot ' + m.state; window.eli.guideState(m.state); break;
      case 'teach_event': toast((m.kind === 'shortcut' ? 'saw: press ' : 'saw: click ') + m.label); break;
      case 'guide':
        window.eli.guide(m);
        if (m.action === 'show' && m.step) heart.setState('executing');
        // the indicator layer once silently failed to appear: after any step event, have main verify it
        if (m.action === 'show' || m.action === 'recognized' || m.action === 'resumed') setTimeout(() => window.eli.guideCheck({ active: true }), 1600);
        break;
      case 'transcript_delta': liveDelta(m); break;
      case 'transcript':
        finishLive();
        addLine(m.role, m.text);
        if (m.role === 'eli' && !panelOpen && !pendingNudge) showBubble(m.text);
        break;
      case 'history':
        transcript.innerHTML = '';
        (m.items || []).forEach((it) => addLine(it.role, it.text, true));
        if (!m.items || !m.items.length) emptyState();
        break;
      case 'context':
        $('#context').textContent = 'Seeing: ' + (m.app || m.process || '—') + (m.title ? ' — ' + m.title : '');
        break;
      case 'attention': window.eli.attention(m.rect, m.ms || 12000); break;
      case 'tool': addLine('tool', m.detail || m.name); break;
      case 'confirm_request': showConfirm(m); break;
      case 'permission_request': showPermission(m); break;
      case 'nudge': showNudge(m); break;
      case 'toast': toast(m.text); break;
      case 'notify': window.eli.notify(m.title, m.body); break;
      default: break;
    }
  }

  // ---------- rendering ----------
  function setPill(state) {
    const p = $('#pill-state');
    p.textContent = state;
    p.className = 'pill ' + state;
  }
  function renderStatus() {
    $('#tg-observe').checked = !!status.observe_enabled;
    $('#tg-wake').checked = !!status.wake_enabled;
    $('#tg-voice').checked = !!status.voice_replies;
    $('#tg-private').checked = !!status.private_mode;
    $('#tg-automation').checked = status.automation_enabled !== false;
    $('#tg-follow').checked = status.follow_cursor !== false;
    $('#tg-trust').checked = !!status.trust_mode;
    $('#tg-autofix').checked = status.proactive_mode === 'auto';
    $('#tg-autostart').checked = !!status.autostart;
    $('#pill-observe').hidden = !status.observe_enabled || !!status.private_mode;
    $('#pill-private').hidden = !status.private_mode;
    const teach = status.teach && status.teach.recording;
    const tp = $('#pill-teach');
    if (tp) { tp.hidden = !(teach && teach.active); if (teach && teach.active) tp.textContent = '● learning · ' + (teach.events || 0) + ' action' + (teach.events === 1 ? '' : 's'); }
    $('#pill-trust').hidden = !status.trust_mode;
    window.eli.setFollow(status.follow_cursor !== false);
    $('#perm-screen').textContent = status.screen_permission || 'ask';
    if (document.activeElement !== $('#blocked-apps')) $('#blocked-apps').value = (status.blocked_apps || []).join(', ');
    heart.observing = !!status.observe_enabled && !status.private_mode;
    heart.mic = !!status.mic_live;
    $('#btn-mic').classList.toggle('live', !!status.mic_live);
    const llm = $('#pill-llm');
    if (status.llm) { llm.textContent = status.model || 'llm on'; llm.className = 'pill pill-on'; llm.title = (status.provider || '') + ' connected'; }
    else { llm.textContent = 'offline mode'; llm.className = 'pill pill-off'; llm.title = status.llm_reason || ''; }
    if (status.mic === false && status.audio_error) $('#btn-mic').title = status.audio_error;
    const u = status.usage || {};
    $('#usage').textContent = status.llm
      ? `Model: ${status.provider}/${status.model} · ${u.calls || 0} calls · ${((u.input || 0) + (u.output || 0)).toLocaleString()} tokens · last ${u.last_ms || 0} ms`
      : 'Model: offline mode';
    renderJobs(status.jobs || []);
    if (status.active_persona) {
      const sel = $('#select-persona');
      if (sel && document.activeElement !== sel) sel.value = status.active_persona;
    }
    if (status.persona) {
      heart.setPersona(status.persona);
      if (status.persona.color) document.documentElement.style.setProperty('--accent', status.persona.color);
    }
    const ms = status.memory || {};
    $('#mem-stats').textContent = `Memory: ${ms.memories || 0} memories, ${ms.entities || 0} entities, ${ms.conversation_lines || 0} lines · encrypted (${ms.key_source || '?'})`;
    if (!$('#mobile-info').hidden) renderMobileInfo();
  }
  function renderJobs(jobs) {
    const badge = $('#jobs-badge');
    badge.hidden = !jobs.length; badge.textContent = String(jobs.length);
    const list = $('#jobs-list');
    list.innerHTML = '';
    if (!jobs.length) { const d = document.createElement('div'); d.className = 'lbl'; d.textContent = 'Nothing scheduled.'; list.appendChild(d); return; }
    for (const j of jobs) {
      const row = document.createElement('div'); row.className = 'job';
      const txt = document.createElement('div'); txt.className = 'txt'; txt.textContent = `#${j.id} ${j.text}`;
      const meta = document.createElement('div'); meta.className = 'meta';
      const nxt = Math.round((j.next_run * 1000 - Date.now()) / 60000);
      meta.textContent = `${j.kind}${j.every ? ' · every ' + Math.round(j.every / 60) + ' min' : ''} · next ${nxt <= 0 ? 'now' : 'in ' + nxt + ' min'} · ${j.runs} run${j.runs === 1 ? '' : 's'}` + (j.last_result ? ' · last: ' + j.last_result.slice(0, 60) : '');
      txt.appendChild(meta);
      const btn = document.createElement('button'); btn.className = 'link-btn'; btn.textContent = 'cancel';
      btn.onclick = () => send({ type: 'job_cancel', id: j.id });
      row.appendChild(txt); row.appendChild(btn); list.appendChild(row);
    }
  }
  function renderMobileInfo() {
    const info = $('#mobile-info');
    const connected = status.mobile_connected ? `Phone connected (${status.mobile_connected}).` : 'No phone connected yet.';
    info.innerHTML = `${connected}<br>On your phone (same Wi-Fi), open:<br><code>${escapeHtml(status.mobile_url || '…')}</code>`;
  }
  function emptyState() {
    transcript.innerHTML = `<div class="empty"><b>Hi, I'm Eli.</b><br>Try: “open notepad”, “what am I looking at?”, “what is wrong?”, “prepare my project report email”, “open my CPAP project”, “remember that I code in Python”.<br><span style="opacity:.7">Ctrl+Shift+Space to talk · click the heart to hide me · Ctrl+Shift+H brings me to your cursor</span></div>`;
  }
  function addLine(role, text, quiet) {
    const empty = transcript.querySelector('.empty');
    if (empty) empty.remove();
    const div = document.createElement('div');
    div.className = 'line ' + role;
    div.innerHTML = role === 'tool' ? escapeHtml(text) : mdLite(text);
    transcript.appendChild(div);
    transcript.scrollTop = transcript.scrollHeight;
    if (!quiet) {
      const tools = transcript.querySelectorAll('.line.tool');
      if (tools.length > 30) tools[0].remove();
    }
    return div;
  }
  function liveDelta(m) {
    if (!liveLine || liveId !== m.id) {
      finishLive();
      liveId = m.id;
      liveLine = addLine('eli', '');
      liveLine.classList.add('live');
      liveLine.dataset.text = '';
    }
    liveLine.dataset.text += m.text || '';
    liveLine.innerHTML = mdLite(liveLine.dataset.text);
    transcript.scrollTop = transcript.scrollHeight;
    if (!panelOpen) showBubble(liveLine.dataset.text, true);
  }
  function finishLive() {
    if (liveLine) { liveLine.remove(); liveLine = null; liveId = null; }
  }
  function mdLite(text) {
    let s = escapeHtml(text || '');
    s = s.replace(/```(\w+)?\n?([\s\S]*?)```/g, (_m, _l, code) => `<pre>${code.replace(/\n$/, '')}</pre>`);
    s = s.replace(/`([^`\n]+)`/g, '<code>$1</code>');
    s = s.replace(/\n/g, '<br>');
    s = s.replace(/<pre>([\s\S]*?)<\/pre>/g, (m) => m.replace(/<br>/g, '\n'));
    return s;
  }
  function escapeHtml(s) { return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }

  function showBubble(text, live) {
    bubble.classList.remove('nudge');
    bubbleActions.hidden = true;
    bubbleActions.innerHTML = '';
    bubbleText.textContent = text.length > 220 ? text.slice(0, 217) + '…' : text;
    bubble.hidden = false;
    updateHitRegions();
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => { bubble.hidden = true; updateHitRegions(); }, live ? 8000 : 5000 + Math.min(12000, text.length * 40));
  }
  function showNudge(m) {
    pendingNudge = m.id;
    bubble.classList.add('nudge');
    bubbleText.textContent = m.text;
    bubbleActions.innerHTML = '';
    (m.actions || [{ id: 'yes', label: 'Yes' }, { id: 'no', label: 'Not now' }]).forEach((a) => {
      const b = document.createElement('button');
      b.className = 'btn' + (a.primary ? ' btn-primary' : '');
      b.textContent = a.label;
      b.onclick = (ev) => { ev.stopPropagation(); send({ type: 'nudge_action', id: m.id, action: a.id }); bubble.hidden = true; pendingNudge = null; updateHitRegions(); };
      bubbleActions.appendChild(b);
    });
    bubbleActions.hidden = false;
    bubble.hidden = false;
    heart.pulse();
    updateHitRegions();
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => { bubble.hidden = true; updateHitRegions(); }, 45000);
    addLine('nudge', m.text);
  }
  function toast(text) {
    toastEl.textContent = text;
    toastEl.hidden = false;
    updateHitRegions();
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; updateHitRegions(); }, 3500);
  }

  // ---------- hit regions (which parts of the transparent window catch the mouse) ----------
  function updateHitRegions() {
    const rects = [];
    const push = (el, pad) => {
      if (!el || el.hidden) return;
      const r = el.getBoundingClientRect();
      if (r.width && r.height) rects.push({ x: r.left - (pad || 0), y: r.top - (pad || 0), w: r.width + 2 * (pad || 0), h: r.height + 2 * (pad || 0) });
    };
    const hw = wrap.getBoundingClientRect();
    rects.push({ x: hw.left + 28, y: hw.top + 34, w: 104, h: 100 });
    push(panel); push(quick, 4); push(bubble, 4); push(toastEl, 4); push($('#permission'));
    window.eli.setHitRegions(rects);
  }

  // ---------- quick bar (default) and full panel (opt-in) ----------
  function setQuick(open) {
    quickOpen = open;
    quick.hidden = !open;
    if (open) { bubble.hidden = true; }
    updateHitRegions();
    window.eli.panelState(open || panelOpen);
    if (open) setTimeout(() => quickInput.focus(), 30);
  }
  function setPanel(open) {
    panelOpen = open;
    panel.hidden = !open;
    bubble.hidden = true;
    if (open && quickOpen) { quickOpen = false; quick.hidden = true; }
    updateHitRegions();
    window.eli.panelState(open || quickOpen);
    if (open) setTimeout(() => input.focus(), 30);
  }
  function toggleUI() {
    if (panelOpen) setPanel(false);
    else setQuick(!quickOpen);
  }
  quick.addEventListener('submit', (e) => {
    e.preventDefault();
    const text = quickInput.value.trim();
    if (!text) return;
    if (send({ type: 'user_text', text })) quickInput.value = '';
  });
  quickInput.addEventListener('keydown', (e) => { if (e.key === 'Escape') setQuick(false); });
  $('#quick-mic').addEventListener('click', () => send({ type: 'ptt' }));
  $('#quick-expand').addEventListener('click', () => setPanel(true));
  $('#btn-close').addEventListener('click', () => setPanel(false));
  $('#composer').addEventListener('submit', (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    if (send({ type: 'user_text', text })) input.value = '';
  });
  input.addEventListener('keydown', (e) => { if (e.key === 'Escape') setPanel(false); });
  $('#btn-mic').addEventListener('click', () => send({ type: 'ptt' }));
  $('#btn-stop').addEventListener('click', () => send({ type: 'stop_speaking' }));
  $('#btn-quit').addEventListener('click', () => window.eli.quit());
  $('#btn-mobile').addEventListener('click', () => {
    const info = $('#mobile-info');
    info.hidden = !info.hidden;
    $('#perms').hidden = true;
    if (!info.hidden) { renderMobileInfo(); send({ type: 'get_status' }); }
  });
  $('#btn-perms').addEventListener('click', () => {
    const p = $('#perms');
    p.hidden = !p.hidden;
    $('#mobile-info').hidden = true; $('#jobs').hidden = true;
    if (!p.hidden) send({ type: 'get_status' });
  });
  $('#btn-jobs').addEventListener('click', () => {
    const p = $('#jobs');
    p.hidden = !p.hidden;
    $('#mobile-info').hidden = true; $('#perms').hidden = true;
    if (!p.hidden) send({ type: 'get_status' });
  });
  for (const [id, key] of [['#tg-observe', 'observe_enabled'], ['#tg-wake', 'wake_enabled'], ['#tg-voice', 'voice_replies'],
    ['#tg-private', 'private_mode'], ['#tg-automation', 'automation_enabled'], ['#tg-follow', 'follow_cursor'], ['#tg-trust', 'trust_mode']]) {
    $(id).addEventListener('change', (e) => send({ type: 'set_setting', key, value: e.target.checked }));
  }
  $('#tg-autofix').addEventListener('change', (e) => send({ type: 'set_setting', key: 'proactive_mode', value: e.target.checked ? 'auto' : 'ask' }));
  $('#tg-autostart').addEventListener('change', (e) => send({ type: 'set_setting', key: 'autostart', value: e.target.checked }));
  $('#blocked-apps').addEventListener('change', (e) => {
    const list = e.target.value.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean);
    send({ type: 'set_setting', key: 'blocked_apps', value: list });
  });
  $('#btn-revoke').addEventListener('click', () => send({ type: 'set_setting', key: 'screen_permission', value: 'ask' }));
  $('#btn-forget-today').addEventListener('click', () => send({ type: 'forget_today' }));
  $('#btn-forget-all').addEventListener('click', () => { if (confirm('Delete every memory Eli has? This cannot be undone.')) send({ type: 'forget_all' }); });
  const selPersona = $('#select-persona');
  if (selPersona) {
    selPersona.addEventListener('change', (e) => {
      send({ type: 'set_persona', persona: e.target.value });
    });
  }

  // ---------- confirmations & permissions ----------
  // With the full panel closed these are small bubbles next to the heart, so nothing big pops up.
  function bubbleAsk(text, yesLabel, noLabel, onAnswer) {
    bubble.classList.add('nudge');
    bubbleText.textContent = text;
    bubbleActions.innerHTML = '';
    for (const [label, ok, primary] of [[noLabel, false, false], [yesLabel, true, true]]) {
      const b = document.createElement('button');
      b.className = 'btn' + (primary ? ' btn-primary' : '');
      b.textContent = label;
      b.onclick = (ev) => { ev.stopPropagation(); onAnswer(ok); bubble.hidden = true; updateHitRegions(); };
      bubbleActions.appendChild(b);
    }
    bubbleActions.hidden = false;
    bubble.hidden = false;
    pendingNudge = 'ask';
    heart.pulse();
    updateHitRegions();
    clearTimeout(bubbleTimer);
    bubbleTimer = setTimeout(() => { bubble.hidden = true; pendingNudge = null; updateHitRegions(); }, 90000);
  }
  function showConfirm(m) {
    pendingConfirm = m.id;
    if (panelOpen) {
      $('#confirm-text').textContent = m.description;
      $('#confirm').hidden = false;
      heart.pulse();
    } else {
      bubbleAsk('Eli wants to ' + m.description + ' — OK?', 'Approve', 'Cancel', (ok) => answerConfirm(ok));
    }
  }
  function answerConfirm(approve) {
    if (!pendingConfirm) return;
    send({ type: 'confirm', id: pendingConfirm, approve });
    pendingConfirm = null;
    pendingNudge = null;
    $('#confirm').hidden = true;
  }
  $('#confirm-yes').addEventListener('click', () => answerConfirm(true));
  $('#confirm-no').addEventListener('click', () => answerConfirm(false));
  function showPermission(m) {
    pendingPermission = m.id;
    if (panelOpen) {
      $('#permission-text').textContent = m.reason || 'Eli wants to capture your screen.';
      $('#permission').hidden = false;
      updateHitRegions();
    } else {
      bubbleAsk('Allow Eli to see your screen? ' + (m.reason || '') + ' Screenshots stay on this PC.', 'Allow', 'Not now', (ok) => answerPermission(ok));
    }
  }
  function answerPermission(allow) {
    if (!pendingPermission) return;
    send({ type: 'permission', id: pendingPermission, allow });
    pendingPermission = null;
    pendingNudge = null;
    $('#permission').hidden = true;
    updateHitRegions();
  }
  $('#perm-yes').addEventListener('click', () => answerPermission(true));
  $('#perm-no').addEventListener('click', () => answerPermission(false));

  // ---------- heart: click to toggle, drag to move ----------
  let drag = null;
  wrap.addEventListener('mousedown', (e) => {
    if (e.button !== 0) return;
    drag = { x: e.screenX, y: e.screenY, moved: false };
    window.eli.dragStart();
    e.preventDefault();
  });
  window.addEventListener('mousemove', (e) => {
    if (!drag) return;
    const dx = e.screenX - drag.x, dy = e.screenY - drag.y;
    if (Math.abs(dx) + Math.abs(dy) > 2) drag.moved = true;
    if (drag.moved) { window.eli.moveBy(dx, dy); drag.x = e.screenX; drag.y = e.screenY; }
  });
  window.addEventListener('mouseup', () => {
    if (!drag) return;
    const moved = drag.moved;
    drag = null;
    window.eli.dragEnd();
    if (!moved) { heart.pulse(); toggleUI(); }
  });
  window.eli.onCursor((x, y) => {
    const r = wrap.getBoundingClientRect();
    heart.lookAt(x - (r.left + r.width / 2), y - (r.top + r.height / 2 + 6));
  });
  bubble.addEventListener('click', () => { if (!pendingNudge) setPanel(true); });

  // Idle drift: every so often the heart floats a little and settles back (never while the panel is open).
  function scheduleDrift() {
    clearTimeout(idleDriftTimer);
    idleDriftTimer = setTimeout(() => {
      if (!panelOpen && !quickOpen && !heart.offline && heart.effectiveState() === 'idle' && status.follow_cursor === false
          && !window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
        window.eli.glideOffset((Math.random() - 0.5) * 60, -10 - Math.random() * 30, 1400);
        setTimeout(() => window.eli.glideHome(1600), 4000 + Math.random() * 3000);
      }
      scheduleDrift();
    }, 35000 + Math.random() * 40000);
  }

  // ---------- global shortcuts from main ----------
  window.eli.onShortcut((name) => {
    if (name === 'toggle-panel') toggleUI();
    if (name === 'open-panel') setPanel(true);
    if (name === 'ptt') { send({ type: 'ptt' }); if (!panelOpen && !quickOpen) toast('Listening… (Ctrl+Space to talk / stop)'); }
    if (name === 'wave') heart.wave();
    if (name === 'toggle-follow') send({ type: 'set_setting', key: 'follow_cursor', value: status.follow_cursor === false });
    if (name === 'toggle-trust') send({ type: 'set_setting', key: 'trust_mode', value: !status.trust_mode });
  });

  // main noticed the guide indicator layer is missing mid-guide: ask the backend to re-emit the step
  window.eli.onGuideResend(() => { if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'guide_resend' })); });

  // ---------- boot ----------
  console.debug('[boot] renderer ready');
  emptyState();
  updateHitRegions();
  setInterval(updateHitRegions, 1000);
  scheduleDrift();
  window.eli.getConfig().then((c) => {
    cfg = c || cfg;
    if (cfg.opaque) document.body.classList.add('opaque');
    connect();
  });
})();
