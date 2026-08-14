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

  // Install no longer launches the app itself -- it hands control back to the
  // renderer so the optional GPU optimization can be offered in between. The
  // app is fully installed and runnable at this point either way; optimizing
  // only changes speed.
  onSetupComplete: (cb) => ipcRenderer.on("setup:complete", () => cb()),

  // Answered by the main process rather than guessed in the page: whether a
  // CUDA GPU and TensorRT are actually present decides if the offer is real.
  checkOptimize: () => ipcRenderer.invoke("setup:check-optimize"),

  startOptimize: () => ipcRenderer.send("setup:start-optimize"),
  onOptimizeEvent: (cb) => ipcRenderer.on("setup:optimize-event", (_e, ev) => cb(ev)),
  onOptimizeLog: (cb) => ipcRenderer.on("setup:optimize-log", (_e, line, isErr) => cb(line, isErr)),

  finish: (optimized) => ipcRenderer.send("setup:finish", optimized),
});
