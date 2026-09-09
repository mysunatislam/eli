// Eli desktop overlay (main process).
// A transparent, frameless, always-on-top window that is click-through everywhere except over the
// heart, the panel, bubbles and dialogs. It can glide around the screen on its own (movement
// engine below). All intelligence lives in the Python backend; this file only manages the window.
//
// Env flags:  ELI_DEBUG=1 (verbose logs)   ELI_DEVTOOLS=1 (open DevTools)
//             ELI_NO_GPU=1 (disable GPU compositing; fixes invisible transparent windows on some drivers)
//             ELI_OPAQUE=1 (solid window instead of transparency, last-resort fallback)
const { app, BrowserWindow, ipcMain, screen, Tray, Menu, globalShortcut, nativeImage, shell, Notification } = require('electron');
const path = require('path');
const fs = require('fs');

const WIN_W = 420;
const WIN_H = 620;
const HEART = { x: 332, y: 538 };          // heart centre inside the window (matches renderer layout)
const BACKEND_WS = process.env.ELI_BACKEND_URL || 'ws://127.0.0.1:8790/ws/desktop';
const BACKEND_HTTP = process.env.ELI_BACKEND_HTTP || 'http://127.0.0.1:8790';
const DEBUG = !!process.env.ELI_DEBUG;
const OPAQUE = !!process.env.ELI_OPAQUE;

if (process.env.ELI_NO_GPU) app.disableHardwareAcceleration();
app.setAppUserModelId('app.eli.companion');

let win = null;
let guideWin = null;        // full-screen, click-through layer for step indicators
let guideHideTimer = null;
let tray = null;
let home = null;            // where the heart rests (updated when the user drags it)
let glideTimer = null;
let attentionTimer = null;
let painted = false;
let hitRegions = [];
let lastOver = null;
let panelOpen = false;       // any UI (quick bar or full panel) is open
let follow = true;           // trail the cursor (synced from the backend's follow_cursor setting)
let dragging = false;
let lastFollow = 0;
const FOLLOW_DIST = 380;     // start moving when the cursor is this far from the heart
const FOLLOW_OFFSET = { x: 110, y: 90 };   // rest a little below-right of the pointer

const log = (...a) => console.log('[eli]', ...a);
const dbg = (...a) => { if (DEBUG) console.log('[eli:debug]', ...a); };

const gotLock = app.requestSingleInstanceLock();
if (!gotLock) app.quit();

// ---------- geometry helpers -----------------------------------------------------------------
function displayForCursor() {
  try { return screen.getDisplayNearestPoint(screen.getCursorScreenPoint()); } catch (_) { return screen.getPrimaryDisplay(); }
}
function displayForWindow() {
  if (!win) return displayForCursor();
  const b = win.getBounds();
  return screen.getDisplayMatching(b) || screen.getPrimaryDisplay();
}
function cornerOf(disp) {
  const wa = disp.workArea;
  return { x: wa.x + wa.width - WIN_W - 8, y: wa.y + wa.height - WIN_H - 8 };
}
// Keep the heart itself on screen (the transparent panel area may hang off the edge when closed).
function clampHeart(x, y, disp) {
  const wa = (disp || displayForWindow()).workArea;
  const hx = Math.min(Math.max(x + HEART.x, wa.x + 50), wa.x + wa.width - 50);
  const hy = Math.min(Math.max(y + HEART.y, wa.y + 50), wa.y + wa.height - 50);
  return { x: Math.round(hx - HEART.x), y: Math.round(hy - HEART.y) };
}
// Keep the whole window on screen (used while the panel is open).
function clampWindow(x, y, disp) {
  const wa = (disp || displayForWindow()).workArea;
  return {
    x: Math.round(Math.min(Math.max(x, wa.x), wa.x + wa.width - WIN_W)),
    y: Math.round(Math.min(Math.max(y, wa.y), wa.y + wa.height - WIN_H)),
  };
}

// ---------- movement engine ---------------------------------------------------------------------
function glideTo(x, y, ms) {
  if (!win) return;
  const [sx, sy] = win.getPosition();
  const dur = Math.max(80, ms || 600);
  const t0 = Date.now();
  if (glideTimer) clearInterval(glideTimer);
  const ease = (k) => (k < 0.5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2);
  glideTimer = setInterval(() => {
    if (!win || win.isDestroyed()) { clearInterval(glideTimer); glideTimer = null; return; }
    const k = Math.min(1, (Date.now() - t0) / dur);
    const e = ease(k);
    win.setPosition(Math.round(sx + (x - sx) * e), Math.round(sy + (y - sy) * e));
    if (k >= 1) { clearInterval(glideTimer); glideTimer = null; }
  }, 16);
}
function glideHome(ms) {
  if (!home) home = cornerOf(displayForWindow());
  const p = panelOpen ? clampWindow(home.x, home.y) : clampHeart(home.x, home.y);
  glideTo(p.x, p.y, ms || 700);
}
// Move the heart next to a screen rectangle (e.g. the window Eli is working in), then come back.
function attend(rect, ms) {
  if (!win || !rect || rect.length < 4) return;
  if (panelOpen) return; // don't move the panel out from under the user's cursor
  const [l, t, r] = rect;
  const disp = displayForWindow();
  const scale = disp.scaleFactor || 1;                        // rect arrives in physical pixels
  const hx = (r / scale) - 70, hy = (t / scale) + 40;         // just inside the top-right corner
  const p = clampHeart(hx - HEART.x, hy - HEART.y, disp);
  glideTo(p.x, p.y, 650);
  if (attentionTimer) clearTimeout(attentionTimer);
  attentionTimer = setTimeout(() => glideHome(900), ms || 12000);
}

// ---------- window --------------------------------------------------------------------------------
function createWindow() {
  const disp = displayForCursor();
  const start = cornerOf(disp);
  home = { ...start };
  win = new BrowserWindow({
    ...start,
    width: WIN_W,
    height: WIN_H,
    transparent: !OPAQUE,
    backgroundColor: OPAQUE ? '#141824' : '#00000000',
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    hasShadow: false,
    show: false,
    title: 'Eli',
    icon: path.join(__dirname, 'assets', 'heart-256.png'),
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      backgroundThrottling: false,
    },
  });
  win.setAlwaysOnTop(true, 'screen-saver');
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  win.setMenuBarVisibility(false);
  win.setIgnoreMouseEvents(!OPAQUE, { forward: true });
  win.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  // ready-to-show is not always emitted for transparent windows: show on a timer as well.
  let shown = false;
  const show = () => {
    if (shown || !win) return;
    shown = true;
    win.showInactive();
    const b = win.getBounds();
    log(`overlay shown at ${b.x},${b.y} ${b.width}x${b.height} on display ${disp.id} (scale ${disp.scaleFactor}); transparent=${!OPAQUE}`);
    firstRunNotice();
  };
  win.once('ready-to-show', show);
  setTimeout(show, 2500);

  win.on('blur', () => { if (win && !win.isDestroyed()) win.setAlwaysOnTop(true, 'screen-saver'); });
  win.on('closed', () => { win = null; });

  win.webContents.on('console-message', (e, level, message) => {
    const msg = typeof message === 'string' ? message : (e && e.message) || '';
    const lvl = typeof level === 'number' ? level : (e && e.level) || 0;
    const serious = lvl === 2 || lvl === 3 || lvl === 'warning' || lvl === 'error';
    if (msg && (serious || DEBUG)) console.log('[renderer]', msg);
  });
  win.webContents.on('render-process-gone', (_e, details) => log('renderer gone:', details.reason));
  if (process.env.ELI_DEVTOOLS) win.webContents.openDevTools({ mode: 'detach' });

  setTimeout(() => {
    if (!painted) {
      log('WARNING: the renderer has not painted after 6 s. If you cannot see the heart, restart with '
        + 'ELI_NO_GPU=1 (or ELI_OPAQUE=1) — see README "Troubleshooting".');
    }
  }, 6000);
  // Some apps steal the top-most slot; re-assert it every 20 s (cheap).
  setInterval(() => { if (win && !win.isDestroyed()) win.setAlwaysOnTop(true, 'screen-saver'); }, 20000);
}

function firstRunNotice() {
  try {
    const flag = path.join(app.getPath('userData'), 'first-run-done');
    if (fs.existsSync(flag)) return;
    fs.writeFileSync(flag, String(Date.now()));
    if (Notification.isSupported()) {
      new Notification({
        title: 'Eli is on your desktop',
        body: 'Look for the heart at the bottom-right corner. Click it, drag it, or press Ctrl+Shift+E.',
        icon: path.join(__dirname, 'assets', 'heart-256.png'),
      }).show();
    }
  } catch (_) { /* ignore */ }
}

// ---------- cursor following ----------------------------------------------------------------------------
// The heart trails the pointer like a companion: it stays put while you work nearby and glides over
// once you've moved far away, settling below-right of the cursor (never under it).
function followStep(p) {
  if (!follow || panelOpen || dragging || attentionTimer || glideTimer) return;
  if (Date.now() - lastFollow < 700) return;
  const [wx, wy] = win.getPosition();
  const hx = wx + HEART.x, hy = wy + HEART.y;
  if (Math.hypot(p.x - hx, p.y - hy) < FOLLOW_DIST) return;
  const disp = screen.getDisplayNearestPoint(p);
  const wa = disp.workArea;
  const ox = (p.x + FOLLOW_OFFSET.x + 60 > wa.x + wa.width) ? -FOLLOW_OFFSET.x : FOLLOW_OFFSET.x;
  const oy = (p.y + FOLLOW_OFFSET.y + 60 > wa.y + wa.height) ? -FOLLOW_OFFSET.y : FOLLOW_OFFSET.y;
  const t = clampHeart(p.x + ox - HEART.x, p.y + oy - HEART.y, disp);
  lastFollow = Date.now();
  home = { x: t.x, y: t.y };
  glideTo(t.x, t.y, 520);
  dbg('follow ->', t.x, t.y);
}

// ---------- click-through management ------------------------------------------------------------------
function pollCursor() {
  if (!win || win.isDestroyed()) return;
  try {
    const p = screen.getCursorScreenPoint();
    const [wx, wy] = win.getPosition();
    const x = p.x - wx, y = p.y - wy;
    if (!OPAQUE) {
      const over = hitRegions.some((r) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h);
      if (over !== lastOver) {
        lastOver = over;
        win.setIgnoreMouseEvents(!over, { forward: true });
        dbg('hit', over, x, y);
      }
    }
    win.webContents.send('cursor', x, y);
    followStep(p);
  } catch (_) { /* window closing */ }
}

// ---------- guide overlay (full-screen indicator layer) ---------------------------------------------------
function ensureGuideWindow(disp) {
  if (guideWin && !guideWin.isDestroyed()) {
    if (disp) guideWin.setBounds(disp.bounds);
    return guideWin;
  }
  const d = disp || screen.getPrimaryDisplay();
  guideWin = new BrowserWindow({
    ...d.bounds,
    transparent: !OPAQUE,
    backgroundColor: '#00000000',
    frame: false,
    alwaysOnTop: true,
    skipTaskbar: true,
    focusable: false,
    resizable: false,
    hasShadow: false,
    show: false,
    title: 'Eli guide',
    webPreferences: { preload: path.join(__dirname, 'guide-preload.js'), contextIsolation: true, nodeIntegration: false, backgroundThrottling: false },
  });
  guideWin.setAlwaysOnTop(true, 'screen-saver');
  guideWin.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  guideWin.setIgnoreMouseEvents(true);
  guideWin.setMenuBarVisibility(false);
  guideWin.loadFile(path.join(__dirname, 'guide', 'guide.html'));
  guideWin.on('closed', () => { guideWin = null; });
  guideWin.webContents.on('console-message', (e, level, message) => {
    const msg = typeof message === 'string' ? message : (e && e.message) || '';
    if (msg && DEBUG) console.log('[guide]', msg);
  });
  return guideWin;
}
// Backend indicator coordinates are physical screen pixels; convert to the guide window's DIP space.
function forwardGuide(m) {
  const inds = Array.isArray(m.indicators) ? m.indicators : [];
  const first = inds.find((i) => typeof i.x === 'number');
  const disp = first ? screen.getDisplayNearestPoint({ x: Math.round(first.x / (screen.getPrimaryDisplay().scaleFactor || 1)), y: Math.round(first.y / (screen.getPrimaryDisplay().scaleFactor || 1)) })
                     : (win ? displayForWindow() : screen.getPrimaryDisplay());
  const gw = ensureGuideWindow(disp);
  const sf = disp.scaleFactor || 1;
  const conv = inds.map((i) => {
    const o = { ...i };
    if (typeof i.x === 'number') o.x = i.x / sf - disp.bounds.x;
    if (typeof i.y === 'number') o.y = i.y / sf - disp.bounds.y;
    if (typeof i.w === 'number') o.w = i.w / sf;
    if (typeof i.h === 'number') o.h = i.h / sf;
    if (typeof i.r === 'number') o.r = i.r / sf;
    return o;
  });
  const payload = { ...m, indicators: conv };
  const send = () => { if (gw && !gw.isDestroyed()) gw.webContents.send('guide', payload); };
  if (gw.webContents.isLoading()) gw.webContents.once('did-finish-load', send); else send();
  clearTimeout(guideHideTimer);
  if (m.action === 'clear') {
    guideHideTimer = setTimeout(() => { if (gw && !gw.isDestroyed()) gw.hide(); }, 400);
  } else if (m.action === 'complete') {
    guideHideTimer = setTimeout(() => { if (gw && !gw.isDestroyed()) gw.hide(); }, 6000);
  } else if (!gw.isVisible() && m.action !== 'speaking') {
    gw.showInactive();
  }
  dbg('guide', m.action, conv.length, 'indicators');
}
ipcMain.on('guide-event', (_e, m) => { try { forwardGuide(m || {}); } catch (err) { log('guide event failed', err); } });
ipcMain.on('guide-state', (_e, s) => { if (guideWin && !guideWin.isDestroyed()) guideWin.webContents.send('guide-state', s); });

// ---------- IPC ---------------------------------------------------------------------------------------
ipcMain.on('set-hit-regions', (_e, regions) => { hitRegions = Array.isArray(regions) ? regions : []; });
ipcMain.on('set-ignore-mouse', (_e, ignore) => { if (win && !OPAQUE) win.setIgnoreMouseEvents(!!ignore, { forward: true }); lastOver = null; });
ipcMain.on('move-window', (_e, dx, dy) => {
  if (!win) return;
  if (glideTimer) { clearInterval(glideTimer); glideTimer = null; }
  const [x, y] = win.getPosition();
  win.setPosition(Math.round(x + dx), Math.round(y + dy));
});
ipcMain.on('drag-start', () => { dragging = true; });
ipcMain.on('drag-end', () => { dragging = false; if (win) { const [x, y] = win.getPosition(); home = { x, y }; } });
ipcMain.on('set-follow', (_e, on) => { follow = !!on; dbg('follow mode', follow); });
ipcMain.on('glide-to', (_e, x, y, ms) => { const p = clampHeart(x, y); glideTo(p.x, p.y, ms); });
ipcMain.on('glide-home', (_e, ms) => glideHome(ms));
ipcMain.on('glide-offset', (_e, dx, dy, ms) => { if (!home) return; const p = clampHeart(home.x + dx, home.y + dy); glideTo(p.x, p.y, ms || 900); });
ipcMain.on('attention', (_e, rect, ms) => attend(rect, ms));
ipcMain.on('panel-state', (_e, open) => {
  panelOpen = !!open;
  if (open && win) { const [x, y] = win.getPosition(); const p = clampWindow(x, y); if (p.x !== x || p.y !== y) glideTo(p.x, p.y, 250); }
});
ipcMain.on('painted', () => { if (!painted) { painted = true; dbg('renderer painted'); } });
ipcMain.on('notify', (_e, title, body) => {
  try { if (Notification.isSupported()) new Notification({ title: String(title || 'Eli'), body: String(body || ''), icon: path.join(__dirname, 'assets', 'heart-256.png') }).show(); } catch (_) { /* ignore */ }
});
ipcMain.on('quit', () => app.quit());
ipcMain.on('open-external', (_e, url) => { if (typeof url === 'string' && /^https?:\/\//.test(url)) shell.openExternal(url); });
ipcMain.handle('get-config', () => ({ backendUrl: BACKEND_WS, httpUrl: BACKEND_HTTP, opaque: OPAQUE }));

function sendShortcut(name) {
  dbg('shortcut', name);
  if (win) win.webContents.send('shortcut', name);
}
function bringHere() {
  if (!win) return;
  const c = cornerOf(displayForCursor());
  home = { ...c };
  glideTo(c.x, c.y, 500);
  sendShortcut('wave');
}

// ---------- app ---------------------------------------------------------------------------------------
app.whenReady().then(() => {
  if (!gotLock) return;
  createWindow();
  try {
    tray = new Tray(nativeImage.createFromPath(path.join(__dirname, 'assets', 'heart.png')));
    tray.setToolTip('Eli');
    tray.setContextMenu(Menu.buildFromTemplate([
      { label: 'Show / hide panel  (Ctrl+Shift+E)', click: () => sendShortcut('toggle-panel') },
      { label: 'Push to talk  (Ctrl+Shift+Space)', click: () => sendShortcut('ptt') },
      { label: 'Bring Eli here  (Ctrl+Shift+H)', click: bringHere },
      { label: 'Follow my cursor  (Ctrl+Shift+F)', click: () => sendShortcut('toggle-follow') },
      { label: 'Trust mode (auto-approve)', click: () => sendShortcut('toggle-trust') },
      { label: 'Open full chat panel', click: () => sendShortcut('open-panel') },
      { label: 'Reset position', click: () => { home = cornerOf(displayForCursor()); glideHome(500); } },
      { type: 'separator' },
      { label: 'Quit Eli', click: () => app.quit() },
    ]));
    tray.on('click', () => sendShortcut('toggle-panel'));
  } catch (e) {
    log('tray unavailable', e);
  }
  globalShortcut.register('Control+Shift+E', () => sendShortcut('toggle-panel'));
  globalShortcut.register('Control+Shift+Space', () => sendShortcut('ptt'));
  globalShortcut.register('Control+Shift+H', bringHere);
  globalShortcut.register('Control+Shift+F', () => sendShortcut('toggle-follow'));
  setInterval(pollCursor, 40);
  screen.on('display-removed', () => glideHome(300));
});

app.on('second-instance', () => { bringHere(); sendShortcut('toggle-panel'); });
app.on('will-quit', () => globalShortcut.unregisterAll());
app.on('window-all-closed', () => app.quit());
