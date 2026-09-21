'use strict';
const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('framepulseDesktop', Object.freeze({
  pickFiles: () => ipcRenderer.invoke('framepulse:pick-files'),
  pickFolder: () => ipcRenderer.invoke('framepulse:pick-folder'),
  pathsForFiles: files => {
    if (!Array.isArray(files) || files.length > 10000) return [];
    return files.map(file => {
      try { return webUtils.getPathForFile(file); } catch { return ''; }
    }).filter(Boolean);
  },
}));
