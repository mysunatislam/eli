// Guide overlay renderer: a click-through, full-screen layer that draws animated indicators
// (pulsing rings, glowing boxes, arrows, key badges) exactly where the next action happens, plus a
// step card with the precise instruction. Coordinates arrive already converted to this window's pixels.
(() => {
  const cv = document.getElementById('fx');
  const ctx = cv.getContext('2d');
  const card = document.getElementById('card'), banner = document.getElementById('banner');
  const $ = (id) => document.getElementById(id);
  const dpr = window.devicePixelRatio || 1;
  let W = 0, H = 0, t = 0, last = performance.now();
  let indicators = [];      // {kind, x, y, w, h, r, label, born}
  let arrowTo = null;       // {x, y}
  let checks = [];          // burst animations {x, y, t0}
  let speaking = false;
  let mode = 'idle';        // idle | step | done
  let hideTimer = null;

  function resize() {
    W = window.innerWidth; H = window.innerHeight;
    cv.width = W * dpr; cv.height = H * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }
  window.addEventListener('resize', resize);
  resize();

  // ---------- events from the backend (via the main process) ----------
  window.guide.onEvent((m) => {
    clearTimeout(hideTimer);
    switch (m.action) {
      case 'recognized':
        setIndicators(m.indicators || []);
        card.hidden = true;
        showBanner(m.text || 'Found it', 'showing the steps', 2400);
        mode = 'step';
        break;
      case 'show':
        showStep(m);
        break;
      case 'step_done': {
        const target = arrowTo || (indicators[0] ? { x: indicators[0].x + (indicators[0].w || 0) / 2, y: indicators[0].y + (indicators[0].h || 0) / 2 } : null);
        if (target) checks.push({ x: target.x, y: target.y, t0: t });
        card.classList.add('done');
        markDot(m.index, 'done');
        break;
      }
      case 'complete':
        indicators = []; arrowTo = null;
        card.hidden = true;
        showBanner('✓ Done', m.text || '', 5000);
        checks.push({ x: W / 2, y: H * 0.45 - 60, t0: t });
        mode = 'done';
        break;
      case 'clear':
        indicators = []; arrowTo = null; card.hidden = true; banner.hidden = true; mode = 'idle';
        break;
      case 'paused':
        card.classList.add('paused');
        showBanner('Paused', `Waiting for you to come back to ${m.app || 'the app'}`, 4000);
        break;
      case 'resumed':
        card.classList.remove('paused');
        break;
      case 'stuck':
        card.classList.add('stuck');
        break;
      case 'hint':
        showBanner('Another way', shorten(m.text, 120), 9000);
        break;
      case 'speaking':
        speaking = !!m.on;
        $('voice').hidden = !speaking;
        break;
      default: break;
    }
  });
  window.guide.onState((s) => { speaking = s === 'talking'; $('voice').hidden = !speaking; });

  function showStep(m) {
    const s = m.step || {};
    mode = 'step';
    card.hidden = false;
    card.classList.remove('done', 'stuck', 'paused');
    card.style.animation = 'none'; void card.offsetWidth; card.style.animation = '';
    $('card-step').textContent = `Step ${s.index + 1} of ${s.total}` + (s.optional ? ' · optional' : '');
    $('card-app').textContent = m.app || '';
    $('card-title').textContent = s.title || '';
    $('card-text').textContent = shorten(s.instruction, 170);
    const dots = $('dots'); dots.innerHTML = '';
    for (let i = 0; i < (s.total || 0); i++) {
      const d = document.createElement('i');
      if (i < s.index) d.className = 'done'; else if (i === s.index) d.className = 'now';
      dots.appendChild(d);
    }
    const keyInd = (m.indicators || []).find((i) => i.kind === 'key');
    $('key').hidden = !keyInd; if (keyInd) $('key').textContent = keyInd.keys;
    setIndicators((m.indicators || []).filter((i) => i.kind !== 'key'));
    placeCardAwayFrom(arrowTo);
  }
  function markDot(i, cls) { const d = $('dots').children[i]; if (d) d.className = cls; }
  function showBanner(title, sub, ms) {
    banner.innerHTML = '';
    banner.appendChild(document.createTextNode(title));
    if (sub) { const s = document.createElement('small'); s.textContent = sub; banner.appendChild(s); }
    banner.hidden = false;
    clearTimeout(hideTimer);
    hideTimer = setTimeout(() => { banner.hidden = true; }, ms);
  }
  function setIndicators(list) {
    indicators = list.map((i) => ({ ...i, born: t }));
    const a = list.find((i) => i.kind === 'arrow');
    arrowTo = a ? { x: a.x, y: a.y } : null;
  }
  // The card lives in a corner and moves to the corner farthest from the target, so it never covers
  // the place you need to click (and never the middle of the screen).
  function placeCardAwayFrom(p) {
    card.classList.remove('bottom', 'left');
    if (!p) return;
    const targetRight = p.x > W * 0.5, targetTop = p.y < H * 0.5;
    if (targetTop && targetRight) card.classList.add('bottom');          // target top-right -> card bottom-right
    else if (targetTop && !targetRight) { /* top-left target -> default top-right */ }
    else if (!targetTop && targetRight) { /* bottom-right target -> default top-right */ }
    else { /* bottom-left target -> top-right */ }
  }
  function shorten(text, n) { return text && text.length > n ? text.slice(0, n - 1).trimEnd() + '…' : (text || ''); }

  // ---------- drawing ----------
  function rounded(x, y, w, h, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y); ctx.lineTo(x + w - r, y); ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h - r); ctx.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
    ctx.lineTo(x + r, y + h); ctx.quadraticCurveTo(x, y + h, x, y + h - r);
    ctx.lineTo(x, y + r); ctx.quadraticCurveTo(x, y, x + r, y); ctx.closePath();
  }
  function label(x, y, text, color) {
    if (!text) return;
    ctx.font = '600 13px "Segoe UI", sans-serif';
    const w = ctx.measureText(text).width + 16;
    const lx = Math.min(Math.max(8, x), W - w - 8), ly = Math.max(8, y);
    rounded(lx, ly, w, 24, 8);
    ctx.fillStyle = 'rgba(21,24,34,0.92)'; ctx.fill();
    ctx.strokeStyle = color; ctx.lineWidth = 1; ctx.stroke();
    ctx.fillStyle = '#EEF1F7'; ctx.textBaseline = 'middle'; ctx.fillText(text, lx + 8, ly + 12);
  }
  function drawBox(i, age) {
    const k = Math.min(1, age / 0.35);
    const grow = 1 + (1 - k) * 0.12;
    const cx = i.x + i.w / 2, cy = i.y + i.h / 2;
    const w = i.w * grow, h = i.h * grow;
    const pulse = 0.55 + 0.45 * Math.sin(t * 4 + (i.x + i.y) * 0.01);
    ctx.save();
    ctx.globalAlpha = k;
    const col = i.kind === 'region' ? 'rgba(255,209,102,' : 'rgba(45,200,196,';
    ctx.shadowColor = col + '0.9)'; ctx.shadowBlur = (i.glow ? 22 : 10) * pulse;
    ctx.lineWidth = 2.5; ctx.strokeStyle = col + (0.75 + 0.25 * pulse) + ')';
    if (i.kind === 'region') ctx.setLineDash([10, 8]);
    ctx.lineDashOffset = -t * 30;
    rounded(cx - w / 2, cy - h / 2, w, h, 8); ctx.stroke();
    ctx.setLineDash([]);
    ctx.shadowBlur = 0;
    ctx.fillStyle = col + '0.08)'; rounded(cx - w / 2, cy - h / 2, w, h, 8); ctx.fill();
    label(i.x, i.y - 30, i.label, col + '0.8)');
    ctx.restore();
  }
  function drawRing(i, age) {
    const k = Math.min(1, age / 0.3);
    ctx.save();
    ctx.globalAlpha = k;
    for (let n = 0; n < 3; n++) {
      const ph = ((t * 0.9 + n / 3) % 1);
      const r = i.r * (0.6 + ph * 1.4);
      ctx.beginPath(); ctx.arc(i.x, i.y, r, 0, Math.PI * 2);
      ctx.strokeStyle = `rgba(240,86,122,${(1 - ph) * 0.9})`; ctx.lineWidth = 3 - ph * 2; ctx.stroke();
    }
    ctx.beginPath(); ctx.arc(i.x, i.y, 5, 0, Math.PI * 2); ctx.fillStyle = '#F0567A'; ctx.fill();
    ctx.restore();
  }
  function drawArrow(p, age) {
    // from the step card's nearest edge to the target, with a gentle curve and an animated dash
    const rect = card.hidden ? null : card.getBoundingClientRect();
    if (!rect) return;
    const k = Math.min(1, age / 0.5);
    const fromY = p.y > rect.bottom ? rect.bottom : rect.top;
    const fromX = Math.min(Math.max(rect.left + 24, p.x), rect.right - 24);
    const dx = p.x - fromX, dy = p.y - fromY;
    const dist = Math.hypot(dx, dy);
    if (dist < 40) return;
    const cxp = fromX + dx * 0.5 - dy * 0.15, cyp = fromY + dy * 0.5 + dx * 0.15;
    const endX = p.x - (dx / dist) * (speaking ? 34 : 30), endY = p.y - (dy / dist) * (speaking ? 34 : 30);
    ctx.save();
    ctx.globalAlpha = k;
    ctx.strokeStyle = 'rgba(240,86,122,0.9)'; ctx.lineWidth = 3; ctx.lineCap = 'round';
    ctx.setLineDash([12, 10]); ctx.lineDashOffset = -t * 60;
    ctx.beginPath(); ctx.moveTo(fromX, fromY); ctx.quadraticCurveTo(cxp, cyp, endX, endY); ctx.stroke();
    ctx.setLineDash([]);
    const ang = Math.atan2(endY - cyp, endX - cxp);
    ctx.beginPath(); ctx.moveTo(endX, endY);
    ctx.lineTo(endX - 16 * Math.cos(ang - 0.45), endY - 16 * Math.sin(ang - 0.45));
    ctx.lineTo(endX - 16 * Math.cos(ang + 0.45), endY - 16 * Math.sin(ang + 0.45)); ctx.closePath();
    ctx.fillStyle = '#F0567A'; ctx.fill();
    ctx.restore();
  }
  function drawCheck(c) {
    const age = t - c.t0, k = Math.min(1, age / 0.5);
    if (age > 1.4) return false;
    ctx.save();
    ctx.globalAlpha = age < 1 ? 1 : 1 - (age - 1) / 0.4;
    ctx.beginPath(); ctx.arc(c.x, c.y, 26 * (0.6 + 0.4 * k), 0, Math.PI * 2);
    ctx.fillStyle = 'rgba(78,210,140,0.9)'; ctx.fill();
    ctx.strokeStyle = '#fff'; ctx.lineWidth = 4; ctx.lineCap = 'round';
    ctx.beginPath(); ctx.moveTo(c.x - 11, c.y); ctx.lineTo(c.x - 3, c.y + 8 * k); ctx.lineTo(c.x + 12 * k, c.y - 9 * k); ctx.stroke();
    for (let n = 0; n < 8; n++) {
      const a = (n / 8) * Math.PI * 2, r = 30 + age * 60;
      ctx.beginPath(); ctx.arc(c.x + Math.cos(a) * r, c.y + Math.sin(a) * r, 3 * (1 - k * 0.7), 0, Math.PI * 2);
      ctx.fillStyle = 'rgba(255,209,102,' + (1 - age / 1.4) + ')'; ctx.fill();
    }
    ctx.restore();
    return true;
  }

  function frame(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now; t += dt;
    ctx.clearRect(0, 0, W, H);
    for (const i of indicators) {
      const age = t - i.born;
      if (i.kind === 'box' || i.kind === 'region') drawBox(i, age);
      else if (i.kind === 'ring') drawRing(i, age);
    }
    if (arrowTo) drawArrow(arrowTo, t - (indicators[0] ? indicators[0].born : t));
    checks = checks.filter(drawCheck);
    if (mode === 'idle' && !checks.length) setTimeout(() => requestAnimationFrame(frame), 120);
    else requestAnimationFrame(frame);
  }
  requestAnimationFrame(frame);
})();
