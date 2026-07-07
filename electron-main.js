// electron-main.js
const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn, exec } = require('child_process');
const net = require('net');

let mainWindow;
let pythonBackendProcess = null;
let pythonAiProcess = null;
let nextJsProcess = null;

// Network confirmation thresholds
const PORT_BACKEND = 8000;
const PORT_VISION  = 8001;
const PORT_UI      = 3000;

// FIXED: Shifted from app.getAppPath() to process.cwd() to completely defeat the Windows cmd.exe working directory crash
const PROJECT_ROOT = process.cwd();
const PYTHON_PATH = path.join(PROJECT_ROOT, '.venv', 'Scripts', 'python.exe');

// Bulletproof Network Socket Connection Scanner
function checkPort(port, callback) {
    const socket = new net.Socket();
    socket.setTimeout(400);
    
    socket.once('connect', () => {
        socket.destroy();
        callback(true); // Port is active and listening!
    });
    
    socket.once('timeout', () => {
        socket.destroy();
        callback(false);
    });
    
    socket.once('error', () => {
        socket.destroy();
        callback(false); // Port is closed/offline
    });
    
    socket.connect(port, '127.0.0.1');
}

function bootBackgroundServices() {
    console.log(`📁 System Environment Root Rooted: ${PROJECT_ROOT}`);
    
    console.log("📁 Initializing standalone data ledger backend...");
    pythonBackendProcess = spawn(PYTHON_PATH, ['-u', path.join(PROJECT_ROOT, 'app', 'backend.py')], {
        env: { ...process.env, NODE_ENV: 'production' },
        cwd: PROJECT_ROOT,
        windowsHide: true,
        shell: true
    });

    console.log("👁️ Initializing neural pose tracking matrices...");
    pythonAiProcess = spawn(PYTHON_PATH, ['-u', path.join(PROJECT_ROOT, 'maincode', 'main.py')], {
        cwd: PROJECT_ROOT,
        windowsHide: true,
        shell: true
    });

    console.log("💻 Mounting production interface architecture maps...");
    nextJsProcess = spawn('npm.cmd', ['run', 'start'], {
        cwd: PROJECT_ROOT,
        windowsHide: true,
        shell: true
    });
}

function createDesktopWindow() {
    mainWindow = new BrowserWindow({
        title: "EcoVision Security Sentinel Command Dashboard",
        width: 1280,
        height: 800,
        minWidth: 1024,
        minHeight: 768,
        resizable: true,
        autoHideMenuBar: true, 
        backgroundColor: '#0B0F17', 
        show: false, 
        webPreferences: {
            nodeIntegration: false,
            contextIsolation: true
        }
    });

    let attempts = 0;
    // Live Diagnostic loop runs every 2 seconds
    const checkInterval = setInterval(() => {
        attempts++;
        
        checkPort(PORT_BACKEND, (backendAlive) => {
            checkPort(PORT_VISION, (visionAlive) => {
                checkPort(PORT_UI, (uiAlive) => {
                    
                    console.log(`⏳ [SYSTEM DIAGNOSTIC] Check #${attempts} | Backend (8000): ${backendAlive ? '🟢 ONLINE' : '🔴 OFFLINE'} | Vision (8001): ${visionAlive ? '🟢 ONLINE' : '🔴 OFFLINE'} | Next.js (3000): ${uiAlive ? '🟢 ONLINE' : '🔴 OFFLINE'}`);
                    
                    if (uiAlive && backendAlive && visionAlive) {
                        clearInterval(checkInterval);
                        console.log("🚀 All edge services responsive! Launching native app frame...");
                        mainWindow.loadURL(`http://localhost:${PORT_UI}`);
                        
                        mainWindow.once('ready-to-show', () => {
                            mainWindow.show();
                            mainWindow.focus();
                        });
                    }
                });
            });
        });
    }, 2000);

    mainWindow.on('closed', () => {
        mainWindow = null;
    });
}

// Electron System Lifecycle Hooks
app.whenReady().then(() => {
    bootBackgroundServices();
    createDesktopWindow();
});

// Absolute process-tree cleanup layer to eliminate sub-shell memory leaks on Windows
app.on('window-all-closed', () => {
    console.log("🛑 App container terminated. Clearing system service registers...");
    
    if (pythonBackendProcess && pythonBackendProcess.pid) {
        exec(`taskkill /F /T /PID ${pythonBackendProcess.pid}`);
    }
    if (pythonAiProcess && pythonAiProcess.pid) {
        exec(`taskkill /F /T /PID ${pythonAiProcess.pid}`);
    }
    if (nextJsProcess && nextJsProcess.pid) {
        exec(`taskkill /F /T /PID ${nextJsProcess.pid}`);
    }
    
    if (process.platform !== 'darwin') {
        app.quit();
    }
});