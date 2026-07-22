// electron/launch-preload.js
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("launchBridge", {
  startLaunch: () => ipcRenderer.send("launch:start"),
  onLog: (cb) => ipcRenderer.on("launch:log", (_e, line) => cb(line)),
  onProgress: (cb) => ipcRenderer.on("launch:progress", (_e, pct, label) => cb(pct, label)),
  onStepStatus: (cb) => ipcRenderer.on("launch:step", (_e, step, state) => cb(step, state)),
  onError: (cb) => ipcRenderer.on("launch:error", (_e, msg) => cb(msg)),
});