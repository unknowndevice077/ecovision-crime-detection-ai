const { contextBridge, ipcRenderer } = require("electron");

contextBridge.exposeInMainWorld("setupBridge", {
  onLog: (cb) => ipcRenderer.on("setup:log", (_e, line) => cb(line)),
  onProgress: (cb) => ipcRenderer.on("setup:progress", (_e, pct, label) => cb(pct, label)),
  onDone: (cb) => ipcRenderer.on("setup:done", () => cb()),
  onError: (cb) => ipcRenderer.on("setup:error", (_e, msg) => cb(msg)),
  retry: () => ipcRenderer.send("setup:retry"),
});