const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('eli', {
  // config & lifecycle
  getConfig: () => ipcRenderer.invoke('get-config'),
  quit: () => ipcRenderer.send('quit'),
  openExternal: (url) => ipcRenderer.send('open-external', url),
  notify: (title, body) => ipcRenderer.send('notify', title, body),
  painted: () => ipcRenderer.send('painted'),
  // mouse / hit-testing
  setHitRegions: (regions) => ipcRenderer.send('set-hit-regions', regions),
  setIgnoreMouse: (ignore) => ipcRenderer.send('set-ignore-mouse', ignore),
  onCursor: (cb) => ipcRenderer.on('cursor', (_e, x, y) => cb(x, y)),
  // movement
  moveBy: (dx, dy) => ipcRenderer.send('move-window', dx, dy),
  dragStart: () => ipcRenderer.send('drag-start'),
  dragEnd: () => ipcRenderer.send('drag-end'),
  glideTo: (x, y, ms) => ipcRenderer.send('glide-to', x, y, ms),
  glideHome: (ms) => ipcRenderer.send('glide-home', ms),
  glideOffset: (dx, dy, ms) => ipcRenderer.send('glide-offset', dx, dy, ms),
  attention: (rect, ms) => ipcRenderer.send('attention', rect, ms),
  panelState: (open) => ipcRenderer.send('panel-state', open),
  setFollow: (on) => ipcRenderer.send('set-follow', on),
  // guide overlay
  guide: (m) => ipcRenderer.send('guide-event', m),
  guideState: (s) => ipcRenderer.send('guide-state', s),
  guideCheck: (g) => ipcRenderer.send('guide-check', g),
  onGuideResend: (cb) => ipcRenderer.on('guide-resend', () => cb()),
  // shortcuts from the tray / global hotkeys
  onShortcut: (cb) => ipcRenderer.on('shortcut', (_e, name) => cb(name)),
});
