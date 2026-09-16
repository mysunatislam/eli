// Eli's heart: a small animated character with a personality.
//
// State machine (driven by the backend):  IDLE  LISTENING  THINKING  SPEAKING  EXECUTING  ERROR  SUCCESS
// plus SLEEPING when the backend is unreachable. ERROR and SUCCESS are short "moods" layered over the
// current state (confused / excited), so the underlying state keeps flowing.
// Personality: breathing, blinking, eyes that follow the pointer and glance around, small gestures
// (nod on success, shrug on error, lean forward while executing), pulse rings while listening.
class Heart {
  constructor(canvas) {
    this.cv = canvas;
    this.ctx = canvas.getContext('2d');
    this.size = 160;
    this.dpr = window.devicePixelRatio || 1;
    this.state = 'idle';
    this.mood = null;          // { name, until }
    this.offline = true;       // sleeping until the backend socket opens
    this.observing = false;
    this.mic = false;
    this.t = 0;
    this.blink = 0;
    this.nextBlink = 2 + Math.random() * 3;
    this.look = { x: 0, y: 0 };
    this.lookTarget = { x: 0, y: 0 };
    this.pointer = { x: 0, y: 0, near: false };
    this.glanceUntil = 0;
    this.nextGlance = 4 + Math.random() * 5;
    this.pulses = [];
    this.lastPulse = 0;
    this.zs = [];
    this.sparks = [];
    this.gesture = null;       // { name, t0, dur }
    this.reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    this.colors = { accent: '#F0567A', light: '#FF7A98', dark: '#C93E5E', teal: '#2DC8C4', rec: '#FF3B30', pupil: '#231533', gold: '#FFD166' };
    this.shape = 'heart';
    this.persona = null;
    this.frames = 0;
    this.onFirstFrame = null;
    this.resize();
    this.last = performance.now();
    this._raf = (now) => this.frame(now);
    requestAnimationFrame(this._raf);
  }

  resize() {
    this.cv.width = this.size * this.dpr;
    this.cv.height = this.size * this.dpr;
    this.cv.style.width = this.cv.style.height = this.size + 'px';
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
  }

  // ---------- persona API ----------
  setPersona(p) {
    if (!p) return;
    this.persona = p;
    this.shape = p.shape || 'heart';
    if (p.color) this.colors.accent = p.color;
    if (p.color_light) this.colors.light = p.color_light;
    if (p.color_dark) this.colors.dark = p.color_dark;
  }

  // ---------- state API ----------
  setState(name) {
    const s = String(name || 'idle').toLowerCase();
    const map = { speaking: 'talking', happy: 'success', working: 'executing', acting: 'executing', confused: 'error' };
    const st = map[s] || s;
    if (st === 'success') { this.setMood('success', 2.6); this.gesture = { name: 'nod', t0: this.t, dur: 0.9 }; this.spark(); return; }
    if (st === 'error') { this.setMood('error', 4.0); this.gesture = { name: 'shrug', t0: this.t, dur: 1.1 }; return; }
    if (['idle', 'listening', 'thinking', 'talking', 'executing', 'sleeping'].includes(st)) this.state = st;
    if (st === 'executing') this.gesture = { name: 'lean', t0: this.t, dur: 0.6 };
  }
  setMood(name, seconds) { this.mood = { name, until: this.t + seconds }; }
  effectiveState() {
    if (this.offline) return 'sleeping';
    if (this.mood && this.t < this.mood.until) return this.mood.name;
    return this.state;
  }
  pulse() { this.pulses.push(this.t); }
  wave() { this.gesture = { name: 'wave', t0: this.t, dur: 1.2 }; this.pulse(); }
  spark() {
    for (let i = 0; i < 7; i++) {
      const a = Math.random() * Math.PI * 2, d = 46 + Math.random() * 26;
      this.sparks.push({ x: Math.cos(a) * d, y: Math.sin(a) * d * 0.8, t0: this.t + Math.random() * 0.3, r: 3 + Math.random() * 3 });
    }
  }
  lookAt(dx, dy) {
    const len = Math.hypot(dx, dy) || 1;
    this.pointer = { x: dx, y: dy, near: len < 260 };
    if (this.pointer.near) {
      const k = Math.min(1, len / 140);
      this.lookTarget = { x: (dx / len) * k, y: (dy / len) * k };
    }
  }

  // ---------- loop ----------
  frame(now) {
    const dt = Math.min(0.05, (now - this.last) / 1000);
    this.last = now;
    this.t += dt;
    const st = this.effectiveState();

    // blinking
    this.nextBlink -= dt;
    if (this.nextBlink <= 0) {
      this.blink = 0.15;
      this.nextBlink = (Math.random() < 0.2 ? 0.35 : 2.4 + Math.random() * 3.6); // occasional double blink
    }
    if (this.blink > 0) this.blink -= dt;

    // glancing around when nobody is pointing at it
    if (!this.pointer.near) {
      this.nextGlance -= dt;
      if (this.nextGlance <= 0) {
        this.lookTarget = { x: (Math.random() - 0.5) * 1.6, y: (Math.random() - 0.5) * 0.9 };
        this.glanceUntil = this.t + 0.9 + Math.random() * 1.1;
        this.nextGlance = 3.5 + Math.random() * 6;
      }
      if (this.t > this.glanceUntil) this.lookTarget = { x: this.lookTarget.x * 0.9, y: this.lookTarget.y * 0.9 };
    }
    const lk = Math.min(1, dt * 7);
    this.look.x += (this.lookTarget.x - this.look.x) * lk;
    this.look.y += (this.lookTarget.y - this.look.y) * lk;

    if (st === 'listening' && this.t - this.lastPulse > 0.9) { this.pulses.push(this.t); this.lastPulse = this.t; }
    if (st === 'sleeping' && (this.zs.length === 0 || this.t - this.zs[this.zs.length - 1].t > 1.4)) this.zs.push({ t: this.t });
    this.pulses = this.pulses.filter((p) => this.t - p < 1.4);
    this.zs = this.zs.filter((z) => this.t - z.t < 2.6);
    this.sparks = this.sparks.filter((s) => this.t - s.t0 < 1.1);
    if (this.gesture && this.t - this.gesture.t0 > this.gesture.dur) this.gesture = null;

    this.draw(st);
    this.frames++;
    if (this.frames === 2 && this.onFirstFrame) this.onFirstFrame();

    // 60 fps while awake; ~15 fps while sleeping or hidden to stay light on the CPU
    if (st === 'sleeping' || document.hidden) setTimeout(() => requestAnimationFrame(this._raf), 66);
    else requestAnimationFrame(this._raf);
  }

  hexToRgba(hex, alpha) {
    const h = (hex || '#F0567A').replace('#', '');
    const r = parseInt(h.substring(0, 2), 16) || 240;
    const g = parseInt(h.substring(2, 4), 16) || 86;
    const b = parseInt(h.substring(4, 6), 16) || 122;
    return `rgba(${r},${g},${b},${alpha})`;
  }

  heartPath(s) {
    const c = this.ctx;
    c.beginPath();
    c.moveTo(0, 0.62 * s);
    c.bezierCurveTo(-0.15 * s, 0.45 * s, -1.02 * s, 0.05 * s, -1.0 * s, -0.35 * s);
    c.bezierCurveTo(-0.98 * s, -0.78 * s, -0.45 * s, -0.92 * s, 0, -0.45 * s);
    c.bezierCurveTo(0.45 * s, -0.92 * s, 0.98 * s, -0.78 * s, 1.0 * s, -0.35 * s);
    c.bezierCurveTo(1.02 * s, 0.05 * s, 0.15 * s, 0.45 * s, 0, 0.62 * s);
    c.closePath();
  }

  shieldPath(s) {
    const c = this.ctx;
    c.beginPath();
    c.moveTo(0, -0.8 * s);
    c.lineTo(0.72 * s, -0.72 * s);
    c.bezierCurveTo(0.88 * s, -0.72 * s, 0.92 * s, -0.55 * s, 0.92 * s, -0.35 * s);
    c.lineTo(0.88 * s, 0.1 * s);
    c.bezierCurveTo(0.82 * s, 0.5 * s, 0.45 * s, 0.8 * s, 0, 0.92 * s);
    c.bezierCurveTo(-0.45 * s, 0.8 * s, -0.82 * s, 0.5 * s, -0.88 * s, 0.1 * s);
    c.lineTo(-0.92 * s, -0.35 * s);
    c.bezierCurveTo(-0.92 * s, -0.55 * s, -0.88 * s, -0.72 * s, -0.72 * s, -0.72 * s);
    c.closePath();
  }

  crestPath(s) {
    const c = this.ctx;
    c.beginPath();
    c.moveTo(0, -0.85 * s);
    c.bezierCurveTo(0.55 * s, -0.85 * s, 0.88 * s, -0.55 * s, 0.82 * s, -0.15 * s);
    c.bezierCurveTo(0.75 * s, 0.2 * s, 0.85 * s, 0.5 * s, 0, 0.85 * s);
    c.bezierCurveTo(-0.85 * s, 0.5 * s, -0.75 * s, 0.2 * s, -0.82 * s, -0.15 * s);
    c.bezierCurveTo(-0.88 * s, -0.55 * s, -0.55 * s, -0.85 * s, 0, -0.85 * s);
    c.closePath();
  }

  sparkPath(s) {
    const c = this.ctx;
    c.beginPath();
    c.moveTo(0, -0.92 * s);
    c.bezierCurveTo(0.22 * s, -0.28 * s, 0.28 * s, -0.22 * s, 0.92 * s, 0);
    c.bezierCurveTo(0.28 * s, 0.22 * s, 0.22 * s, 0.28 * s, 0, 0.92 * s);
    c.bezierCurveTo(-0.22 * s, 0.28 * s, -0.28 * s, 0.22 * s, -0.92 * s, 0);
    c.bezierCurveTo(-0.28 * s, -0.22 * s, -0.22 * s, -0.28 * s, 0, -0.92 * s);
    c.closePath();
  }

  compassPath(s) {
    const c = this.ctx;
    c.beginPath();
    const r = 0.78 * s;
    c.arc(0, 0, r, 0, Math.PI * 2);
    c.closePath();
  }

  drawBodyShape(s) {
    const sh = this.shape || 'heart';
    if (sh === 'shield') this.shieldPath(s);
    else if (sh === 'crest') this.crestPath(s);
    else if (sh === 'spark') this.sparkPath(s);
    else if (sh === 'compass') this.compassPath(s);
    else this.heartPath(s);
  }

  draw(st) {
    const c = this.ctx, t = this.t, S = this.size;
    c.clearRect(0, 0, S, S);
    const cx = S / 2, cy = S / 2 + 6;
    const s = 40;
    const anim = !this.reduced;

    // rings
    for (const p of this.pulses) {
      const k = (t - p) / 1.4;
      c.beginPath();
      c.arc(cx, cy, s * (1.15 + k * 0.9), 0, Math.PI * 2);
      c.strokeStyle = this.hexToRgba(this.colors.accent, (1 - k) * 0.55);
      c.lineWidth = 2;
      c.stroke();
    }
    if (this.mic) {
      c.beginPath();
      c.arc(cx, cy, s * 1.32 + (anim ? Math.sin(t * 6) * 2 : 0), 0, Math.PI * 2);
      c.strokeStyle = 'rgba(255,59,48,0.85)';
      c.lineWidth = 3;
      c.stroke();
    }
    // thinking dots (slow orbit)
    if (st === 'thinking') {
      for (let i = 0; i < 3; i++) {
        const a = t * 1.6 + (i * Math.PI * 2) / 3;
        c.beginPath();
        c.arc(cx + Math.cos(a) * s * 1.45, cy + Math.sin(a) * s * 1.05, s * 0.085, 0, Math.PI * 2);
        c.fillStyle = this.colors.teal;
        c.globalAlpha = 0.55 + 0.45 * Math.sin(t * 4 + i);
        c.fill();
        c.globalAlpha = 1;
      }
    }
    // executing: a small spinning "work" ring of ticks at the top-right
    if (st === 'executing') {
      const ox = cx + s * 1.15, oy = cy - s * 0.95;
      for (let i = 0; i < 8; i++) {
        const a = t * 4 + (i * Math.PI) / 4;
        c.beginPath();
        c.moveTo(ox + Math.cos(a) * 5, oy + Math.sin(a) * 5);
        c.lineTo(ox + Math.cos(a) * 9, oy + Math.sin(a) * 9);
        c.strokeStyle = this.colors.teal;
        c.globalAlpha = 0.25 + 0.75 * ((i + Math.floor(t * 8)) % 8) / 8;
        c.lineWidth = 2;
        c.lineCap = 'round';
        c.stroke();
        c.globalAlpha = 1;
      }
    }
    // success sparkles
    for (const sp of this.sparks) {
      const k = Math.max(0, Math.min(1, (t - sp.t0) / 1.1));
      if (k <= 0) continue;
      const r = sp.r * (1 - k) + 0.5;
      c.save();
      c.translate(cx + sp.x, cy + sp.y - k * 18);
      c.rotate(k * 2);
      c.beginPath();
      for (let i = 0; i < 4; i++) {
        const a = (i * Math.PI) / 2;
        c.lineTo(Math.cos(a) * r * 2.2, Math.sin(a) * r * 2.2);
        c.lineTo(Math.cos(a + Math.PI / 4) * r * 0.7, Math.sin(a + Math.PI / 4) * r * 0.7);
      }
      c.closePath();
      c.fillStyle = this.colors.gold;
      c.globalAlpha = 1 - k;
      c.fill();
      c.globalAlpha = 1;
      c.restore();
    }

    c.save();
    c.translate(cx, cy);
    // motion: breathing, bob, lean, gestures
    let scale = 1, rot = 0, bob = 0;
    if (anim) {
      if (st === 'idle' || st === 'listening') { scale = 1 + 0.03 * Math.sin(t * 2.2); bob = Math.sin(t * 1.3) * 2; }
      if (st === 'thinking') { scale = 1 + 0.05 * Math.sin(t * 1.9); rot = Math.sin(t * 1.2) * 0.05; }   // slow pulse
      if (st === 'talking') { scale = 1 + 0.035 * Math.abs(Math.sin(t * 7)); bob = Math.sin(t * 7) * 1.5; }
      if (st === 'executing') { scale = 1 + 0.02 * Math.sin(t * 4); rot = 0.09; bob = -3; }
      if (st === 'success') { scale = 1 + 0.07 * Math.abs(Math.sin(t * 6)); bob = -Math.abs(Math.sin(t * 6)) * 7; rot = Math.sin(t * 6) * 0.06; }
      if (st === 'error') { rot = -0.16 + Math.sin(t * 9) * 0.02; bob = 2; }
      if (st === 'sleeping') { scale = 1 + 0.015 * Math.sin(t * 1.2); bob = 2; }
      if (this.gesture) {
        const k = (t - this.gesture.t0) / this.gesture.dur;
        if (this.gesture.name === 'nod') bob += Math.sin(k * Math.PI * 2) * 5;
        if (this.gesture.name === 'shrug') { rot += Math.sin(k * Math.PI) * -0.12; bob -= Math.sin(k * Math.PI) * 4; }
        if (this.gesture.name === 'wave') { rot += Math.sin(k * Math.PI * 4) * 0.18 * (1 - k); }
        if (this.gesture.name === 'lean') { rot += (1 - k) * 0.06; }
      }
    }
    c.translate(0, bob);
    c.rotate(rot);
    c.scale(scale, scale);
    if (st === 'sleeping') c.globalAlpha = 0.62;

    // glow + body
    c.shadowColor = st === 'sleeping' ? 'rgba(0,0,0,0)' : (st === 'success' ? 'rgba(255,209,102,0.7)' : this.hexToRgba(this.colors.accent, 0.55));
    c.shadowBlur = st === 'thinking' || st === 'listening' || st === 'success' ? 22 : 14;
    const g = c.createLinearGradient(-s, -s, s, s);
    g.addColorStop(0, this.colors.light);
    g.addColorStop(0.55, this.colors.accent);
    g.addColorStop(1, this.colors.dark);
    this.drawBodyShape(s);
    c.fillStyle = g;
    c.fill();
    c.shadowBlur = 0;
    c.beginPath();
    c.ellipse(-0.45 * s, -0.5 * s, 0.22 * s, 0.13 * s, -0.6, 0, Math.PI * 2);
    c.fillStyle = 'rgba(255,255,255,0.35)';
    c.fill();

    this.drawFace(s, st);
    c.restore();

    // observing dot (recording indicator)
    if (this.observing) {
      const a = anim ? 0.6 + 0.4 * Math.sin(t * 3) : 1;
      c.beginPath();
      c.arc(cx + s * 1.05, cy - s * 0.85, s * 0.15, 0, Math.PI * 2);
      c.fillStyle = `rgba(255,59,48,${a})`;
      c.fill();
      c.lineWidth = 2;
      c.strokeStyle = 'rgba(255,255,255,0.9)';
      c.stroke();
    }
    // sleeping z's
    if (st === 'sleeping') {
      c.font = 'bold 13px "Segoe UI", sans-serif';
      for (const z of this.zs) {
        const k = (t - z.t) / 2.6;
        c.globalAlpha = 1 - k;
        c.fillStyle = '#B7BFCF';
        c.fillText('z', cx + s * 0.9 + k * 14, cy - s * 0.9 - k * 34);
      }
      c.globalAlpha = 1;
    }
    // confused "?"
    if (st === 'error') {
      c.font = 'bold 20px "Segoe UI", sans-serif';
      c.fillStyle = this.colors.teal;
      c.globalAlpha = 0.85;
      c.fillText('?', cx + s * 0.95, cy - s * 0.95 + Math.sin(t * 3) * 3);
      c.globalAlpha = 1;
    }
  }

  drawFace(s, st) {
    const c = this.ctx;
    const ex = 0.36 * s, ey = -0.22 * s;
    const open = this.blink > 0 ? 0.08 : 1;
    const lookX = this.look.x * 0.07 * s, lookY = this.look.y * 0.05 * s;
    c.lineCap = 'round';

    if (st === 'sleeping') {
      c.strokeStyle = this.colors.pupil;
      c.lineWidth = 0.07 * s;
      for (const sx of [-1, 1]) { c.beginPath(); c.arc(sx * ex, ey - 0.02 * s, 0.14 * s, 0.15 * Math.PI, 0.85 * Math.PI); c.stroke(); }
      return;
    }
    if (st === 'success') {
      c.strokeStyle = this.colors.pupil;
      c.lineWidth = 0.075 * s;
      for (const sx of [-1, 1]) { c.beginPath(); c.arc(sx * ex, ey + 0.06 * s, 0.15 * s, 1.15 * Math.PI, 1.85 * Math.PI); c.stroke(); }
      c.beginPath(); c.arc(0, 0.0 * s, 0.2 * s, 0.12 * Math.PI, 0.88 * Math.PI); c.stroke();
      for (const sx of [-1, 1]) { c.beginPath(); c.ellipse(sx * 0.62 * s, -0.02 * s, 0.13 * s, 0.08 * s, 0, 0, Math.PI * 2); c.fillStyle = 'rgba(255,255,255,0.35)'; c.fill(); }
      return;
    }

    // eye whites (asymmetric when confused)
    let ryL = 0.2 * s * open, ryR = 0.2 * s * open;
    if (st === 'listening') { ryL *= 1.15; ryR *= 1.15; }
    if (st === 'thinking') { ryL *= 0.8; ryR *= 0.8; }
    if (st === 'executing') { ryL *= 0.7; ryR *= 0.7; }
    if (st === 'error') { ryL *= 1.1; ryR *= 0.65; }
    c.fillStyle = '#FFFFFF';
    c.beginPath(); c.ellipse(-ex, ey, 0.16 * s, ryL, 0, 0, Math.PI * 2); c.fill();
    c.beginPath(); c.ellipse(ex, ey, 0.16 * s, ryR, 0, 0, Math.PI * 2); c.fill();

    // pupils
    let px = lookX, py = lookY, inward = 0;
    if (st === 'thinking') { px = 0.06 * s; py = -0.08 * s; }
    if (st === 'executing') { px = 0.05 * s; py = 0.07 * s; }
    if (st === 'error') { px = 0; py = -0.02 * s; inward = 0.05 * s; }
    if (st === 'talking') { px *= 0.5; py *= 0.5; }
    if (open > 0.2) {
      for (const sx of [-1, 1]) {
        c.beginPath();
        c.arc(sx * ex + px - sx * inward, ey + py, 0.085 * s, 0, Math.PI * 2);
        c.fillStyle = this.colors.pupil;
        c.fill();
        c.beginPath();
        c.arc(sx * ex + px - sx * inward - 0.03 * s, ey + py - 0.035 * s, 0.028 * s, 0, Math.PI * 2);
        c.fillStyle = '#fff';
        c.fill();
      }
    }
    // eyebrows while executing (focused) and confused
    if (st === 'executing' || st === 'error') {
      c.strokeStyle = this.colors.pupil;
      c.lineWidth = 0.055 * s;
      for (const sx of [-1, 1]) {
        c.beginPath();
        const tilt = st === 'executing' ? -0.06 * s * sx : (sx < 0 ? -0.09 * s : 0.02 * s);
        c.moveTo(sx * (ex - 0.14 * s), ey - 0.32 * s + tilt);
        c.lineTo(sx * (ex + 0.14 * s), ey - 0.32 * s - tilt);
        c.stroke();
      }
    }
    // mouth
    c.strokeStyle = this.colors.pupil;
    c.lineWidth = 0.06 * s;
    if (st === 'talking') {
      const h = 0.05 * s + 0.11 * s * Math.abs(Math.sin(this.t * 13));
      c.beginPath(); c.ellipse(0, 0.08 * s, 0.13 * s, h, 0, 0, Math.PI * 2); c.fillStyle = this.colors.pupil; c.fill();
    } else if (st === 'thinking') {
      c.beginPath(); c.moveTo(-0.1 * s, 0.1 * s); c.lineTo(0.12 * s, 0.07 * s); c.stroke();
    } else if (st === 'executing') {
      c.beginPath(); c.moveTo(-0.09 * s, 0.09 * s); c.lineTo(0.09 * s, 0.09 * s); c.stroke();
    } else if (st === 'error') {
      c.beginPath();
      for (let i = 0; i <= 8; i++) { const x = -0.14 * s + (i / 8) * 0.28 * s; const y = 0.1 * s + Math.sin(i * 1.6) * 0.025 * s; if (i === 0) c.moveTo(x, y); else c.lineTo(x, y); }
      c.stroke();
    } else {
      c.beginPath(); c.arc(0, 0.0 * s, 0.13 * s, 0.2 * Math.PI, 0.8 * Math.PI); c.stroke();
    }
  }
}
window.Heart = Heart;
