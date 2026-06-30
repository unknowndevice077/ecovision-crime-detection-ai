// electron-main.js
const { app, BrowserWindow } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const net = require('net');

let mainWindow;
let pythonBackendProcess = null;
let pythonAiProcess = null;
let nextJsProcess = null;

// Network confirmation thresholds
const PORT_BACKEND = 8000;
const PORT_VISION  = 8001;
const PORT_UI      = 3000;

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
    console.log("📁 Initializing standalone data ledger backend...");
    // NEW: '-u' forces unbuffered output so Python logs print instantly
    pythonBackendProcess = spawn('.\\.venv\\Scripts\\python.exe', ['-u', 'app/backend.py'], {
        env: { ...process.env, NODE_ENV: 'production' },
        windowsHide: true,
        shell: true,
        stdio: 'inherit' 
    });

    console.log("👁️ Initializing neural pose tracking matrices...");
    // NEW: '-u' forces unbuffered output for main.py as well
    pythonAiProcess = spawn('.\\.venv\\Scripts\\python.exe', ['-u', 'maincode/main.py'], {
        windowsHide: true,
        shell: true,
        stdio: 'inherit'
    });

    console.log("💻 Mounting production interface architecture maps...");
    nextJsProcess = spawn('npm.cmd', ['run', 'start'], {
        windowsHide: true,
        shell: true,
        stdio: 'inherit' 
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
        backgroundColor: '#111827', 
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
                    
                    // Prints clear live status reports directly to your command line
                    console.log(`⏳ [SYSTEM DIAGNOSTIC] Check #${attempts} | Backend (8000): ${backendAlive ? '🟢 ONLINE' : '🔴 OFFLINE'} | Vision (8001): ${visionAlive ? '🟢 ONLINE' : '🔴 OFFLINE'} | Next.js (3000): ${uiAlive ? '🟢 ONLINE' : '🔴 OFFLINE'}`);
                    
                    if (uiAlive) {
                        clearInterval(checkInterval);
                        console.log("🚀 Next.js endpoint responded! Launching native app frame...");
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

// Absolute hardware process cleanup layer to eliminate background memory leaks
app.on('window-all-closed', () => {
    console.log("🛑 App container terminated. Clearing system service registers...");
    
    if (pythonBackendProcess) pythonBackendProcess.kill();
    if (pythonAiProcess) pythonAiProcess.kill();
    if (nextJsProcess) nextJsProcess.kill();
    
    if (process.platform !== 'darwin') {
        app.quit();
    }
});