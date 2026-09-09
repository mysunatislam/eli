const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('setup', {
  finish: (data) => ipcRenderer.invoke('onboarding-finish', data),
  openExternal: (url) => ipcRenderer.send('open-external', url),
  onProgress: (cb) => ipcRenderer.on('onboarding-progress', (_e, text) => cb(text)),
});
