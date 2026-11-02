const {contextBridge,ipcRenderer}=require('electron');
contextBridge.exposeInMainWorld('desktop',{
 connectAI:()=>ipcRenderer.invoke('setup-connect'),
 windowMinimize:()=>ipcRenderer.invoke('window-minimize'),
 windowToggleMaximize:()=>ipcRenderer.invoke('window-toggle-maximize'),
 windowClose:()=>ipcRenderer.invoke('window-close'),
 windowIsMaximized:()=>ipcRenderer.invoke('window-is-maximized'),
 chooseInvoice:()=>ipcRenderer.invoke('choose-invoice'),
 saveExport:opts=>ipcRenderer.invoke('save-export',opts),
 openExternal:url=>ipcRenderer.invoke('open-external',url),
 copyText:text=>ipcRenderer.invoke('copy-text',text),
 onSetupProgress:fn=>ipcRenderer.on('setup-progress',(_e,data)=>fn(data)),
 onBackendLog:fn=>ipcRenderer.on('backend-log',(_e,data)=>fn(data))
});
