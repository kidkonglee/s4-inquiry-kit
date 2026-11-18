'use strict';
const $=id=>document.getElementById(id);
let token=sessionStorage.getItem('inquiry-teacher')||'', classes=[], rows=[], selected=null, detail=null, roster=null, loading=false;
const fieldNames={prediction:'預測',plan:'實驗計劃',analysis:'數據分析',claim:'主張',evidence:'證據',reasoning:'推理',reflection:'反思',transfer:'遷移'};
async function api(path,method='GET',body) {
  const r=await fetch(path,{method,headers:{Authorization:'Bearer '+token,'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});
  const p=await r.json();if(!r.ok)throw new Error(p.error||'請求失敗');return p;
}
function error(e){$('error').textContent=e.message||String(e);}
function node(tag,text,cls){const n=document.createElement(tag);if(text!==undefined)n.textContent=text;if(cls)n.className=cls;return n;}
function dl(name,text,type){const url=URL.createObjectURL(new Blob([text],{type}));const a=node('a');a.href=url;a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
async function start() {
  try{const lessons=await api('/api/teacher/lessons');$('lessons').replaceChildren();lessons.forEach(l=>{const o=node('option',l.title);o.value=l.id;$('lessons').append(o);});
    $('auth').hidden=true;$('workspace').hidden=false;sessionStorage.setItem('inquiry-teacher',token);$('adminToken').value='';$('error').textContent='';await loadClasses();
  }catch(e){error(e);$('auth').hidden=false;$('workspace').hidden=true;}
}
async function loadClasses(wanted) {
  classes=await api('/api/teacher/classes');const previous=wanted||$('classes').value;$('classes').replaceChildren();
  classes.forEach(c=>{const o=node('option',c.name+(c.closed?' · 已結束':' · 進行中')+' ['+c.id+']');o.value=c.id;$('classes').append(o);});
  if(classes.some(c=>c.id===previous))$('classes').value=previous;
  await refresh();
}
async function refresh(){
  if(loading)return;loading=true;
  try{if(!$('classes').value){rows=[];renderRows();return;}rows=await api('/api/teacher/students?class_id='+encodeURIComponent($('classes').value));renderRows();$('updated').textContent='最後更新：'+new Date().toLocaleTimeString()+' · 課堂編號 '+$('classes').value;$('error').textContent='';}
  catch(e){error(e);}finally{loading=false;}
}
function renderRows(){
  $('stats').replaceChildren();
  const metrics=[['學生',rows.length],['已開始',rows.filter(r=>r.state.last_seen).length],['已提交',rows.filter(r=>r.state.submitted).length],['待判讀',rows.filter(r=>!r.state.grade).length]];
  metrics.forEach(([label,value])=>{const n=node('div',undefined,'stat');n.append(node('small',label),node('strong',value));$('stats').append(n);});
  $('students').replaceChildren();
  for(const r of rows){const s=r.state,tr=node('tr');[r.label,s.completion+'%',s.trials.length,s.process_score,s.grade?s.grade.scores.reduce((a,b)=>a+b,0):'待評',s.total===null?'待評':s.total,(s.submitted?'已提交':'進行中')+(s.previous_grade?' · 修改後待重評':'')+(s.flags.length?' · '+s.flags.join('；'):'')].forEach(x=>tr.append(node('td',x)));const td=node('td'),b=node('button','查看','secondary');b.onclick=()=>openDetail(r);td.append(b);tr.append(td);$('students').append(tr);}
}
async function openDetail(r){
  try{const d=await api('/api/teacher/detail?student_id='+r.id);selected=r;detail=d;$('detail').hidden=false;$('detailTitle').textContent=r.label+' · 探究過程';$('answers').replaceChildren();
    for(const [k,label] of Object.entries(fieldNames)){$('answers').append(node('h4',label),node('p',d.state.answers[k]||'尚未填寫','answer-block'));}
    $('trialData').textContent=d.state.trials.map((t,i)=>(i+1)+'. F='+t.force+' N, m='+t.mass+' kg, a='+t.acceleration+' m/s²').join('\n')||'尚無實驗';
    $('components').replaceChildren();Object.entries(d.state.components).forEach(([k,v])=>$('components').append(node('p',k+'：'+v)));
    $('flags').textContent=d.state.flags.join('；');const grade=d.state.grade||d.state.previous_grade;
    for(let i=0;i<4;i++)$('g'+i).value=grade?grade.scores[i]:'';$('comment').value=grade?grade.comment:'';
    $('gradeStatus').textContent=d.state.previous_grade?'學生在評分後有新紀錄，舊分數待覆核。':d.state.grade?'已評分，可修改。':'尚未評分。';
    $('timeline').replaceChildren();for(const e of d.events){const n=node('div',undefined,'event-item');n.append(node('strong','#'+e.seq+' · '+({answer:'答案保存',trial:'執行實驗',slider:'調節變量',target:'選擇研究變量',submit:'提交學案'}[e.type]||e.type)),node('div','伺服器：'+new Date(e.received_at).toLocaleString()+' · 裝置：'+e.client_time),node('pre',JSON.stringify(e.payload,null,2)));$('timeline').append(n);}
    $('detail').scrollIntoView({behavior:'smooth',block:'start'});
  }catch(e){error(e);}
}
$('authForm').onsubmit=e=>{e.preventDefault();token=$('adminToken').value.trim();start();};
$('classes').onchange=()=>{$('detail').hidden=true;refresh();};$('refresh').onclick=refresh;
$('createForm').onsubmit=async e=>{e.preventDefault();try{roster=await api('/api/teacher/classes','POST',{name:$('className').value,lesson_id:$('lessons').value,labels:$('labels').value.split(/\r?\n/).map(s=>s.trim()).filter(Boolean)});$('rosterBox').hidden=false;$('roster').textContent='課堂編號：'+roster.id+'\n'+roster.roster.map(x=>x.label+'\t'+x.code).join('\n');await loadClasses(roster.id);}catch(err){error(err);}};
$('downloadRoster').onclick=()=>{if(roster)dl('private-login-codes-'+roster.id+'.txt',$('roster').textContent,'text/plain');};
$('closeClass').onclick=async()=>{const c=classes.find(x=>x.id===$('classes').value);if(!c)return;try{await api('/api/teacher/close','POST',{class_id:c.id,closed:!c.closed});await loadClasses(c.id);}catch(e){error(e);}};
$('signout').onclick=()=>{sessionStorage.removeItem('inquiry-teacher');location.reload();};
$('hideDetail').onclick=()=>$('detail').hidden=true;
$('gradeForm').onsubmit=async e=>{e.preventDefault();try{await api('/api/teacher/grade','POST',{student_id:selected.id,revision:detail.state.revision,scores:[0,1,2,3].map(i=>+$('g'+i).value),comment:$('comment').value});$('gradeStatus').textContent='教師評分已保存。';await refresh();}catch(err){error(err);}};
$('exportJSON').onclick=async()=>{try{if(!$('classes').value)return;const data=await api('/api/teacher/export?class_id='+$('classes').value);const full=[];for(const r of data)full.push({...r,detail:await api('/api/teacher/detail?student_id='+r.id)});dl('classroom-'+$('classes').value+'.json',JSON.stringify({exported_at:new Date().toISOString(),class_id:$('classes').value,students:full},null,2),'application/json');}catch(e){error(e);}};
function csvCell(x){let s=String(x??'');if(/^[=+@\-\t\r\n]/.test(s))s="'"+s;return '"'+s.replaceAll('"','""')+'"';}
$('exportCSV').onclick=async()=>{try{if(!$('classes').value)return;const latest=await api('/api/teacher/export?class_id='+$('classes').value);const lines=[['學生代號','完成度','實驗次數','過程分40','教師分60','總分100','已提交','追問線索','評語']];latest.forEach(r=>{const s=r.state;lines.push([r.label,s.completion,s.trials.length,s.process_score,s.grade?s.grade.scores.reduce((a,b)=>a+b,0):'',s.total,s.submitted,s.flags.join('；'),s.grade?s.grade.comment:'']);});dl('scores-'+$('classes').value+'.csv','\ufeff'+lines.map(l=>l.map(csvCell).join(',')).join('\r\n'),'text/csv');}catch(e){error(e);}};
setInterval(()=>{if(!$('workspace').hidden&&!document.hidden)refresh();},10000);
if(token)start();
