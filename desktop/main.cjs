'use strict';

const { app, BrowserWindow, dialog, ipcMain, session } = require('electron');
const { spawn, execFile } = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const { parseOptions, isAppUrl, sameToken, freeLoopbackPort, checkpointBeforeExit } = require('./lifecycle.cjs');

app.setName('FramePulse');
app.setAppUserModelId('org.framepulse.desktop');
const sleep = milliseconds => new Promise(resolve => setTimeout(resolve, milliseconds));
let options;
try { options = parseOptions(process.argv.slice(1)); }
catch (error) { dialog.showErrorBox('FramePulse 启动参数', error.message); app.exit(1); }
if (options?.smokeOutput) {
  fs.mkdirSync(options.smokeOutput, { recursive: true });
  const qaProfile = path.join(options.smokeOutput, 'desktop-profile');
  fs.mkdirSync(qaProfile, { recursive: true });
  app.setPath('userData', qaProfile);
}

let mainWindow = null;
let backend = null;
let backendOrigin = '';
let serverToken = '';
let quitting = false;
let canQuit = false;
let initialized = false;
let backendLog = '';
let backendLogStream = null;
let smokeExitCode = 0;
const rendererErrors = [];
const allowedExtensions = ['mp4', 'mov', 'mkv', 'avi', 'mxf', 'webm', 'm4v', 'mts', 'm2ts', 'mpg', 'mpeg', 'png', 'jpg', 'jpeg', 'webp', 'bmp', 'tif', 'tiff', 'avif'];

function backendAlive() { return Boolean(backend && backend.exitCode === null && backend.signalCode === null && !backend.killed); }
function appendLog(chunk) {
  const message = String(chunk).split(serverToken).join('[session-token]');
  backendLog = (backendLog + message).slice(-12000);
  backendLogStream?.write(message);
}

async function backendRequest(route, method = 'GET') {
  const response = await fetch(`${backendOrigin}${route}`, {
    method, headers: { 'x-framepulse-token': serverToken }, signal: AbortSignal.timeout(6000),
  });
  if (!response.ok) throw new Error(`后台请求 ${route} 失败（${response.status}）。`);
  return response.json();
}

async function startBackend(dataDir) {
  const port = await freeLoopbackPort();
  backendOrigin = `http://127.0.0.1:${port}`;
  serverToken = crypto.randomBytes(32).toString('hex');
  const root = path.resolve(__dirname, '..');
  const developerPython = [path.join(root, '.venv', 'Scripts', 'python.exe'), path.join(root, '.qa-venv', 'Scripts', 'python.exe')]
    .find(candidate => fs.existsSync(candidate));
  const executable = app.isPackaged
    ? path.join(process.resourcesPath, 'backend', 'framepulse-backend.exe')
    : developerPython;
  if (!executable || !fs.existsSync(executable)) throw new Error('缺少后台程序。源码用户请先运行 Setup-FramePulse.cmd；安装版请重新解压或安装完整程序。');
  const args = app.isPackaged ? [] : [path.join(root, 'scripts', 'desktop-backend.py')];
  args.push('serve', '--data', dataDir, '--port', String(port));
  const env = { ...process.env, FRAMEPULSE_SERVER_TOKEN: serverToken, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' };
  if (!app.isPackaged) env.PYTHONPATH = path.join(root, 'src');
  const logsDir = app.getPath('logs');
  fs.mkdirSync(logsDir, { recursive: true });
  const logFile = path.join(logsDir, 'backend.log');
  if (fs.existsSync(logFile) && fs.statSync(logFile).size > 2 * 1024 * 1024) fs.renameSync(logFile, path.join(logsDir, `backend-${Date.now()}.log`));
  backendLogStream = fs.createWriteStream(logFile, { flags: 'a' });
  backend = spawn(executable, args, { cwd: dataDir, env, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'] });
  backend.stdout.on('data', appendLog);
  backend.stderr.on('data', appendLog);
  let spawnError;
  backend.on('error', error => { spawnError = error; });
  backend.on('exit', (code, signal) => {
    backendLogStream?.end();
    if (initialized && !quitting && !canQuit) {
      dialog.showErrorBox('FramePulse 后台已停止', `后台程序意外停止（${code ?? signal}）。已写入的镜头记录仍保留，请重新打开软件。\n\n日志：${logFile}`);
      canQuit = true;
      app.quit();
    }
  });
  const deadline = Date.now() + 90000;
  while (Date.now() < deadline) {
    if (spawnError) throw spawnError;
    if (!backendAlive()) throw new Error(`后台未能启动。\n${backendLog}`);
    try {
      const health = await backendRequest('/api/health');
      const config = await backendRequest('/api/config');
      if (health.app !== 'framepulse' || !sameToken(config.token, serverToken)) {
        throw Object.assign(new Error('后台身份核验失败，未连接该端口上的服务。'), { fatal: true });
      }
      return health;
    } catch (error) {
      if (error.fatal) throw error;
      await sleep(250);
    }
  }
  throw new Error(`后台启动超时。\n${backendLog}`);
}

function authorizePicker(event) {
  if (!mainWindow || event.sender !== mainWindow.webContents || event.senderFrame !== mainWindow.webContents.mainFrame || !isAppUrl(event.senderFrame.url, backendOrigin)) {
    throw new Error('文件选择请求来自未授权窗口。');
  }
}

function registerPickers() {
  ipcMain.handle('framepulse:pick-files', async event => {
    authorizePicker(event);
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择视频或图片', properties: ['openFile', 'multiSelections', 'dontAddToRecent'],
      filters: [{ name: '视频和图片', extensions: allowedExtensions }],
    });
    return result.canceled ? [] : result.filePaths;
  });
  ipcMain.handle('framepulse:pick-folder', async event => {
    authorizePicker(event);
    const result = await dialog.showOpenDialog(mainWindow, {
      title: '选择素材文件夹', properties: ['openDirectory', 'dontAddToRecent'],
    });
    return result.canceled ? [] : result.filePaths;
  });
}

async function stopOwnedBackend() {
  if (!backendAlive()) return;
  const owned = backend;
  const exit = new Promise(resolve => owned.once('exit', resolve));
  if (process.platform === 'win32') {
    // PID comes only from our still-running ChildProcess. Never kill by image name.
    await new Promise(resolve => execFile(path.join(process.env.SystemRoot || 'C:\\Windows', 'System32', 'taskkill.exe'),
      ['/PID', String(owned.pid), '/T', '/F'], { windowsHide: true }, () => resolve()));
  } else owned.kill('SIGTERM');
  await Promise.race([exit, sleep(5000)]);
}

async function requestQuit() {
  if (quitting || canQuit) return;
  quitting = true;
  mainWindow?.setTitle('FramePulse · 正在保存任务进度…');
  mainWindow?.setEnabled(false);
  try {
    let saved = !initialized;
    while (!saved && backendAlive()) {
      try {
        saved = await checkpointBeforeExit(backendRequest, { isAlive: backendAlive, sleep, maxWait: 120000 });
      } catch (error) {
        appendLog(`\n[desktop shutdown] ${error.message}\n`);
      }
      if (!saved && backendAlive()) {
        const result = await dialog.showMessageBox(mainWindow, {
          type: 'warning', title: '任务正在停止', message: '正在等待当前镜头保存断点。',
          detail: '继续等待会保留完整的恢复状态。强制退出可能丢失当前尚未写入的工作，已保存的镜头不受影响。',
          buttons: ['继续等待', '强制退出'], defaultId: 0, cancelId: 0, noLink: true,
        });
        if (result.response === 1) break;
      }
    }
    await stopOwnedBackend();
    canQuit = true;
    if (options.smokeOutput) app.exit(smokeExitCode);
    else app.quit();
  } finally { quitting = false; }
}

async function smokeTest(health, dataDir) {
  // An explicit local QA launch captures the actual Electron window; no debug port
  // or arbitrary JavaScript bridge is exposed to the renderer or network.
  await sleep(1800);
  const contents = mainWindow.webContents;
  const renderer = await contents.executeJavaScript(`(() => ({
    title: document.title, readyState: document.readyState,
    bodyText: document.body.innerText.slice(0, 2400),
    hasDesktopBridge: typeof window.framepulseDesktop?.pickFiles === 'function' && typeof window.framepulseDesktop?.pickFolder === 'function',
    hasPathBridge: typeof window.framepulseDesktop?.pathsForFiles === 'function',
    requireType: typeof window.require, processType: typeof window.process,
    userAgent: navigator.userAgent,
    nonDiskFilePaths: window.framepulseDesktop?.pathsForFiles([new File(['test'], 'test.png')])
  }))()`);
  const preferences = contents.getLastWebPreferences();
  const screenshot = await contents.capturePage();
  fs.writeFileSync(path.join(options.smokeOutput, 'desktop-window.png'), screenshot.toPNG());
  const report = { app: 'FramePulse', packaged: app.isPackaged, appVersion: app.getVersion(),
    backendVersion: health.version, backendCapabilities: health.capabilities,
    origin: backendOrigin, backendPid: backend.pid, desktopPid: process.pid, dataDir,
    electron: process.versions.electron, chromium: process.versions.chrome,
    security: { contextIsolation: preferences.contextIsolation, nodeIntegration: preferences.nodeIntegration, sandbox: preferences.sandbox },
    renderer, rendererErrors, timestamp: new Date().toISOString() };
  fs.writeFileSync(path.join(options.smokeOutput, 'desktop-smoke.json'), JSON.stringify(report, null, 2));
  const valid = renderer.hasDesktopBridge && renderer.hasPathBridge && renderer.requireType === 'undefined' && renderer.processType === 'undefined' && !rendererErrors.length;
  smokeExitCode = valid ? 0 : 1;
  if (!valid) appendLog('\n[desktop smoke] renderer verification failed\n');
  await requestQuit();
}

async function launch() {
  const dataDir = options.data || path.join(app.getPath('userData'), 'library');
  fs.mkdirSync(dataDir, { recursive: true });
  const icon = fs.readFileSync(path.join(__dirname, 'assets', 'icon.png')).toString('base64');
  const loadingUrl = `data:text/html;charset=utf-8,${encodeURIComponent(`<!doctype html><html lang="zh-CN"><meta charset="utf-8">
    <meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'">
    <title>FramePulse · 正在启动</title><style>
    *{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:#10151d;color:#e4e8ef;font:15px 'Segoe UI','Microsoft YaHei',sans-serif}
    main{text-align:center}img{width:80px;height:80px}h1{font-size:26px;font-weight:550;letter-spacing:-.7px;margin:22px 0 10px}p{color:#9ba5b5;font-size:13px}
    .progress{height:2px;width:120px;margin:30px auto;background:#283440;overflow:hidden}.progress:after{content:'';display:block;height:100%;width:50%;background:#f3bd54;animation:scan 1.5s ease-in-out infinite}
    @keyframes scan{0%{transform:translateX(-100%)}100%{transform:translateX(300%)}}@media(prefers-reduced-motion:reduce){.progress:after{animation:none;width:100%}}
    </style><main><img alt="" src="data:image/png;base64,${icon}"><h1>FramePulse</h1><p>正在准备素材工作台…</p><div class="progress"></div></main></html>`)}`;
  const isolatedSession = session.fromPartition(`framepulse-${crypto.randomBytes(12).toString('hex')}`);
  isolatedSession.setPermissionRequestHandler((_contents, _permission, callback) => callback(false));
  isolatedSession.setPermissionCheckHandler(() => false);
  isolatedSession.webRequest.onBeforeRequest((details, callback) => {
    const inlineImage = details.resourceType === 'image' && details.url.startsWith('data:image/');
    callback({ cancel: !isAppUrl(details.url, backendOrigin) && details.url !== 'about:blank' && details.url !== loadingUrl && !inlineImage });
  });
  mainWindow = new BrowserWindow({
    title: 'FramePulse · 正在启动', width: 1480, height: 960, minWidth: 1080, minHeight: 720,
    backgroundColor: '#10151d', show: true, autoHideMenuBar: true,
    icon: path.join(__dirname, 'assets', 'icon.png'),
    webPreferences: { preload: path.join(__dirname, 'preload.cjs'), contextIsolation: true,
      nodeIntegration: false, sandbox: true, webSecurity: true, session: isolatedSession,
      allowRunningInsecureContent: false, webviewTag: false, devTools: !app.isPackaged },
  });
  mainWindow.setMenu(null);
  mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  mainWindow.webContents.on('will-navigate', (event, url) => { if (!isAppUrl(url, backendOrigin)) event.preventDefault(); });
  mainWindow.webContents.on('will-redirect', (event, url) => { if (!isAppUrl(url, backendOrigin)) event.preventDefault(); });
  mainWindow.webContents.on('will-attach-webview', event => event.preventDefault());
  mainWindow.webContents.on('console-message', (event, level, message) => {
    const details = typeof level === 'object' ? level : event;
    if (details?.level === 'error' || level === 3) rendererErrors.push(String(details.message || message).slice(0, 1000));
  });
  mainWindow.webContents.on('render-process-gone', (_event, details) => {
    if (!quitting) { appendLog(`\n[renderer] ${details.reason}\n`); requestQuit(); }
  });
  mainWindow.on('close', event => { if (!canQuit) { event.preventDefault(); void requestQuit(); } });
  registerPickers();
  await mainWindow.loadURL(loadingUrl);
  if (quitting || canQuit) return;
  const health = await startBackend(dataDir);
  if (quitting || canQuit) return;
  initialized = true;
  await mainWindow.loadURL(backendOrigin);
  mainWindow.setTitle('FramePulse');
  if (options.smokeOutput) await smokeTest(health, dataDir);
}

if (!app.requestSingleInstanceLock()) {
  app.exit(0);
} else {
  app.on('second-instance', () => { if (mainWindow) { if (mainWindow.isMinimized()) mainWindow.restore(); mainWindow.show(); mainWindow.focus(); } });
  app.on('before-quit', event => { if (!canQuit) { event.preventDefault(); void requestQuit(); } });
  app.on('window-all-closed', () => { if (canQuit) app.quit(); });
  app.whenReady().then(launch).catch(async error => {
    if (canQuit || quitting) return;
    if (options.smokeOutput) fs.writeFileSync(path.join(options.smokeOutput, 'desktop-error.txt'), String(error.stack || error));
    quitting = true;
    await stopOwnedBackend();
    dialog.showErrorBox('FramePulse 无法启动', error.message);
    canQuit = true;
    app.exit(1);
  });
}
