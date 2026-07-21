// electron/setup-preload.js
const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("setupBridge", {
  selectDirectory: () => ipcRenderer.invoke("setup:select-directory"),
  startSetup: (targetPath) => ipcRenderer.send("setup:start", targetPath),
  onLog: (cb) => ipcRenderer.on("setup:log", (_e, line) => cb(line)),
  onProgress: (cb) => ipcRenderer.on("setup:progress", (_e, pct, label) => cb(pct, label)),
  onInstallPath: (cb) => ipcRenderer.on("setup:install-path", (_e, p) => cb(p)),
  onDone: (cb) => ipcRenderer.on("setup:done", () => cb()),
  onError: (cb) => ipcRenderer.on("setup:error", (_e, msg) => cb(msg)),
  retry: (targetPath) => ipcRenderer.send("setup:start", targetPath),
});