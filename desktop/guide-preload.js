const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('guide', {
  onEvent: (cb) => ipcRenderer.on('guide', (_e, m) => cb(m)),
  onState: (cb) => ipcRenderer.on('guide-state', (_e, s) => cb(s)),
});
