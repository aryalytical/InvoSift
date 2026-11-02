const { app, BrowserWindow, ipcMain, dialog, shell, clipboard } = require('electron');
const { spawn, execFile } = require('child_process');
const fs = require('fs');
const path = require('path');
const https = require('https');
const crypto = require('crypto');
const { session } = require('electron');

let win, backendProc;
const SESSION_TOKEN = crypto.randomBytes(32).toString('hex');
const PORT = 8765;
const API = `http://127.0.0.1:${PORT}`;
const packaged = app.isPackaged;
if (process.platform === 'win32') app.setAppUserModelId('com.invosift.desktop');
const root = packaged ? process.resourcesPath : path.join(__dirname, '..');
const userData = app.getPath('userData');
function send(channel, payload) { if (win && !win.isDestroyed()) win.webContents.send(channel, payload); }
function createWindow() {
  win = new BrowserWindow({width:1440,height:920,minWidth:1050,minHeight:680,backgroundColor:'#17191d', icon:path.join(app.getAppPath(),'build','icon.ico'),
    title:'InvoSift', frame:false, autoHideMenuBar:true, webPreferences:{preload:path.join(__dirname,'preload.js'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
  win.webContents.setWindowOpenHandler(({url}) => {
    try { const u = new URL(url); if (u.protocol === 'https:' && ['ollama.com','github.com','www.github.com'].includes(u.hostname)) shell.openExternal(u.toString()); } catch {}
    return { action: 'deny' };
  });
  win.webContents.on('will-navigate', (event, url) => {
    if (!url.startsWith('file://') || !url.includes('/ui/index.html')) event.preventDefault();
  });
  win.loadFile(path.join(app.getAppPath(),'ui','index.html'));
  win.on('closed',()=>{win=null;});
}
function request(url, opts={}) {
  const headers = new Headers(opts.headers || {});
  if (String(url).startsWith(API + '/')) headers.set('X-InvoSift-Session', SESSION_TOKEN);
  return fetch(url, {...opts, headers});
}
function trustedSender(event) {
  try {
    const u = new URL(event.senderFrame.url);
    return u.protocol === 'file:' && u.pathname.replace(/\\\\/g, '/').endsWith('/ui/index.html');
  } catch { return false; }
}
function registerSecureHandle(channel, handler) {
  ipcMain.handle(channel, (event, ...args) => {
    if (!trustedSender(event)) throw new Error('Unauthorized IPC sender.');
    return handler(event, ...args);
  });
}
async function waitBackend() {
  for(let i=0;i<90;i++){ try { const r=await request(`${API}/api/health`); if(r.ok)return true; }catch{} await new Promise(r=>setTimeout(r,500)); }
  return false;
}
function startBackend() {
  if(packaged) {
    const exe=path.join(process.resourcesPath,'backend','invoice-backend.exe');
    if(fs.existsSync(exe)) backendProc=spawn(exe,[],{windowsHide:true,env:{...process.env,PORT:String(PORT),HOST:'127.0.0.1',INVOSIFT_SESSION_TOKEN:SESSION_TOKEN,DATA_DIR:path.join(userData,'data'),DATABASE_PATH:path.join(userData,'data','invoices.db'),OLLAMA_MODEL:'gemma3:4b'}});
    else {
      const py=path.join(process.resourcesPath,'backend_src','run.py');
      backendProc=spawn('python',[py,'--host','127.0.0.1','--port',String(PORT)],{windowsHide:true,env:{...process.env,PORT:String(PORT),HOST:'127.0.0.1',INVOSIFT_SESSION_TOKEN:SESSION_TOKEN,DATA_DIR:path.join(userData,'data'),DATABASE_PATH:path.join(userData,'data','invoices.db')},cwd:path.dirname(py)});
    }
  } else {
    const py=process.env.PYTHON||'python';
    backendProc=spawn(py,['run.py','--host','127.0.0.1','--port',String(PORT)],{cwd:path.join(root,'backend_src'),windowsHide:true,env:{...process.env,PORT:String(PORT),HOST:'127.0.0.1',INVOSIFT_SESSION_TOKEN:SESSION_TOKEN,DATA_DIR:path.join(userData,'data'),DATABASE_PATH:path.join(userData,'data','invoices.db')},shell:false});
  }
  backendProc.on('error',e=>send('backend-log',{stage:'backend',message:`Backend launch error: ${e.message}`}));
  backendProc.stderr?.on('data',d=>send('backend-log',{stage:'backend',message:String(d).trim().slice(-500)}));
}
function which(cmd,args=['--version']) { return new Promise(resolve=>execFile(cmd,args,{windowsHide:true},(e,stdout)=>resolve(!e?String(stdout).trim():null))); }
function download(url,dest,onProgress=()=>{}) {
  return new Promise((resolve,reject)=>{
    const go=(u)=>https.get(u,res=>{
      if(res.statusCode>=300&&res.statusCode<400&&res.headers.location)return go(res.headers.location);
      if(res.statusCode!==200)return reject(new Error(`Download failed (HTTP ${res.statusCode})`));
      const total=Number(res.headers['content-length']||0); let got=0;
      const out=fs.createWriteStream(dest); res.on('data',b=>{got+=b.length;onProgress(total?Math.round(got/total*100):0,got,total);});
      res.pipe(out);out.on('finish',()=>out.close(resolve));out.on('error',reject);
    }).on('error',reject);go(url);
  });
}
const delay=ms=>new Promise(r=>setTimeout(r,ms));
async function runCommand(cmd,args,stage,label) {
  return new Promise((resolve,reject)=>{
    const p=spawn(cmd,args,{windowsHide:true,shell:false});
    let buffer='';
    p.stdout.on('data',d=>{buffer+=String(d); const lines=buffer.split(/\r?\n/);buffer=lines.pop()||'';for(const line of lines)if(line.trim())send('setup-progress',{stage,label,message:line.trim(),percent:null});});
    p.stderr.on('data',d=>{buffer+=String(d); const lines=buffer.split(/\r?\n/);buffer=lines.pop()||'';for(const line of lines)if(line.trim())send('setup-progress',{stage,label,message:line.trim(),percent:null});});
    p.on('error',reject);p.on('close',code=>code===0?resolve():reject(new Error(`${label} failed (exit code ${code})`)));
  });
}
async function locateOllama() {
  const found=await which('ollama',['--version']); if(found)return 'ollama';
  const candidates=[path.join(process.env.LOCALAPPDATA||'', 'Programs','Ollama','ollama.exe'),path.join(process.env.ProgramFiles||'C:\\Program Files','Ollama','ollama.exe')];
  return candidates.find(p=>p&&fs.existsSync(p))||null;
}
registerSecureHandle('setup-connect',async()=>{
  try {
    send('setup-progress',{stage:'checking',label:'Checking system',message:'Checking for Ollama...',percent:5});
    let ollama=await locateOllama();
    if(!ollama){
      send('setup-progress',{stage:'download',label:'Download Ollama',message:'Downloading the official Ollama installer...',percent:10});
      const installer=path.join(app.getPath('temp'),'OllamaSetup.exe');
      await download('https://ollama.com/download/OllamaSetup.exe',installer,(pct,got,total)=>send('setup-progress',{stage:'download',label:'Download Ollama',message:`Downloaded ${(got/1048576).toFixed(1)} MB${total?' / '+(total/1048576).toFixed(1)+' MB':''}`,percent:total?10+Math.round(pct*.35):null}));
      send('setup-progress',{stage:'install',label:'Install Ollama',message:'Running Ollama installer silently...',percent:48});
      await runCommand(installer,['/VERYSILENT','/NORESTART'],'install','Ollama installer');
      for(let i=0;i<30&&! (ollama=await locateOllama());i++)await delay(1000);
      if(!ollama)throw new Error('Ollama was installed but its executable was not found. Restart Windows and try Connect AI again.');
    }
    send('setup-progress',{stage:'service',label:'Start AI service',message:'Starting Ollama service...',percent:55});
     // Start service detached; if already running, the existing service is reused.
    try { spawn(ollama,['serve'],{detached:true,stdio:'ignore',windowsHide:true}).unref(); } catch {}
    let alive=false;
    for(let i=0;i<25;i++){try{const r=await request('http://127.0.0.1:11434/api/tags');if(r.ok){alive=true;break;}}catch{}await delay(800);}
    if(!alive)throw new Error('Ollama did not start. Please close any other Ollama setup window and retry.');
    const tags=await (await request('http://127.0.0.1:11434/api/tags')).json();
    if(!(tags.models||[]).some(m=>m.name==='gemma3:4b'||m.name.startsWith('gemma3:4b'))){
      send('setup-progress',{stage:'model',label:'Connect AI model',message:'Downloading Gemma 3 4B. This can take a while; keep the app open.',percent:60});
      await new Promise((resolve,reject)=>{
        const p=spawn(ollama,['pull','gemma3:4b'],{windowsHide:true});
        p.stdout.on('data',d=>{const s=String(d).trim(); if(s)send('setup-progress',{stage:'model',label:'Connect AI model',message:s,percent:60});});
        p.stderr.on('data',d=>{const s=String(d).trim(); if(s)send('setup-progress',{stage:'model',label:'Connect AI model',message:s,percent:60});});
        p.on('error',reject);p.on('close',c=>c===0?resolve():reject(new Error(`Model download failed (exit code ${c}). Check your internet connection and retry.`)));
      });
    }
    send('setup-progress',{stage:'ready',label:'Ready',message:'AI model connected successfully.',percent:100});
    return {ok:true};
  } catch(e) { send('setup-progress',{stage:'error',label:'Setup needs attention',message:e.message,percent:null}); return {ok:false,error:e.message}; }
});
registerSecureHandle('choose-invoice',async()=>{
  const result=await dialog.showOpenDialog(win,{title:'Choose an invoice',properties:['openFile'],filters:[{name:'Invoice documents',extensions:['pdf','png','jpg','jpeg']}]});
  if(result.canceled)return null;const p=result.filePaths[0];return {name:path.basename(p),path:p,data:fs.readFileSync(p).toString('base64'),size:fs.statSync(p).size};
});
registerSecureHandle('save-export',async(_e,{url,defaultName})=>{
  if (typeof url !== 'string') throw new Error('Invalid export request.');
  const exportUrl = new URL(url);
  if (exportUrl.origin !== API || !/^\/api\/invoices\/(?:export\/excel|\d+\/export\/(?:excel|json|csv|text))$/.test(exportUrl.pathname)) throw new Error('Invalid export request.');
  if (typeof defaultName !== 'string' || !/^[A-Za-z0-9 _.-]{1,100}$/.test(defaultName)) throw new Error('Invalid export filename.');
  const result=await dialog.showSaveDialog(win,{defaultPath:defaultName});
  if(result.canceled)return {canceled:true};
  const r=await request(url);if(!r.ok)throw new Error(`Export failed (${r.status})`);
  fs.writeFileSync(result.filePath,Buffer.from(await r.arrayBuffer()));
  return {ok:true,path:result.filePath};
});
registerSecureHandle('window-minimize',()=>{if(win&&!win.isDestroyed())win.minimize();});
registerSecureHandle('window-toggle-maximize',()=>{if(!win||win.isDestroyed())return false;if(win.isMaximized())win.unmaximize();else win.maximize();return win.isMaximized();});
registerSecureHandle('window-close',()=>{if(win&&!win.isDestroyed())win.close();});
registerSecureHandle('window-is-maximized',()=>!!(win&&!win.isDestroyed()&&win.isMaximized()));
registerSecureHandle('open-external',async(_e,url)=>{
  if(typeof url!=='string') throw new Error('Invalid URL.');
  const u=new URL(url);
  if(u.origin===API && /^\/api\/invoices\/\d+\/file$/.test(u.pathname)) {
    const r=await request(u.toString());
    if(!r.ok) throw new Error(`Could not open invoice (${r.status}).`);
    const type=(r.headers.get('content-type')||'').toLowerCase();
    const ext=type.includes('pdf')?'.pdf':type.includes('png')?'.png':'.jpg';
    const dest=path.join(app.getPath('temp'),`InvoSift-preview-${crypto.randomBytes(12).toString('hex')}${ext}`);
    fs.writeFileSync(dest,Buffer.from(await r.arrayBuffer()));
    await shell.openPath(dest);
    return {ok:true};
  }
  if(u.protocol!=='https:' || !['ollama.com','github.com','www.github.com'].includes(u.hostname)) throw new Error('Only approved HTTPS links can be opened.');
  return shell.openExternal(u.toString());
});
registerSecureHandle('copy-text',(_e,text)=>{clipboard.writeText(String(text||''));return {ok:true};});
async function autoStartExistingOllama() {
  const ollama = await locateOllama();
  if (!ollama) return;
  try {
    const r = await request('http://127.0.0.1:11434/api/tags');
    if (r.ok) return; // already running; do not spawn a duplicate service
  } catch {}
  try { spawn(ollama, ['serve'], { detached: true, stdio: 'ignore', windowsHide: true }).unref(); } catch {}
}
app.whenReady().then(async()=>{
  session.defaultSession.webRequest.onBeforeSendHeaders({urls:[`${API}/*`]}, (details, callback) => {
    details.requestHeaders['X-InvoSift-Session'] = SESSION_TOKEN;
    callback({requestHeaders:details.requestHeaders});
  });
  startBackend();await waitBackend();createWindow();autoStartExistingOllama();});
app.on('before-quit',()=>{if(backendProc&&!backendProc.killed)backendProc.kill();});
app.on('window-all-closed',()=>{if(process.platform!=='darwin')app.quit();});
