'use strict';
const $ = id => document.getElementById(id);
const fields = ['prediction','plan','analysis','claim','evidence','reasoning','reflection','transfer'];
const apiBase = (window.APP_CONFIG.apiBase || (location.port === '8000' ? location.origin : '')).replace(/\/$/, '');
let lesson, session, base, pending = [], drafts = {}, busy = false, running = false, memoryOnly = false;
let saveTimer, animation;
const uid = () => crypto.randomUUID ? crypto.randomUUID() : Date.now() + '_' + Math.random().toString(36).slice(2);
const empty = () => ({answers:Object.fromEntries(fields.map(k=>[k,''])),target:'force',trials:[],submitted:false});
function readStore(k, fallback) { try { return JSON.parse(localStorage.getItem(k)) || fallback; } catch { return fallback; } }
function putStore(k, v) { try { localStorage.setItem(k,JSON.stringify(v)); } catch { memoryOnly=true; $('message').textContent='裝置儲存空間不足或不允許儲存。請保持頁面開啟，並立即匯出紀錄。'; } }
// Keep the bearer token within this tab, while retaining offline answers separately.
try {
  session=JSON.parse(sessionStorage.getItem('inquiry-session')) || readStore('inquiry-session',null);
  if(session)sessionStorage.setItem('inquiry-session',JSON.stringify(session));
  localStorage.removeItem('inquiry-session');
} catch {session=null;}
function storageKey() { return session ? 'inquiry-student-' + session.student_id : 'inquiry-demo-' + lesson.id; }
function persist() { putStore(storageKey(), {base,pending,drafts,lesson}); updateSync(); }
function loadLocal() { const old=readStore(storageKey(),{}); if(session&&old.lesson)configure(old.lesson);base=old.base || empty(); pending=old.pending || []; drafts=old.drafts || {}; }
function event(type,payload) { pending.push({id:uid(),type,payload,client_time:new Date().toISOString()}); persist(); render(); }
function localState() {
  const s=JSON.parse(JSON.stringify(base || empty()));
  for (const e of pending) {
    if(e.type==='answer') s.answers[e.payload.field]=e.payload.value;
    if(e.type==='target') s.target=e.payload.value;
    if(e.type==='trial') { const p=e.payload; s.trials.push({...p,acceleration:p.force/p.mass,displacement:2*p.force/p.mass}); }
    if(e.type!=='slider') s.submitted=e.type==='submit';
  }
  Object.assign(s.answers,drafts);
  if(Object.keys(drafts).length) s.submitted=false;
  return s;
}
function captureAnswers() {
  clearTimeout(saveTimer);
  for (const k of Object.keys(drafts)) {
    const value=drafts[k];
    delete drafts[k];
    event('answer',{field:k,value});
  }
  persist(); flush();
}
async function request(path, method='GET', body) {
  const controller=new AbortController(); const timeout=setTimeout(()=>controller.abort(),10000);
  try {
    const r=await fetch(apiBase+path,{method,signal:controller.signal,headers:{'Content-Type':'application/json','ngrok-skip-browser-warning':'1',...(session?{Authorization:'Bearer '+session.token}:{})},...(body?{body:JSON.stringify(body)}:{})});
    const content=await r.text(); let data;
    try { data=JSON.parse(content); } catch { throw new Error('後端沒有回傳資料。請教師檢查後端網址與 ngrok 狀態。'); }
    if(!r.ok) throw new Error(data.error || '連線失敗');
    return data;
  } finally { clearTimeout(timeout); }
}
async function flush() {
  if(!session || !apiBase || busy || !pending.length) return;
  busy=true; updateSync();
  try {
    while(pending.length) {
      const batch=[];
      for(const e of pending.slice(0,40)) {
        if(new TextEncoder().encode(JSON.stringify({events:[...batch,e]})).length>240000)break;
        batch.push(e);
      }
      const r=await request('/api/events','POST',{events:batch});
      base=r.state; const ack=new Set(r.ack);
      pending=pending.filter(e=>!ack.has(e.id)); persist();
    }
    $('message').textContent=''; render();
  } catch(e) {
    $('message').textContent='尚未同步：'+e.message+'。請保留此頁；可匯出紀錄作備份。';
  } finally { busy=false; updateSync(); }
}
function updateSync() {
  if(!lesson) return;
  $('identity').textContent=session ? session.label : '學案預覽';
  $('sync').textContent=!session?'示範 · 僅此裝置':busy?'同步中…':pending.length?'待傳 '+pending.length+' 筆':'已同步';
  if(memoryOnly) $('sync').textContent+=' · 請匯出備份';
  $('logout').hidden=!session;
  $('mode').textContent=session ? '正式課堂 '+session.class_id+' · 答案、實驗數據及修改會傳送給教師。登入有效期 12 小時。' : '示範模式 · 操作只保存在這台裝置，不會傳給教師。正式課堂請使用個別登入碼。';
}
function render() {
  if(!lesson) return;
  const s=localState();
  for(const k of fields) if(document.activeElement!==$(k)) $(k).value=s.answers[k] || '';
  $('target').value=s.target;
  const ready=[!!(s.answers.prediction.trim()&&s.answers.plan.trim()),s.trials.length>=3,
    ['analysis','claim','evidence','reasoning'].every(k=>s.answers[k].trim()),['reflection','transfer'].every(k=>s.answers[k].trim()),!!s.submitted];
  const percent=ready.filter(Boolean).length*20; $('progress').value=percent; $('progressText').textContent=percent+'%';
  $('trialCount').textContent=s.trials.length;
  $('trials').replaceChildren();
  if(!s.trials.length) { const tr=document.createElement('tr'); const td=document.createElement('td'); td.colSpan=5; td.textContent='還沒有數據。寫下預測後，開始你的第一次實驗。'; tr.append(td); $('trials').append(tr); }
  s.trials.forEach((t,i)=>{ const tr=document.createElement('tr'); [i+1,t.force,t.mass,fmt(t.acceleration),fmt(t.displacement)].forEach(v=>{const td=document.createElement('td');td.textContent=v;tr.append(td);}); $('trials').append(tr); });
  $('submission').textContent=s.submitted ? (session?(pending.length?'提交已暫存，仍需等待同步。':'已提交並同步到教師端。修改內容後，請再次提交。'):'示範提交完成，未傳送給教師。') : '尚未提交；填寫內容會自動保存。';
  $('planHint').textContent=s.trials.length?'比較時檢查：另一個變量是否保持相同？至少測三個不同數值，再看趨勢。':'先記下預測與計劃，再進行第一次實驗。';
  drawChart(s.trials); updateSync();
}
const fmt=n=>Number(n).toFixed(2);
function svg(tag,attrs,text) { const el=document.createElementNS('http://www.w3.org/2000/svg',tag); for(const [k,v] of Object.entries(attrs))el.setAttribute(k,v);if(text!==undefined)el.textContent=text;return el; }
function drawChart(trials) {
  const el=$('chart');el.replaceChildren();const xkey=$('graphAxis').value, ckey=xkey==='force'?'mass':'force';
  const xmax=lesson[xkey].max, ymax=lesson.force.max/lesson.mass.min;
  el.append(svg('path',{d:'M55 18V195H675',stroke:'#b3c5bd',fill:'none'}));
  for(let i=0;i<=4;i++) {
    const y=195-i*40;el.append(svg('line',{x1:55,x2:675,y1:y,y2:y,stroke:'#e5ece7'}));
    el.append(svg('text',{x:48,y:y+4,'text-anchor':'end',fill:'#61737a','font-size':11},fmt(ymax*i/4)));
    el.append(svg('text',{x:55+620*i/4,y:215,'text-anchor':'middle',fill:'#61737a','font-size':11},fmt(xmax*i/4)));
  }
  el.append(svg('text',{x:60,y:12,fill:'#61737a','font-size':11},'加速度 / m/s²'));
  el.append(svg('text',{x:675,y:235,'text-anchor':'end',fill:'#61737a','font-size':11},xkey==='force'?'合力 / N':'質量 / kg'));
  const colors=['#087f78','#c47c2d','#5a71ac','#a95c82','#617c39'];
  const controls=[...new Set(trials.map(t=>t[ckey]))].sort((a,b)=>a-b);
  trials.forEach((t,i)=>{const hue=controls.indexOf(t[ckey])*137.508%360;const dot=svg('circle',{cx:55+t[xkey]/xmax*620,cy:195-t.acceleration/ymax*160,r:5,fill:'hsl('+hue+',55%,38%)',stroke:'white','stroke-width':1});dot.append(svg('title',{},'實驗 '+(i+1)+'：F='+t.force+' N，m='+t.mass+' kg，a='+fmt(t.acceleration)+' m/s²'));el.append(dot);});
}
function configure(l) {
  lesson=l;
  ['title','question','textbook','reading','predictionPrompt','planPrompt','analysisPrompt','reflectionPrompt','transferPrompt','modelNote'].forEach(k=>$(k).textContent=l[k]);
  $('meta').textContent=l.subject+' / '+l.duration+' MIN';
  for(const name of ['force','mass']) { const r=l[name], unit=name==='force'?'N':'kg'; Object.assign($(name),{min:r.min,max:r.max,step:r.step,value:r.initial}); $(name+'Range').textContent=r.min+'–'+r.max+' '+unit; }
  $('ticks').replaceChildren();const maxDistance=2*l.force.max/l.mass.min;
  for(let i=0;i<=4;i++) {const x=75+700*i/4; $('ticks').append(svg('line',{x1:x,x2:x,y1:158,y2:168,stroke:'#849ba4'}));$('ticks').append(svg('text',{x,y:193,'text-anchor':'middle'},fmt(maxDistance*i/4)+' m'));}
  sliders();
}
function sliders() {
  $('forceValue').textContent=$('force').value+' N';$('massValue').textContent=$('mass').value+' kg';
  if(!running) { $('acceleration').textContent='待測';$('distance').textContent='待測'; }
}
function exportRecord() {
  captureAnswers();
  const record={format:'s4-inquiry-backup-v1',lesson,student:session?{student_id:session.student_id,class_id:session.class_id,label:session.label}:null,state:localState(),confirmed:base,pending,exported_at:new Date().toISOString()};
  download('inquiry-record.json',JSON.stringify(record,null,2),'application/json');
}
function download(name,data,type) {const url=URL.createObjectURL(new Blob([data],{type}));const a=document.createElement('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
fields.forEach(k=>{$(k).maxLength=6000;$(k).addEventListener('input',()=>{drafts[k]=$(k).value;persist();clearTimeout(saveTimer);saveTimer=setTimeout(captureAnswers,700);});});
$('target').addEventListener('change',()=>{const value=$('target').value;captureAnswers();event('target',{value});flush();});
$('savePlan').onclick=()=>{captureAnswers();$('message').textContent='預測與計劃已保存在此裝置；正式課堂會自動同步。';};
['force','mass'].forEach(k=>{ $(k).oninput=sliders;$(k).onchange=()=>{event('slider',{force:+$('force').value,mass:+$('mass').value});flush();}; });
$('graphAxis').onchange=render;
$('run').onclick=()=>{
  captureAnswers(); const force=+$('force').value,mass=+$('mass').value,a=force/mass;
  $('arrow').style.opacity=force===0?'0':'1';
  const arrowEnd=86+50*force/lesson.force.max;
  $('arrow').setAttribute('d','M76 123H'+arrowEnd+' M'+(arrowEnd-10)+' 116L'+arrowEnd+' 123L'+(arrowEnd-10)+' 130');
  event('trial',{force,mass}); flush();
  running=true; $('run').disabled=true; $('force').disabled=true;$('mass').disabled=true;
  const start=performance.now();const maxDistance=2*lesson.force.max/lesson.mass.min;
  function frame(t) {const secs=Math.min((t-start)/1000,2);$('car').setAttribute('transform','translate('+(75+(0.5*a*secs*secs)/maxDistance*700)+' 0)');$('time').textContent='t = '+fmt(secs)+' s';$('acceleration').textContent=fmt(a);$('distance').textContent=fmt(0.5*a*secs*secs);if(secs<2)animation=requestAnimationFrame(frame);else{running=false;$('run').disabled=false;$('force').disabled=false;$('mass').disabled=false;}}
  cancelAnimationFrame(animation);animation=requestAnimationFrame(frame);
};
$('submit').onclick=()=>{
  captureAnswers();const s=localState();
  if(!fields.every(k=>s.answers[k].trim())||s.trials.length<3){$('message').textContent='請填寫所有文字欄位，並完成至少三次實驗。文字是否有科學推理將由教師評閱。';return;}
  event('submit',{});flush();
};
$('backup').onclick=exportRecord;
$('logout').onclick=()=>{captureAnswers();if(pending.length){$('message').textContent='尚有待傳紀錄，請先等候同步，或匯出備份後再切換。';return;}session=null;sessionStorage.removeItem('inquiry-session');localStorage.removeItem('inquiry-session');location.reload();};
$('joinForm').onsubmit=async e=>{
  e.preventDefault();if(!apiBase){$('joinError').textContent='教師尚未設定後端網址，目前只可示範。';return;}
  captureAnswers();
  if(session&&pending.length&&$('classId').value.trim()!==session.class_id){$('joinError').textContent='仍有待傳紀錄，請用原課堂和原學生的登入碼重新登入。';return;}
  try {const r=await request('/api/join','POST',{class_id:$('classId').value.trim(),code:$('joinCode').value.trim()});
    if(session&&pending.length&&r.student_id!==session.student_id)throw new Error('仍有待傳紀錄，只可用原學生的登入碼重新登入。');
    session={token:r.token,student_id:r.student_id,label:r.label,class_id:r.class_id};sessionStorage.setItem('inquiry-session',JSON.stringify(session));configure(r.lesson);loadLocal();base=r.state;persist();render();$('loginPanel').open=false;$('joinCode').value='';$('joinError').textContent='';flush();
  }catch(err){$('joinError').textContent=err.message;}
};
window.addEventListener('online',flush);
window.addEventListener('pagehide',()=>{captureAnswers();persist();});
document.addEventListener('visibilitychange',()=>{if(document.hidden)captureAnswers();else flush();});
setInterval(flush,10000);
(async()=>{
  try {
    const list=await (await fetch('lessons/catalog.json')).json();
    const wanted=new URLSearchParams(location.search).get('lesson');
    const item=list.find(x=>x.id===wanted)||list[0];
    configure(await (await fetch('lessons/'+item.file)).json());loadLocal();render();
    if(session&&apiBase) {
      try{const r=await request('/api/me');configure(r.lesson);base=r.state;persist();render();if(r.closed)$('message').textContent='課堂已結束；請保留備份。需要修改時，請教師重開課堂。';else flush();}
      catch(e){$('message').textContent='尚未連上正式課堂：'+e.message+'。請勿清除裝置資料；連線恢復後重試或重新登入。';$('loginPanel').open=true;}
    }
  }catch(e){$('message').textContent='無法載入學案。請透過 HTTP 網址開啟，並檢查學案 JSON：'+e.message;}
})();
