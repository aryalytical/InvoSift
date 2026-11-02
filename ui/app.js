const $=s=>document.querySelector(s), $$=s=>[...document.querySelectorAll(s)];
const API='http://127.0.0.1:8765';
let selectedFile=null, invoices=[], currentInvoice=null, chatInvoice=null, chatHistory=[], isReady=false, activeExtractionToken=null, extractionInProgress=false, cancelRequested=false;
const esc=v=>String(v??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const money=(v,c='')=>v==null?'—':`${c?c+' ':''}${Number(v).toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2})}`;
function toast(s,error=false){const t=$('#toast');t.textContent=s;t.className='toast'+(error?' error':'');t.hidden=false;setTimeout(()=>t.hidden=true,3500)}
async function api(path,opts={}){let r;try{r=await fetch(API+path,opts)}catch{throw Error('The processing service is starting. Please try again in a moment.')}if(!r.ok){let msg=`Request failed (${r.status})`;try{msg=(await r.json()).detail||msg}catch{}throw Error(msg)}return r}
const json=(p,o)=>api(p,o).then(r=>r.json());
document.documentElement.dataset.theme='dark';
function closeDrawers(){document.body.classList.remove('history-open','about-open');$$('.rail-btn').forEach(b=>b.classList.remove('active'));$$('.view').forEach(v=>v.hidden=true);$('#workspaceView').hidden=false;$$('[data-view="workspace"]').forEach(b=>b.classList.add('active'));}
function showView(name){if(!['workspace','history','about'].includes(name))return;closeDrawers();if(name==='workspace')return;$('#workspaceView').hidden=true;$('#historyView').hidden=name!=='history';$('#aboutView').hidden=name!=='about';$(`[data-view="${name}"]`)?.classList.add('active');if(name==='history'){loadHistory().then(renderHistory);renderHistory();}}
$$('[data-view]').forEach(b=>b.addEventListener('click',()=>showView(b.dataset.view)));$('#closeHistory')?.addEventListener('click',()=>showView('workspace'));$$('[data-close-drawer]').forEach(b=>b.addEventListener('click',()=>showView('workspace')));document.addEventListener('keydown',e=>{if(e.key==='Escape')showView('workspace')});
// The compact recent-extractions list lives directly below the upload drop area.
const recentPanel=$('#historyPanel');if(recentPanel){recentPanel.classList.add('recent-inline');$('.upload-panel')?.appendChild(recentPanel);}
function setupProgress(d){$('#setupCard').hidden=false;$('#setupStage').textContent=`${d.label||d.stage}: ${d.message||''}`;$('#setupLog').textContent=d.message||'';if(d.percent!=null){$('#setupProgressBar').style.width=Math.max(0,Math.min(100,d.percent))+'%';$('#setupPercent').textContent=Math.round(d.percent)+'%'}if(d.stage==='ready'){localStorage.removeItem('invoice-ai-disconnected');isReady=true;$('#setupCard').hidden=true;$('#connectBtn').textContent='AI connected';$('#connectionStatus').classList.add('ready');$('#connectionStatus').innerHTML='<i></i> AI connected';toast('AI model connected. You’re ready to extract invoices.')}if(d.stage==='error'){isReady=false;$('#connectBtn').textContent='Retry connection';$('#setupStage').textContent=d.message}}
if(window.desktop?.onSetupProgress)window.desktop.onSetupProgress(setupProgress);
if(window.desktop?.onBackendLog)window.desktop.onBackendLog(d=>console.log(d.message));

async function loadOllamaModels(){try{const data=await json('/api/ollama/models');const label=$('#modelStatus'),select=$('#modelSelect');if(label)label.textContent=data.connected?'Local':'Ollama offline';if(select){const names=data.models||[];select.innerHTML=names.length?names.map(n=>`<option value="${esc(n)}" ${n===data.configured_model?'selected':''}>${esc(n)}</option>`).join(''):'<option value="">No local models</option>';select.disabled=!data.connected||!names.length;}}catch{const label=$('#modelStatus');if(label)label.textContent='Ollama offline'}}
$('#modelSelect')?.addEventListener('change',async e=>{const name=e.target.value;if(!name)return;try{await json('/api/ollama/models/select',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});toast(`Using ${name}`);await checkStatus()}catch(err){toast(err.message,true);loadOllamaModels()}});
async function checkStatus(){try{const h=await json('/api/health');isReady=!!(h.ollama&&h.model)&&localStorage.getItem('invoice-ai-disconnected')!=='1';if(isReady){$('#connectionStatus').classList.add('ready');$('#connectionStatus').innerHTML='<i></i> AI connected';$('#connectBtn').textContent='Disconnect AI';$('#setupCard').hidden=true;}else{$('#connectionStatus').innerHTML='<i></i> AI not connected';$('#connectBtn').textContent='Connect AI model';$('#setupCard').hidden=false;}}catch{$('#connectionStatus').innerHTML='<i></i> Starting services';setTimeout(checkStatus,1500)}}
async function connectAI(){localStorage.removeItem('invoice-ai-disconnected');if(!window.desktop){toast('Desktop setup is available in the installed Windows application.',true);return}$('#connectBtn').disabled=true;$('#connectBtn').textContent='Connecting…';$('#setupCard').hidden=false;$('#setupStage').textContent='Preparing setup…';$('#setupLog').textContent='';try{const r=await window.desktop.connectAI();if(!r.ok)toast(r.error||'Setup failed',true);else{await checkStatus();}}catch(e){toast(e.message,true)}finally{$('#connectBtn').disabled=false;if(!isReady)$('#connectBtn').textContent='Retry connection'}}
async function disconnectAI(){localStorage.setItem('invoice-ai-disconnected','1');isReady=false;$('#connectionStatus').classList.remove('ready');$('#connectionStatus').innerHTML='<i></i> AI disconnected';$('#connectBtn').textContent='Connect AI model';toast('AI disconnected. Your installed model and invoice history are unchanged.')}
$('#connectBtn').onclick=()=>isReady?disconnectAI():connectAI();
function resetUploadState(message='',error=false){
 extractionInProgress=false;activeExtractionToken=null;cancelRequested=false;
 $('.upload-panel')?.classList.remove('extracting','is-processing');
 $('#extractProgress').hidden=true;$('#confirmRow')?.removeAttribute('hidden');
 if(selectedFile){$('#selectedFile').hidden=false;$('#selectedName').textContent=selectedFile.name;$('#selectedSize').textContent=(selectedFile.size/1024/1024).toFixed(2)+' MB';$('#clearFile').hidden=false;$('#fileTitle').textContent='File ready';$('#fileSubtitle').textContent='Review the selected file, then confirm extraction';}
 else{$('#selectedFile').hidden=true;$('#clearFile').hidden=true;$('#fileTitle').textContent='Upload files';$('#fileSubtitle').textContent='PDF, PNG or JPG · up to 20 MB';}
 $('#extractBtn').disabled=!selectedFile;$('#extractBtn').innerHTML='Extract <span aria-hidden="true">↗</span>';
 $('#cancelExtractBtn').disabled=false;$('#cancelExtractBtn').textContent='Stop extraction';
 $('#uploadMessage').textContent=message;$('#uploadMessage').className='message'+(error?' error':'');
 $('#fileInput').value='';
}
function chooseFile(file){if(!file||extractionInProgress)return;if(!/\.(pdf|png|jpe?g)$/i.test(file.name)){toast('Choose a PDF, PNG, or JPG invoice.',true);return}if(file.size>20*1024*1024){toast('This file is larger than 20 MB.',true);return}selectedFile=file;$('#selectedFile').hidden=false;$('#selectedName').textContent=file.name;$('#selectedSize').textContent=(file.size/1024/1024).toFixed(2)+' MB';$('#fileTitle').textContent='File ready';$('#fileSubtitle').textContent='Review the selected file, then confirm extraction';$('#clearFile').hidden=false;$('#extractBtn').disabled=false;$('#uploadMessage').textContent='';}
$('#chooseBtn').onclick=async e=>{e.stopPropagation();if(extractionInProgress)return;if(window.desktop){const f=await window.desktop.chooseInvoice();if(f){const bytes=Uint8Array.from(atob(f.data),c=>c.charCodeAt(0));const ext=f.name.split('.').pop().toLowerCase();const type=ext==='pdf'?'application/pdf':ext==='png'?'image/png':'image/jpeg';chooseFile(new File([bytes],f.name,{type,lastModified:Date.now()}))}}else $('#fileInput').click()};
$('#fileInput').onchange=e=>chooseFile(e.target.files[0]);
$('#dropzone').ondragover=e=>{if(extractionInProgress)return;e.preventDefault();$('#dropzone').classList.add('dragover')};$('#dropzone').ondragleave=()=>$('#dropzone').classList.remove('dragover');$('#dropzone').ondrop=e=>{if(extractionInProgress)return;e.preventDefault();$('#dropzone').classList.remove('dragover');chooseFile(e.dataTransfer.files[0])};$('#dropzone').onclick=e=>{if(extractionInProgress||e.target.closest('button'))return;$('#fileInput').click()};
$('#clearFile').onclick=e=>{e.stopPropagation();selectedFile=null;$('#selectedFile').hidden=true;$('#extractBtn').disabled=true;$('#fileTitle').textContent='Upload files';$('#fileSubtitle').textContent='PDF, PNG or JPG · up to 20 MB';$('#clearFile').hidden=true;$('#fileInput').value=''};
function showExtractionAnimation(){const host=$('#resultsContent');if(!host)return;host.innerHTML=`<div class="sift-animation" role="status" aria-live="polite"><div class="sift-visual"><div class="sift-orbit orbit-a"></div><div class="sift-orbit orbit-b"></div><div class="sift-doc-stack"><i></i><i></i><i></i></div><div class="sift-scan"></div><div class="sift-spark">✦</div></div><strong>InvoSift is reading your invoice</strong><p id="siftStatus">Preparing the document for extraction…</p><div class="sift-stages"><span>Read</span><b></b><span>Understand</span><b></b><span>Verify</span></div></div>`;}
async function updateExtractionProgress(token){
 const order=['reading','render','ocr','ai_extraction','validation','save'];
 const d=await json('/api/progress/'+encodeURIComponent(token));
 const pct=d.percent!==null&&d.percent!==undefined&&Number.isFinite(Number(d.percent))?Math.max(0,Math.min(100,Number(d.percent))):null;
 $('#extractStage').textContent=d.detail||({uploading:'Receiving invoice…',queued:'Preparing extraction…',reading:'Reading document…',render:'Rendering document…',ocr:'Recognising text…',ai_extraction:'Extracting invoice fields…',validation:'Validating extracted data…',save:'Saving extraction…',cancelling:'Stopping extraction…'}[d.stage]||d.stage);
 if(!cancelRequested)$('#processingHint').textContent=d.detail||'Your invoice is being processed locally.';
 $('#extractPercent').textContent=pct===null?'…':Math.round(pct)+'%';
 if(pct!==null)$('#extractProgressFill').style.width=pct+'%';
 const siftStatus=$('#siftStatus');if(siftStatus)siftStatus.textContent=d.detail||'Working through the invoice…';
 $$('#extractSteps [data-step]').forEach(el=>{const index=order.indexOf(el.dataset.step),active=order.indexOf(d.stage),effectiveStage=d.stage==='reading'?'render':d.stage;el.classList.toggle('done',d.stage==='complete'||(index>=0&&active>=0&&index<active));el.classList.toggle('active',el.dataset.step===effectiveStage)});
 return d;
}
async function waitForExtraction(token){
 let transientFailures=0;
 while(activeExtractionToken===token){
  let state;
  try{state=await updateExtractionProgress(token);transientFailures=0;}
  catch(err){if(++transientFailures>=8)throw Error('Lost contact with the extraction service. Check that InvoSift is still running, then retry.');await new Promise(r=>setTimeout(r,400));continue;}
  // The worker adds invoice_id after the processor returns. Ignore any early
  // complete envelope without that id instead of treating it as a finished job.
  if(state.stage==='complete'&&state.invoice_id)return state;
  if(state.stage==='cancelled')throw Error('Extraction stopped by user.');
  if(state.stage==='error')throw Error(state.detail||'Invoice extraction failed.');
  await new Promise(r=>setTimeout(r,350));
 }
 throw Error(cancelRequested?'Extraction stopped by user.':'Extraction was interrupted.');
}
$('#extractBtn').onclick=async()=>{
 if(!selectedFile||extractionInProgress)return;
 if(!isReady){toast('Connect the AI model before extracting.',true);return;}
 if(selectedFile.path){toast('Please use Choose file inside the app to load this file.');return;}
 const file=selectedFile,previousInvoice=currentInvoice,fd=new FormData();fd.append('file',file);
 const token=(crypto.randomUUID?crypto.randomUUID():`${Date.now()}-${Math.random().toString(16).slice(2)}`);
 activeExtractionToken=token;extractionInProgress=true;cancelRequested=false;
 try{
  $('.upload-panel')?.classList.add('extracting','is-processing');
  $('#resultsPanel').hidden=false;$('#resultsPanel')?.classList.add('results-open','is-extracting');showExtractionAnimation();
  // Keep the processing animation as the only visible content in this panel.
  // Existing invoice actions are hidden until the new extraction reaches a terminal state.
  $('#clearDetailsBtn').hidden=true;$('#resultActions').hidden=true;
  $('#selectedFile').hidden=true;$('#confirmRow')?.setAttribute('hidden','');$('#extractBtn').disabled=true;
  $('#extractProgress').hidden=false;$('#processingFileName').textContent=file.name;
  $('#extractStage').textContent='Preparing upload…';$('#extractPercent').textContent='0%';$('#extractProgressFill').style.width='0%';
  $('#processingHint').textContent='Sending the invoice to the local extraction service.';
  $('#uploadMessage').textContent='';$('#uploadMessage').className='message';
  // $() returns one element; use $$() for the collection. Keep this inside try so
  // any future UI setup failure is reported and the upload state is always reset.
  $$('#extractSteps [data-step]').forEach(el=>el.classList.remove('done','active'));
  $('#extractSteps [data-step="render"]')?.classList.add('active');

  // Start polling before the multipart upload finishes. This exposes the backend's
  // uploading/queued states and lets Stop work while a large file is still arriving.
  const progressOutcome=waitForExtraction(token).then(state=>({state}),error=>({error}));
  try{
   const ack=await json('/api/invoices/upload',{method:'POST',headers:{'X-Progress-Token':token},body:fd});
   if(ack?.stage==='error')throw Error(ack.detail||'The extraction service rejected the upload.');
  }catch(uploadError){
   // Ensure the poller reaches a terminal state even if the upload request failed.
   try{await json('/api/invoices/cancel/'+encodeURIComponent(token),{method:'POST'});}catch{}
   // If the multipart request failed before the backend registered the job, stop
   // the local poller explicitly; otherwise it could wait on a token that has no worker.
   activeExtractionToken=null;
   await progressOutcome;
   throw uploadError;
  }
  const outcome=await progressOutcome;
  if(outcome.error)throw outcome.error;
  const completed=outcome.state;
  if(completed.stage!=='complete'||!completed.invoice_id)throw Error(completed.detail||'The extraction job ended without a saved invoice.');
  currentInvoice=await json('/api/invoices/'+Number(completed.invoice_id));
  $('#extractProgressFill').style.width='100%';$('#extractPercent').textContent='100%';$('#extractStage').textContent='Extraction complete';$('#processingHint').textContent='Saved to your extraction history.';
  $$('#extractSteps [data-step]').forEach(el=>el.classList.add('done'));
  selectedFile=null;resetUploadState('Extraction complete. Review the extracted fields.');
  await loadHistory();await renderInvoice(currentInvoice);chatInvoice=currentInvoice;refreshChatInvoiceOptions();
  $('#chatInput').placeholder=`Ask about ${(currentInvoice.data||currentInvoice).invoice_number||currentInvoice.original_filename||'this invoice'}…`;
  appendChatNotice(chatInvoice);toast('Invoice extracted successfully. Ready in AI chat.');
 }catch(e){
  const stopped=cancelRequested||/stopped by user|extraction stopped|cancelled/i.test(e.message);
  if(stopped)selectedFile=null;
  resetUploadState(stopped?'Extraction stopped. Choose another file when you are ready.':e.message,!stopped);
  $('#resultsPanel')?.classList.remove('is-extracting');
  if(previousInvoice){renderInvoice(previousInvoice);}
  else{
   $('#resultTitle').textContent='';$('#resultActions').hidden=true;$('#clearDetailsBtn').hidden=true;
   $('#resultsContent').innerHTML='<div class="empty-state large"><div class="empty-art">▤</div><strong>No invoice selected</strong><p>Upload and confirm an invoice to see the extracted fields here.</p></div>';
  }
  if(stopped){toast('Extraction stopped. You can choose another file.');await loadHistory();}else toast(e.message||'Invoice extraction failed.',true);
 }finally{
  if(activeExtractionToken===token){activeExtractionToken=null;extractionInProgress=false;$('.upload-panel')?.classList.remove('extracting','is-processing');}
 }
};
$('#cancelExtractBtn').onclick=async()=>{
 const token=activeExtractionToken;if(!token||!extractionInProgress||cancelRequested)return;
 cancelRequested=true;$('#cancelExtractBtn').disabled=true;$('#cancelExtractBtn').textContent='Stopping…';
 $('#processingHint').textContent='Sending an independent stop request to the extraction service…';
 try{
  const result=await json('/api/invoices/cancel/'+encodeURIComponent(token),{method:'POST'});
  if(!result.ok&&!['complete','cancelled','error'].includes(result.stage))throw Error(result.detail||'The extraction service did not acknowledge the stop request.');
  $('#processingHint').textContent=result.stage==='complete'?'Extraction finished just before the stop request.':'Stop acknowledged. Waiting for the active processing stage to release…';
 }catch(e){
  // Keep cancellation latched if the request may have reached the backend; polling
  // remains authoritative and will either report cancelled or surface a service error.
  $('#processingHint').textContent=`Stop request could not be confirmed: ${e.message}`;
  toast(e.message,true);
 }
};
function refreshChatInvoiceOptions(){const select=$('#chatInvoiceSelect');if(!select)return;const selected=chatInvoice?String(chatInvoice.id):'general';select.innerHTML='<option value="general">General conversation</option>'+invoices.map(i=>`<option value="${i.id}">${esc(i.invoice_number||i.supplier_name||i.original_filename)}</option>`).join('');select.value=[...select.options].some(o=>o.value===selected)?selected:'general';select.setAttribute('title',select.options[select.selectedIndex]?.text||'General conversation')}
$('#chatInvoiceSelect')?.addEventListener('change',async e=>{const value=e.target.value;chatHistory=[];$('#chatMessages').innerHTML='';if(value==='general'){chatInvoice=null;$('#chatInput').placeholder='Ask a general invoice or accounting question…';$('#chatMessages').innerHTML='<div class="chat-welcome"><div><strong>What would you like to know?</strong><p>Ask general questions about invoices, GST, accounting fields or extraction.</p></div></div>';return;}try{chatInvoice=await json('/api/invoices/'+Number(value));const d=chatInvoice.data||chatInvoice;$('#chatInput').placeholder=`Ask about ${d.invoice_number||chatInvoice.original_filename||'this invoice'}…`;appendChatNotice(chatInvoice,'Ready to work with this invoice');}catch(err){toast(err.message,true)}});
function appendChatNotice(inv,label='Ready to work with this invoice'){const d=inv?.data||inv||{};const name=d.invoice_number||inv?.invoice_number||inv?.original_filename||'your invoice';const notice=document.createElement('div');notice.className='chat-system-notice';notice.innerHTML=`<span class="notice-pulse"></span><span><strong>${esc(label)}</strong><small>${esc(name)} is available in this chat.</small></span>`;$('#chatMessages').appendChild(notice);$('#chatMessages').scrollTop=$('#chatMessages').scrollHeight}
async function loadHistory(retries=12){for(let attempt=0;attempt<=retries;attempt++){try{const latest=await json('/api/invoices');if(!Array.isArray(latest))throw Error('The history service returned an invalid response.');invoices=latest;refreshChatInvoiceOptions();renderRecent();$('#historyCount').textContent=`${invoices.length} invoice${invoices.length===1?'':'s'} saved`;renderHistory();return invoices}catch(e){if(attempt===retries){const message=esc(`Could not load extraction history: ${e.message}`);const errorMarkup=`<div class="empty-state history-load-error"><strong>History unavailable</strong><p>${message}</p><button class="btn small" data-retry-history>Retry</button></div>`;if($('#fullHistory'))$('#fullHistory').innerHTML=errorMarkup;if($('#recentList'))$('#recentList').innerHTML=errorMarkup;$$('[data-retry-history]').forEach(button=>button.addEventListener('click',()=>loadHistory()));toast(`Could not load extraction history: ${e.message}`,true);return null}await new Promise(r=>setTimeout(r,750));}}return null}
function dateFmt(v){if(!v)return'—';try{return new Date(v).toLocaleDateString()}catch{return v}}
function renderRecent(){const q=($('#recentSearch')?.value||'').toLowerCase();let list=invoices.filter(i=>!['failed','processing'].includes(String(i.status||'').toLowerCase())&&`${i.original_filename||''} ${i.invoice_number||''} ${i.supplier_name||''}`.toLowerCase().includes(q));list.sort((a,b)=>String(b.created_at||'').localeCompare(String(a.created_at||''))||Number(b.id)-Number(a.id));list=list.slice(0,5);$('#recentList').innerHTML=list.length?list.map(i=>`<div class="recent-item ${currentInvoice?.id===i.id?'selected':''}" data-id="${i.id}"><div class="mini-doc">PDF</div><div class="recent-info"><strong>${esc(i.supplier_name||i.original_filename)}</strong><small>${esc(i.invoice_number||'Invoice number unavailable')} · ${esc(dateFmt(i.created_at))}</small></div><div class="recent-total">${money(i.grand_total,i.currency)}</div></div>`).join(''):'<div class="empty-state">No matching invoices yet.</div>';$$('#recentList [data-id]').forEach(el=>el.onclick=()=>openInvoice(Number(el.dataset.id)))}
$('#recentSearch').oninput=renderRecent;$('#historySort')?.addEventListener('change',renderRecent);$('#historySearch').oninput=renderHistory;$('#historySortFull')?.addEventListener('change',renderHistory);
function renderHistory(){const q=($('#historySearch')?.value||'').toLowerCase();const list=invoices.filter(i=>`${i.original_filename} ${i.invoice_number||''} ${i.supplier_name||''}`.toLowerCase().includes(q));const sort=$('#historySortFull')?.value||'newest';list.sort((a,b)=>sort==='oldest'?String(a.invoice_date||'').localeCompare(String(b.invoice_date||'')):sort==='invoice-newest'?String(b.invoice_date||'').localeCompare(String(a.invoice_date||'')):sort==='extracted-newest'?String(b.created_at).localeCompare(String(a.created_at)):sort==='az'?String(a.supplier_name||a.original_filename).localeCompare(String(b.supplier_name||b.original_filename)):sort==='za'?String(b.supplier_name||b.original_filename).localeCompare(String(a.supplier_name||a.original_filename)):String(b.created_at).localeCompare(String(a.created_at)));$('#fullHistory').innerHTML=list.length?list.map(i=>`<div class="history-item"><div class="mini-doc">PDF</div><div class="recent-info" data-open="${i.id}"><strong>${esc(i.supplier_name||i.original_filename)}</strong><small>${esc(i.original_filename)} · ${esc(i.invoice_number||'No invoice number')} · ${esc(dateFmt(i.created_at))}</small></div><div class="recent-total">${money(i.grand_total,i.currency)}</div><div class="actions"><button class="btn small" data-open="${i.id}">Open</button><button class="btn small" data-extraction-details="${i.id}">Extraction details</button><button class="danger-btn" data-delete="${i.id}">Delete</button></div></div>`).join(''):'<div class="empty-state">No invoice history found.</div>';$$('[data-open]').forEach(el=>el.onclick=()=>openExtractionDetails(Number(el.dataset.open)));$$('[data-extraction-details]').forEach(el=>el.onclick=()=>openExtractionInfoPanel(Number(el.dataset.extractionDetails)));$$('[data-delete]').forEach(el=>el.onclick=()=>deleteInvoice(Number(el.dataset.delete)))}
async function openExtractionDetails(id){try{const inv=await json('/api/invoices/'+id);$('#workspaceView').hidden=true;$('#historyView').hidden=true;$('#aboutView').hidden=true;$('#extractionDetailsView').hidden=false;$$('.rail-btn').forEach(b=>b.classList.toggle('active',b.dataset.view==='history'));const d=inv.data||inv;$('#detailHeading').textContent=d.invoice_number?`Invoice ${d.invoice_number}`:(inv.supplier_name||inv.original_filename||'Extraction details');$('#detailSubheading').textContent=`${inv.original_filename||'Source file'} · Extracted ${dateTimeFmt(inv.created_at)}`;$('#detailContent').innerHTML=invoiceContentMarkup(inv);$('#detailActions').innerHTML=['excel','json','csv','txt','copy-text'].map(k=>`<button class="btn small" data-detail-export="${k}">${k==='txt'?'TXT':k==='copy-text'?'Copy':k.toUpperCase()}</button>`).join('');$$('[data-detail-export]').forEach(b=>b.onclick=()=>exportInvoice(b.dataset.detailExport,inv));const src=`${API}/api/invoices/${id}/file`;$('#documentFrame').src=src;$('#openDocument').onclick=()=>{if(window.desktop?.openExternal)window.desktop.openExternal(src);else window.open(src,'_blank','noopener')};$('#backToHistory').onclick=()=>showView('history');}catch(e){toast(e.message,true)}}
async function openExtractionInfoPanel(id){try{const inv=await json('/api/invoices/'+id);const d=inv.data||inv;$('#extractionInfoHeading').textContent=d.invoice_number?`Invoice ${d.invoice_number}`:(inv.supplier_name||inv.original_filename||'Extraction details');$('#extractionInfoSubheading').textContent=`${inv.original_filename||'Source file'} · Extracted ${dateTimeFmt(inv.created_at)}`;$('#extractionInfoContent').innerHTML=invoiceContentMarkup(inv);$('#extractionInfoActions').innerHTML=['excel','json','csv','txt','copy-text'].map(k=>`<button class="btn small" data-info-export="${k}">${k==='txt'?'TXT':k==='copy-text'?'Copy text':k.toUpperCase()}</button>`).join('');$$('[data-info-export]').forEach(b=>b.onclick=()=>exportInvoice(b.dataset.infoExport,inv));$('#extractionInfoOverlay').hidden=false;$('#closeExtractionInfo').focus();}catch(e){toast(e.message,true)}}
$('#closeExtractionInfo')?.addEventListener('click',()=>{$('#extractionInfoOverlay').hidden=true});
$('#extractionInfoOverlay')?.addEventListener('click',e=>{if(e.target.id==='extractionInfoOverlay')$('#extractionInfoOverlay').hidden=true});
function dateTimeFmt(v){if(!v)return'—';const d=new Date(v);return Number.isNaN(d.getTime())?v:d.toLocaleString()}
$('#backToHistory')?.addEventListener('click',()=>showView('history'));
async function openInvoice(id){try{currentInvoice=await json('/api/invoices/'+id);$('#resultsPanel').hidden=false;await renderInvoice(currentInvoice);renderRecent()}catch(e){toast(e.message,true)}}
function invoiceContentMarkup(inv){const d=inv.data||inv;const sup=d.supplier||{};const cus=d.customer||{};const tiles=[['Supplier',sup.name],['Customer',cus.name],['Invoice date',d.invoice_date],['Due date',d.due_date],['Invoice number',d.invoice_number],['Balance due',money(d.financials?.balance_due??d.balance_due,d.currency)],['Grand total',money(d.financials?.grand_total??d.grand_total,d.currency),'total']];return `<div class="invoice-summary">${tiles.map(([l,v,c])=>`<div class="data-tile ${c||''}"><label>${esc(l)}</label><strong>${esc(v??'Not found')}</strong></div>`).join('')}</div><div class="result-subhead">Line items · ${(d.items||[]).length}</div>${(d.items||[]).length?`<div style="overflow:auto"><table class="items-table"><thead><tr><th>Description</th><th>Qty</th><th>Unit price</th><th>Total</th></tr></thead><tbody>${d.items.map(it=>`<tr><td>${esc(it.description)}</td><td>${esc(it.quantity)}</td><td>${money(it.unit_price,d.currency)}</td><td>${money(it.total,d.currency)}</td></tr>`).join('')}</tbody></table></div>`:'<p class="muted">No line items were detected.</p>'}<div class="result-subhead">Validation</div><p class="muted">${inv.needs_review?'Some fields need a quick review. Check the original invoice before relying on the values.':'Extraction completed. Please verify important amounts against the source document.'}</p>`}
function renderInvoice(inv){$('#resultsPanel')?.classList.remove('is-extracting');currentInvoice=inv;$('#clearDetailsBtn').hidden=false;$('#resultsPanel').hidden=false;$('#resultsPanel')?.classList.add('results-open');const d=inv.data||inv;$('#resultTitle').textContent=d.invoice_number?`Invoice ${d.invoice_number}`:'Invoice overview';$('#resultActions').hidden=false;$('#resultsContent').innerHTML=invoiceContentMarkup(inv);return inv}

function clearExtractionDetails(){if(extractionInProgress){toast('Wait until extraction finishes before clearing the details.');return;}currentInvoice=null;$('#resultTitle').textContent='';$('#resultActions').hidden=true;$('#clearDetailsBtn').hidden=true;$('#resultsContent').innerHTML='<div class="empty-state large"><div class="empty-art">▤</div><strong>No invoice selected</strong><p>Upload an invoice or choose one from history to see its extracted details here.</p></div>';renderRecent();}
$('#clearDetailsBtn')?.addEventListener('click',clearExtractionDetails);
async function loadInvoiceDetail(id){return json('/api/invoices/'+id)}
function wireSuggestions(){$$('.suggestions button').forEach(b=>b.onclick=()=>{ $('#chatInput').value=b.textContent;$('#chatForm').requestSubmit()})}
function addChat(role,text){const el=document.createElement('div');el.className='chat-line '+role;const bubble=document.createElement('div');bubble.className='bubble';bubble.textContent=text;el.appendChild(bubble);$('#chatMessages').appendChild(el);$('#chatMessages').scrollTop=$('#chatMessages').scrollHeight}
$('#chatInput').addEventListener('input',()=>{const el=$('#chatInput');el.style.height='auto';el.style.height=Math.min(el.scrollHeight,190)+'px';$('#chatPanel')?.classList.toggle('chat-expanded',el.value.length>0||chatHistory.length>0)});$('#chatInput').addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();$('#chatForm').requestSubmit()}});$('#chatForm').onsubmit=async e=>{e.preventDefault();const msg=$('#chatInput').value.trim();if(!msg)return;if(!isReady){toast('Connect the AI model first.',true);return}$('#chatInput').value='';$('#chatInput').style.height='35px';$('#chatPanel').classList.add('chat-expanded');addChat('user',msg);chatHistory.push({role:'user',content:msg});const pending=document.createElement('div');pending.className='chat-line assistant pending';pending.innerHTML='<div class="bubble">Thinking</div>'; $('#chatMessages').appendChild(pending);try{const out=await json('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:msg,invoice_id:chatInvoice?.id??null,history:chatHistory.slice(-12)})});pending.remove();addChat('assistant',out.reply||'I could not produce a response.');$('#chatMessages').scrollTo({top:$('#chatMessages').scrollHeight,behavior:'smooth'});chatHistory.push({role:'assistant',content:out.reply||''})}catch(err){pending.remove();addChat('assistant',err.message);toast(err.message,true)}};
$$('[data-export]').forEach(b=>b.onclick=()=>exportInvoice(b.dataset.export));
async function exportInvoice(kind,invoiceOverride=null){const invoice=invoiceOverride||currentInvoice;if(!invoice)return toast('Select an invoice first.',true);const urls={excel:`/api/invoices/${invoice.id}/export/excel`,json:`/api/invoices/${invoice.id}/export/json`,csv:`/api/invoices/${invoice.id}/export/csv`,txt:`/api/invoices/${invoice.id}/export/text`};const names={excel:`invoice_${invoice.id}.xlsx`,json:`invoice_${invoice.id}.json`,csv:`invoice_${invoice.id}.csv`,txt:`invoice_${invoice.id}.txt`};try{if(kind==='copy-text'){const r=await api(urls.txt);const txt=await r.text();if(window.desktop?.copyText)await window.desktop.copyText(txt);else await navigator.clipboard.writeText(txt);toast('Invoice report copied to clipboard.');return}if(window.desktop){const r=await window.desktop.saveExport({url:API+urls[kind],defaultName:names[kind]});if(!r.canceled)toast('Export saved.')}else{const r=await api(urls[kind]);const blob=await r.blob();const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=names[kind];a.click();URL.revokeObjectURL(a.href)}}catch(e){toast(e.message,true)}}
$('#exportAllBtn').onclick=async()=>{try{if(window.desktop){const r=await window.desktop.saveExport({url:API+'/api/invoices/export/excel',defaultName:'invoice_history.xlsx'});if(!r.canceled)toast('History export saved.')}else toast('Use the installed desktop app to save the consolidated export.')}catch(e){toast(e.message,true)}};
async function deleteInvoice(id){if(!confirm('Delete this invoice and its stored source file? This cannot be undone.'))return;try{await api('/api/invoices/'+id,{method:'DELETE'});if(chatInvoice?.id===id){chatInvoice=null;chatHistory=[];refreshChatInvoiceOptions();$('#chatMessages').innerHTML='<div class="chat-welcome"><div><strong>What would you like to know?</strong><p>Ask a general invoice or accounting question.</p></div></div>';}if(currentInvoice?.id===id){currentInvoice=null;$('#resultsPanel').hidden=false;$('#resultActions').hidden=true;$('#resultsContent').innerHTML='<div class="empty-state large"><div class="empty-art">▤</div><strong>No invoice selected</strong><p>Choose an invoice from history.</p></div>';}await loadHistory();renderHistory();toast('Invoice deleted from history.')}catch(e){toast(e.message,true)}}
checkStatus();loadOllamaModels();loadHistory();setInterval(()=>{checkStatus();loadOllamaModels()},15000);

// Secure frameless-window controls exposed by the preload bridge.
$('#winMin')?.addEventListener('click',()=>window.desktop?.windowMinimize?.());
$('#winMax')?.addEventListener('click',async()=>{await window.desktop?.windowToggleMaximize?.();const maximized=await window.desktop?.windowIsMaximized?.();$('#winMax').textContent=maximized?'❐':'□';});
$('#winClose')?.addEventListener('click',()=>window.desktop?.windowClose?.());

// Always start with an unselected details panel; history is loaded independently.
clearExtractionDetails();
