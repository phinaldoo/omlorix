const { contextBridge, ipcRenderer } = require('electron');

// Chromium lowercases renderer switch names on Windows. Using an explicitly
// lowercase switch here and in main.js keeps progress state and button actions
// connected on every platform.
const channelArg = process.argv.find((arg) => arg.startsWith('--launcher-update-progress-channel='));
const channel = channelArg ? channelArg.split('=').slice(1).join('=') : '';

contextBridge.exposeInMainWorld('launcherUpdateProgress', {
  sendAction: (action) => {
    if (!channel) return;
    ipcRenderer.send(`${channel}:action`, String(action || ''));
  },
  onState: (callback) => {
    if (!channel || typeof callback !== 'function') {
      return () => {};
    }
    const listener = (event, payload) => callback(payload || {});
    ipcRenderer.on(`${channel}:state`, listener);
    return () => ipcRenderer.removeListener(`${channel}:state`, listener);
  },
});
