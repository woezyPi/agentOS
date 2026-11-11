(() => {
'use strict';
const KEY='agentos-brut-v1', PREFS='agentos-prefs-v1';
const $=id=>document.getElementById(id);
const skillSel=$('skill'),ctxLine=$('ctxLine'),ctxText=$('ctxText'),ctxFill=$('ctxFill'),compactBtn=$('compactBtn');
const app=$('app'),feed=$('feed'),threads=$('threads'),prompt=$('prompt'),agent=$('agent'),modeSel=$('mode'),deleg=$('delegation'),autoBox=$('auto'),sendBtn=$('send'),statusEl=$('status'),foot=$('foot');
function makeId(){return typeof crypto!=='undefined'&&crypto.randomUUID?crypto.randomUUID():(Date.now().toString(16)+'-'+Math.random().toString(16).slice(2,10)).padEnd(12,'0')}

// Shared outline icons keep the same size and stroke in static and live controls.
const iconPaths={"chat":"M21 11.5a8.4 8.4 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.4 8.4 0 0 1-3.8-.9L3 21l1.9-5.7a8.4 8.4 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.4 8.4 0 0 1 3.8-.9h.5a8.5 8.5 0 0 1 8 8v.5Z","skills":"m12 3 2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5L12 3Z","copy":"M9 9h12v12H9z M15 5V3H3v12h2","close":"m6 6 12 12M6 18 18 6","sun":"M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8ZM12 2v2m0 16v2M2 12h2m16 0h2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4","arrow":"M7 17 17 7M7 7h10v10","send":"M12 19V5m-6 6 6-6 6 6","stop":"M6 6h12v12H6z","panel":"M3 4h18v16H3zM9 4v16","window":"M9 9h12v12H9zM15 5V3H3v12h2"};
function icon(name){
 const ns='http://www.w3.org/2000/svg',svg=document.createElementNS(ns,'svg'),path=document.createElementNS(ns,'path');
 for(const [key,value] of Object.entries({class:'ui-icon',viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.65','stroke-linecap':'round','stroke-linejoin':'round','aria-hidden':'true'}))svg.setAttribute(key,value);
 path.setAttribute('d',iconPaths[name]);svg.append(path);return svg;
}

const themeKey='agentos-theme',themeToggle=$('themeToggle');
function paintTheme(light){
 if(light)document.documentElement.dataset.theme='light';else delete document.documentElement.dataset.theme;
 themeToggle.setAttribute('aria-pressed',String(light));
 themeToggle.title=light?'Activer le mode nuit':'Activer le mode jour';
}
paintTheme(document.documentElement.dataset.theme==='light');
themeToggle.addEventListener('click',()=>{
 const light=document.documentElement.dataset.theme!=='light';paintTheme(light);
 try{localStorage.setItem(themeKey,light?'light':'dark')}catch(e){console.warn('Sauvegarde du thème impossible',e)}
});
addEventListener('storage',e=>{if(e.key===themeKey||e.key===null)paintTheme(e.newValue==='light')});

// --- jeton : transmis dans le fragment de l'URL affichée par `cc-web` (jamais envoyé au serveur dans l'URL) ---
let token='';
try{const h=new URLSearchParams(location.hash.slice(1)).get('token');if(h){localStorage.setItem('cc-token',h);history.replaceState(null,'',location.pathname+location.search)}token=localStorage.getItem('cc-token')||''}catch{}
function askToken(){const t=window.prompt('Jeton d’accès (affiché au lancement de cc-web, ou contenu du fichier ~/.claude_clone/web_token) :');if(t&&t.trim()){token=t.trim();try{localStorage.setItem('cc-token',token)}catch{}return true}return false}
const api=(path,opts={})=>{const headers={'X-CC-Token':token,...(opts.headers||{})};if(opts.body)headers['Content-Type']='application/json';return fetch(path,{...opts,headers})};

// --- état local (conversations) + préférences ---
let state={active:null,conversations:[]};
try{const p=JSON.parse(localStorage.getItem(KEY));if(p&&Array.isArray(p.conversations))state=p}catch{}
const gone=new Set();let wantNew=false;
try{const q=new URLSearchParams(location.search);const want=q.get('c')||sessionStorage.getItem('cc-active');wantNew=q.get('new')==='1';
 if(want&&state.conversations.some(c=>c.id===want))state.active=want;
 if(q.has('c')||q.has('new')){q.delete('c');q.delete('new');history.replaceState(null,'',location.pathname+(q.toString()?'?'+q:''))}}catch{}
let prefs={agent:'auto',mode:'challenge',auto:false,delegation:'balanced'};
try{Object.assign(prefs,JSON.parse(localStorage.getItem(PREFS))||{})}catch{}
agent.value=prefs.agent;modeSel.value=prefs.mode;deleg.value=prefs.delegation||'balanced';autoBox.checked=!!prefs.auto;
if(agent.value!==prefs.agent)agent.value='auto';
function savePrefs(){prefs={agent:agent.value,mode:modeSel.value,auto:autoBox.checked,delegation:deleg.value};try{localStorage.setItem(PREFS,JSON.stringify(prefs))}catch{}}
agent.onchange=()=>{savePrefs();updateModelIndicator()};modeSel.onchange=deleg.onchange=savePrefs;
const autoLabel=document.querySelector('label.auto');const paintAuto=()=>autoLabel.classList.toggle('on',autoBox.checked);paintAuto();
autoBox.onchange=()=>{savePrefs();paintAuto();if(autoBox.checked)reevaluate()};
function mergeStored(){let st=null;try{st=JSON.parse(localStorage.getItem(KEY))}catch{}
 if(!st||!Array.isArray(st.conversations))return;
 const mine=new Map(state.conversations.map(c=>[c.id,c])),out=[];
 for(const c of st.conversations){if(gone.has(c.id))continue;out.push(c.id===state.active&&mine.has(c.id)?mine.get(c.id):c);mine.delete(c.id)}
 for(const c of mine.values())if(!gone.has(c.id))out.unshift(c);
 state.conversations=out}
function save(){try{mergeStored();localStorage.setItem(KEY,JSON.stringify(state));sessionStorage.setItem('cc-active',state.active||'')}catch(e){console.warn('Sauvegarde impossible',e)}}
function openTab(id){const q=id?'?c='+encodeURIComponent(id):'?new=1',w=Math.round(screen.availWidth/2),h=screen.availHeight;window.open(location.pathname+q,'_blank','popup=yes,width='+w+',height='+h+',left='+(screen.availLeft||0)+w+',top='+(screen.availTop||0))}
addEventListener('storage',e=>{if(e.key!==KEY)return;const cur=current();mergeStored();if(cur&&!state.conversations.some(c=>c.id===cur.id))state.conversations.unshift(cur);renderThreads()})
const current=()=>state.conversations.find(c=>c.id===state.active);
let roleTitles={coder:'AGENT CODEUR'};
const label=k=>(roleTitles[k]||String(k||'AGENT')).toUpperCase();

let ctxInfo=null,supModel='',busy=false,controller=null,raf=0,runStart=0,lastEvent=0;
function tickText(){if(!busy||!runStart)return '';const t=Math.floor((Date.now()-runStart)/1000),q=Math.floor((Date.now()-lastEvent)/1000);return t+' s'+(q>=6&&t-q>=2?' · '+q+' s sans signal':'')}
let liveM=null;let firstOutputMs=null;
function progress(m){if(!m||!m.blocks)return null;const t=Math.floor((Date.now()-runStart)/1000),parts=[];let pct=null;
 const tools=m.blocks.filter(b=>b.t==='tool'&&b.name!=='update_plan'),ag=tools.filter(b=>b.agent),direct=tools.filter(b=>!b.agent),plan=m.blocks.find(b=>b.t==='plan');
 if(plan&&plan.todos?.length){const d=plan.todos.filter(x=>x.status==='completed').length,n=plan.todos.length;pct=Math.round(100*d/n);parts.push('étape '+Math.min(d+1,n)+'/'+n)}
 if(ag.length){const d=ag.filter(b=>b.status!=='run').length;parts.push(d+'/'+ag.length+' agents terminés');if(pct===null&&d<ag.length)pct=Math.round(100*d/ag.length)}
 if(direct.length)parts.push(direct.length+' outil'+(direct.length>1?'s':''));
 const ch=m.chars||0;if(ch>=400)parts.push('~'+(ch>=4000?Math.round(ch/400)/10+'k':Math.round(ch/4))+' tokens estimés'+(t>=3?' ('+Math.round(ch/4/t)+'/s)':''));
 if(!parts.length||(t<6&&!direct.length&&!ag.length&&!plan))return null;return{text:parts.join(' · '),pct}}
function paintProgress(){const r=progress(liveM);document.querySelectorAll('.status-stats').forEach(e=>{const text=r?r.text:'';if(e.textContent!==text)e.textContent=text;const bar=e.parentElement?.querySelector('.status-bar'),fill=bar?.firstElementChild,visible=!!(r&&r.pct!==null),width=(visible?r.pct:0)+'%';if(fill&&fill.style.width!==width)fill.style.width=width;if(bar&&bar.hidden===visible)bar.hidden=!visible})}

function paintStatusbar(){
  const sb=document.getElementById('statusbar');if(!sb)return;
  const parts=[];const c=current();const m=liveM||(c&&c.messages[c.messages.length-1]);
  if(m&&m.agent)parts.push(String(m.agent).toUpperCase());
  if(m&&m.blocks){const ag=m.blocks.filter(b=>b.t==='tool'&&b.agent);const run=ag.filter(b=>b.status==='run').length,ok=ag.filter(b=>b.status==='ok').length;if(ag.length)parts.push(ag.length+' worker'+(ag.length>1?'s':'')+(run?' · '+run+' actif'+(run>1?'s':''):'')+(ok?' · '+ok+' terminé'+(ok>1?'s':''):''))}
  if(busy&&m){parts.push(phaseOf(m));const t=tickText();if(t)parts.push(t)}
  if(m&&m.chars)parts.push('~'+Math.round(m.chars/4)+' tokens estimés');
  if(ctxInfo)parts.push('ctx ≈ '+kfmt(ctxInfo.tokens));if(firstOutputMs!==null)parts.push('Premier flux client '+(firstOutputMs/1000).toFixed(2)+' s');
  parts.push(String(statusEl.textContent||'').replace('● ',''));
  const next=parts.join(' · ');if(sb.textContent!==next)sb.textContent=next;
}
setInterval(()=>{if(busy){document.querySelectorAll('.status-time').forEach(e=>e.textContent=tickText());paintProgress()}paintStatusbar()},500);
const sidebarMedia=matchMedia('(max-width:850px)'),sidebarKey='agentos-sidebar-collapsed';
try{app.classList.toggle('sidebar-collapsed',localStorage.getItem(sidebarKey)==='1')}catch{}
function paintSidebar(){
 const visible=sidebarMedia.matches?app.classList.contains('menu-open'):!app.classList.contains('sidebar-collapsed');
 const toggle=$('menuToggle'),label=(visible?'Masquer':'Afficher')+' les conversations';
 toggle.setAttribute('aria-expanded',String(visible));toggle.setAttribute('aria-label',label);toggle.title=label;
 $('sidebar').inert=!visible;
}
function closeMenu(){app.classList.remove('menu-open');paintSidebar()}
function toggleSidebar(){
 if(sidebarMedia.matches)app.classList.toggle('menu-open');
 else{app.classList.toggle('sidebar-collapsed');try{localStorage.setItem(sidebarKey,app.classList.contains('sidebar-collapsed')?'1':'0')}catch{}}
 paintSidebar();
}
sidebarMedia.addEventListener('change',closeMenu);
paintSidebar();
function start(){closeAgentInspector();ctxInfo=null;const c={id:makeId(),title:'Nouvelle conversation',messages:[],created:Date.now()};state.conversations.unshift(c);state.active=c.id;save();render();closeMenu();prompt.focus()}
function select(id){if(busy||!state.conversations.some(c=>c.id===id))return;closeAgentInspector();ctxInfo=null;state.active=id;stickNext=true;save();render();closeMenu();syncPending();refreshCtx()}
function remove(id){if(busy)return;const c=state.conversations.find(c=>c.id===id);if(!c||!confirm('Supprimer cette conversation (et son historique côté serveur) ?'))return;api('/api/threads/'+encodeURIComponent(id),{method:'DELETE'}).catch(()=>{});gone.add(id);state.conversations=state.conversations.filter(c=>c.id!==id);if(state.active===id)state.active=state.conversations[0]?.id??null;if(!state.active){start();return}save();render()}

// --- rendu riche : tout passe par le DOM / textContent (aucun innerHTML sur du contenu venant d'un modèle) ---
// --- coloration syntaxique (sans dépendance) : mots-clés, chaînes, commentaires, nombres, fonctions, classes ---
const W=s=>new Set(s.split(/\s+/).filter(Boolean));
const NUM=String.raw`\b0[xX][\da-fA-F_]+\b|\b\d[\d_]*(?:\.\d+)?(?:[eE][+-]?\d+)?\b`;
const DQ=String.raw`"(?:\\.|[^"\\\n])*"`, SQ=String.raw`'(?:\\.|[^'\\\n])*'`, BT=String.raw`\x60(?:\\[\s\S]|[^\x60\\])*\x60`;
const HL_LANGS={
  py:{kw:W('and as assert async await break class continue def del elif else except finally for from global if import in is lambda nonlocal not or pass raise return try while with yield match case'),cn:W('True False None self cls'),defs:{def:'fn',class:'cls'},
    com:String.raw`#[^\n]*`,str:String.raw`[rRbBfFuU]{0,2}(?:"""[\s\S]*?(?:"""|(?![\s\S]))|'''[\s\S]*?(?:'''|(?![\s\S])))|[rRbBfFuU]{0,2}(?:${DQ}|${SQ})`,dec:String.raw`@[A-Za-z_][\w.]*`},
  js:{kw:W('async await break case catch class const continue debugger default delete do else export extends finally for from function if import in instanceof let new of return static super switch throw try typeof var void while with yield as get set interface type enum implements public private protected readonly namespace declare abstract'),cn:W('true false null undefined this NaN Infinity'),defs:{function:'fn',class:'cls',interface:'cls',enum:'cls',type:'cls'},
    com:String.raw`//[^\n]*|/\*[\s\S]*?(?:\*/|(?![\s\S]))`,str:`${DQ}|${SQ}|${BT}`,dec:String.raw`@[A-Za-z_]\w*`},
  clike:{kw:W('abstract as assert async await bool boolean break byte case catch char class const continue crate data default defer delete do double else enum export extends extern fallthrough final finally float fn for func go goto if impl implements import in include instanceof int interface internal let long loop match mod move mut namespace new object override package private protected pub public readonly ref return select short signed static string struct super switch synchronized template this throw throws trait try type typedef typeof union unsafe unsigned use using val var virtual void volatile where while yield fun'),cn:W('true false null nil None this self'),defs:{class:'cls',struct:'cls',enum:'cls',interface:'cls',trait:'cls',fn:'fn',func:'fn',fun:'fn'},
    com:String.raw`//[^\n]*|/\*[\s\S]*?(?:\*/|(?![\s\S]))`,str:String.raw`${DQ}|'(?:\\.|[^'\\\n]){1,2}'|${BT}`,dec:String.raw`@[A-Za-z_]\w*`},
  sh:{kw:W('if then else elif fi for do done while until case esac function in return exit export local readonly source set unset'),cn:W('true false'),defs:{function:'fn'},
    com:String.raw`(?:^|(?<=\s))#[^\n]*`,str:`${DQ}|${SQ}`,vr:String.raw`\$(?:\{[^}\n]*\}|[A-Za-z_]\w*|[0-9@#?$!*-])`},
  yaml:{kw:W(''),cn:W('true false null yes no on off True False None'),defs:{},com:String.raw`(?:^|(?<=\s))#[^\n]*`,str:`${DQ}|${SQ}`,key:String.raw`^[ \t-]*[\w.\-/"']+(?=[ \t]*:(?:\s|(?![\s\S])))`},
  json:{kw:W(''),cn:W('true false null'),defs:{},com:'',str:DQ,jsonKeys:true},
  html:{kw:W(''),cn:W(''),defs:{},com:String.raw`<!--[\s\S]*?(?:-->|(?![\s\S]))`,str:`${DQ}|${SQ}`,tag:String.raw`</?[A-Za-z][\w:.-]*|/?>`,noId:true},
  css:{kw:W(''),cn:W(''),defs:{},com:String.raw`/\*[\s\S]*?(?:\*/|(?![\s\S]))`,str:`${DQ}|${SQ}`,dec:String.raw`@[\w-]+`,key:String.raw`[\w-]+(?=\s*:(?!:))`,vr:String.raw`#[\da-fA-F]{3,8}\b`},
  sql:{kw:W('select from where and or not insert into values update set delete create table alter drop index join left right inner outer on group by order having limit offset as distinct union all case when then else end primary key foreign references default null is in like between exists with'),cn:W('true false null'),defs:{},ci:true,com:String.raw`--[^\n]*|/\*[\s\S]*?(?:\*/|(?![\s\S]))`,str:SQ+'|'+DQ}
};
const HL_EXT={py:'py',js:'js',mjs:'js',cjs:'js',jsx:'js',ts:'js',tsx:'js',vue:'html',json:'json',jsonc:'json',java:'clike',kt:'clike',scala:'clike',c:'clike',h:'clike',cpp:'clike',cc:'clike',hpp:'clike',cs:'clike',go:'clike',rs:'clike',swift:'clike',php:'clike',sh:'sh',bash:'sh',zsh:'sh',yml:'yaml',yaml:'yaml',toml:'yaml',ini:'yaml',cfg:'yaml',env:'yaml',html:'html',htm:'html',xml:'html',svg:'html',css:'css',scss:'css',sql:'sql'};
const HL_NAME={python:'py',py:'py',javascript:'js',js:'js',jsx:'js',typescript:'js',ts:'js',tsx:'js',json:'json',bash:'sh',sh:'sh',shell:'sh',zsh:'sh',yaml:'yaml',yml:'yaml',toml:'yaml',ini:'yaml',html:'html',xml:'html',svg:'html',css:'css',scss:'css',sql:'sql',java:'clike',kotlin:'clike',c:'clike',cpp:'clike','c++':'clike',csharp:'clike',cs:'clike',go:'clike',rust:'clike',rs:'clike',swift:'clike',php:'clike'};
const langOfPath=p=>HL_EXT[(String(p||'').split('.').pop()||'').toLowerCase()]||null;
const langOfName=n=>HL_NAME[String(n||'').trim().toLowerCase()]||null;
const HL_RE={};
function hlRe(k){if(HL_RE[k])return HL_RE[k];const L=HL_LANGS[k],p=[];
  if(L.com)p.push('(?<com>'+L.com+')');if(L.str)p.push('(?<str>'+L.str+')');if(L.tag)p.push('(?<tag>'+L.tag+')');if(L.key)p.push('(?<key>'+L.key+')');if(L.vr)p.push('(?<vr>'+L.vr+')');if(L.dec)p.push('(?<dec>'+L.dec+')');
  p.push('(?<num>'+NUM+')');if(!L.noId)p.push('(?<id>[A-Za-z_$][\\w$]*)');
  return HL_RE[k]=new RegExp(p.join('|'),'gm'+(L.ci?'i':''))}
// → tableau de lignes ; chaque ligne = [{c:classe,t:texte}] ; null si langage inconnu
function hlLines(text,k){const L=k&&HL_LANGS[k];if(!L||text.length>600000)return null;
  const re=hlRe(k);re.lastIndex=0;const toks=[];let last=0,prev='';
  for(const m of text.matchAll(re)){if(m.index>last)toks.push({c:'',t:text.slice(last,m.index)});const g=m.groups;let c='';const w=m[0];
    if(g.com!==undefined){c='com';prev=''}else if(g.str!==undefined){c=L.jsonKeys&&/^\s*:/.test(text.slice(m.index+w.length,m.index+w.length+40))?'key':'str';prev=''}
    else if(g.tag!==undefined){c='tag';prev=''}else if(g.key!==undefined){c='key';prev=''}else if(g.vr!==undefined){c='vr';prev=''}else if(g.dec!==undefined){c='dec';prev=''}
    else if(g.num!==undefined){c='num';prev=''}
    else{const lw=L.ci?w.toLowerCase():w;if(L.kw.has(lw))c='kw';else if(L.cn.has(w))c='cn';else if(L.defs[prev])c=L.defs[prev];else if(text[m.index+w.length]==='(')c='fn';prev=c==='kw'?w:''}
    toks.push({c,t:w});last=m.index+w.length}
  if(last<text.length)toks.push({c:'',t:text.slice(last)});
  const lines=[[]];for(const t of toks){const ps=t.t.split('\n');ps.forEach((s,i)=>{if(i>0)lines.push([]);if(s)lines[lines.length-1].push({c:t.c,t:s})})}
  return lines}
function hlPaint(node,toks){for(const t of toks){if(t.c){const s=document.createElement('span');s.className='hl-'+t.c;s.textContent=t.t;node.append(s)}else node.append(document.createTextNode(t.t))}}

// --- Markdown : titres, listes (imbriquées, tâches), citations, tableaux, liens, gras/italique/barré, séparateurs ---
function inline(el,text){
  const re=/(`[^`\n]+`)|(\*\*[^*\n]+?\*\*)|(~~[^~\n]+?~~)|(\[[^\]\n]+\]\([^)\s]+\))|(https?:\/\/[^\s<>)\]]+)|(\*[^*\s][^*\n]*?\*)/g;let last=0,m;
  while((m=re.exec(text))){if(m.index>last)el.append(text.slice(last,m.index));const t=m[0];last=m.index+t.length;
    if(m[1]){const c=document.createElement('code');c.className='icode';c.textContent=t.slice(1,-1);el.append(c)}
    else if(m[2]){const b=document.createElement('strong');inline(b,t.slice(2,-2));el.append(b)}
    else if(m[3]){const d=document.createElement('del');inline(d,t.slice(2,-2));el.append(d)}
    else if(m[4]){const k=/^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(t);if(/^(https?:|mailto:)/i.test(k[2])){const a=document.createElement('a');a.href=k[2];a.target='_blank';a.rel='noopener noreferrer';inline(a,k[1]);el.append(a)}else el.append(t)}
    else if(m[5]){let u=t,tail='';const mm=/[.,;:!?]+$/.exec(u);if(mm){tail=mm[0];u=u.slice(0,-tail.length)}const a=document.createElement('a');a.href=u;a.target='_blank';a.rel='noopener noreferrer';a.textContent=u;el.append(a);if(tail)el.append(tail)}
    else{const e=document.createElement('em');inline(e,t.slice(1,-1));el.append(e)}}
  if(last<text.length)el.append(text.slice(last))}
const MD_LI=/^(\s*)([-*+]|\d+[.)])\s+(.*)$/, MD_HR=/^\s*([-*_])(\s*\1){2,}\s*$/, MD_SEP=/^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;
function mdCells(r){return r.trim().replace(/^\|/,'').replace(/\|$/,'').split(/(?<!\\)\|/).map(c=>c.trim().replace(/\\\|/g,'|'))}
function mdRender(container,text){
  const lines=String(text).split('\n'),blank=l=>/^\s*$/.test(l),isTable=k=>k+1<lines.length&&lines[k].includes('|')&&lines[k+1].includes('|')&&MD_SEP.test(lines[k+1]);
  const special=k=>/^#{1,6}\s/.test(lines[k])||/^\s*>/.test(lines[k])||MD_LI.test(lines[k])||MD_HR.test(lines[k])||isTable(k);
  let i=0;
  while(i<lines.length){const l=lines[i];let m;
    if(blank(l)){i++;continue}
    if((m=/^(#{1,6})\s+(.*?)\s*#*\s*$/.exec(l))){const h=document.createElement('div');h.className='md-h h'+m[1].length;inline(h,m[2]);container.append(h);i++;continue}
    if(MD_HR.test(l)){container.append(document.createElement('hr'));i++;continue}
    if(/^\s*>/.test(l)){const q=[];while(i<lines.length&&/^\s*>/.test(lines[i])){q.push(lines[i].replace(/^\s*>\s?/,''));i++}const bq=document.createElement('blockquote');mdRender(bq,q.join('\n'));container.append(bq);continue}
    if(isTable(i)){const head=mdCells(l),al=mdCells(lines[i+1]).map(c=>/^:-+:$/.test(c)?'center':/-+:$/.test(c)?'right':'left');i+=2;
      const t=document.createElement('table'),th=document.createElement('thead'),tb=document.createElement('tbody'),hr=document.createElement('tr');
      head.forEach((c,k)=>{const e=document.createElement('th');e.style.textAlign=al[k]||'left';inline(e,c);hr.append(e)});th.append(hr);
      while(i<lines.length&&!blank(lines[i])&&lines[i].includes('|')){const tr=document.createElement('tr');mdCells(lines[i]).forEach((c,k)=>{const e=document.createElement('td');e.style.textAlign=al[k]||'left';inline(e,c);tr.append(e)});tb.append(tr);i++}
      t.append(th,tb);container.append(t);continue}
    if(MD_LI.test(l)){const items=[];
      while(i<lines.length){const x=lines[i],mm=MD_LI.exec(x);
        if(mm&&!MD_HR.test(x)){items.push({indent:mm[1].replace(/\t/g,'    ').length,ord:/\d/.test(mm[2][0]),num:parseInt(mm[2],10),text:mm[3]});i++}
        else if(!blank(x)&&/^\s+\S/.test(x)&&items.length){items[items.length-1].text+=' '+x.trim();i++}
        else if(blank(x)&&i+1<lines.length&&MD_LI.test(lines[i+1])&&!MD_HR.test(lines[i+1])){i++}
        else break}
      const stack=[];
      for(const it of items){while(stack.length&&it.indent<stack[stack.length-1].indent)stack.pop();
        if(stack.length&&it.indent===stack[stack.length-1].indent&&it.ord!==(stack[stack.length-1].list.tagName==='OL'||stack[stack.length-1].list.tag==='ol'))stack.pop();
        if(!stack.length||it.indent>stack[stack.length-1].indent){const ls=document.createElement(it.ord?'ol':'ul');if(it.ord&&it.num>1)ls.start=it.num;
          if(stack.length){const p=stack[stack.length-1].list.lastElementChild;(p||stack[stack.length-1].list).append(ls)}else container.append(ls);stack.push({indent:it.indent,list:ls})}
        const li=document.createElement('li');let tx=it.text;const tk=/^\[( |x|X)\]\s+(.*)$/.exec(tx);
        if(tk){li.className='task';const b=document.createElement('span');b.className='box';b.textContent=tk[1]===' '?'☐':'☑';li.append(b,' ');tx=tk[2]}
        inline(li,tx);stack[stack.length-1].list.append(li)}
      continue}
    const p=document.createElement('p');let first=true;
    while(i<lines.length&&!blank(lines[i])&&(first||!special(i))){if(!first)p.append(document.createElement('br'));inline(p,lines[i].trim());first=false;i++}
    container.append(p)}}

function codeBlock(lang,code){const w=document.createElement('div');w.className='code';const pre=document.createElement('pre');const c=document.createElement('code');const hk=hlLines(code,langOfName(lang));if(hk){hk.forEach((ln,k)=>{if(k)c.append('\n');hlPaint(c,ln)})}else c.textContent=code;pre.append(c);w.append(pre);if(lang){const l=document.createElement('span');l.className='lang';l.textContent=lang;w.append(l)}const b=document.createElement('button');b.className='cp';b.type='button';b.textContent='COPIER';b.onclick=async()=>{try{await navigator.clipboard.writeText(code);b.textContent='COPIÉ ✓'}catch{b.textContent='ÉCHEC'}};w.append(b);return w}
function richText(container,text){for(const part of String(text).split(/(```[^\n]*\n[\s\S]*?```)/g)){if(!part)continue;const m=/^```([^\n]*)\n([\s\S]*?)```$/.exec(part);if(m){container.append(codeBlock(m[1].trim(),m[2].replace(/\n$/,'')));continue}const txt=part.replace(/^\n+/,'').replace(/\n+$/,'');if(!txt)continue;const d=document.createElement('div');d.className='md';mdRender(d,txt);if(d.childNodes.length)container.append(d)}}
// --- diff : analyse du diff unifié (compteurs de lignes des blocs @@, donc fiable même si une ligne supprimée commence par « -- ») ---
function parseDiff(text){
  const files=[];let cur=null,ol=0,nl=0,orem=0,nrem=0,dels=[],adds=[];
  function flush(){if(!cur)return;const n=Math.max(dels.length,adds.length);for(let i=0;i<n;i++){const d=dels[i],a=adds[i];cur.rows.push({kind:d&&a?'chg':d?'del':'add',ln:d?.n,lt:d?.t,rn:a?.n,rt:a?.t})}dels=[];adds=[]}
  function newFile(name){flush();cur={file:name,rows:[]};files.push(cur)}
  for(const raw of String(text).split('\n')){
    if(orem>0||nrem>0){const c=raw[0],t=raw.slice(1);
      if(c==='-'){dels.push({n:ol++,t});orem--;continue}
      if(c==='+'){adds.push({n:nl++,t});nrem--;continue}
      if(c===' '||raw===''){flush();cur.rows.push({kind:'ctx',ln:ol++,lt:t,rn:nl++,rt:t});orem--;nrem--;continue}
      if(c==='\\')continue}
    flush();let m;
    if(raw.startsWith('--- ')){newFile(null);continue}
    if(raw.startsWith('+++ ')){if(!cur)newFile(null);cur.file=raw.slice(4).replace(/^b\//,'');continue}
    if((m=/^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@/.exec(raw))){if(!cur)newFile('');ol=+m[1];nl=+m[3];orem=m[2]===undefined?1:+m[2];nrem=m[4]===undefined?1:+m[4];cur.rows.push({kind:'hunk',lt:raw});continue}
    if(raw.trim()){if(!cur)newFile('');cur.rows.push({kind:'note',lt:raw})}
  }
  flush();return files}
function splitView(files){
  const wrap=el('div','sbs-wrap');
  for(const f of files){const lang=langOfPath(f.file);if(f.file)wrap.append(el('div','sbs-file',f.file));
    const t=document.createElement('table');t.className='sbs';const cg=document.createElement('colgroup');for(const w of['46px','','46px','']){const c=document.createElement('col');if(w)c.style.width=w;cg.append(c)}t.append(cg);
    for(const r of f.rows){const tr=document.createElement('tr');
      if(r.kind==='hunk'||r.kind==='note'){const td=document.createElement('td');td.colSpan=4;td.className=r.kind;td.textContent=r.lt;tr.append(td);t.append(tr);continue}
      const delL=r.kind==='del'||r.kind==='chg',addR=r.kind==='add'||r.kind==='chg';
      const cell=(cls,v)=>{const td=document.createElement('td');td.className=cls;const sv=v===undefined||v===null?'':String(v),hk=sv&&lang?hlLines(sv,lang):null;if(hk&&hk[0])hlPaint(td,hk[0]);else td.textContent=sv;tr.append(td)};
      cell('ln',r.ln);cell('cl'+(r.lt===undefined?' empty':delL?' del':''),r.lt);cell('ln r',r.rn);cell('cr'+(r.rt===undefined?' empty':addR?' add':''),r.rt);t.append(tr)}
    wrap.append(t)}
  return wrap}
function unifiedPre(body){const pre=document.createElement('pre');pre.className='pv';String(body).split('\n').forEach((ln,i,a)=>{const s=document.createElement('span');s.className=ln.startsWith('+')&&!ln.startsWith('+++')?'add':ln.startsWith('-')&&!ln.startsWith('---')?'del':ln.startsWith('@@')?'hunk':'';s.textContent=ln;pre.append(s);if(i<a.length-1)pre.append('\n')});return pre}
function pvBody(kind,body){
  if(kind!=='diff'){const pre=document.createElement('pre');pre.className='pv';pre.textContent=body;return pre}
  let files=[];try{files=parseDiff(body)}catch{}
  if(!files.some(f=>f.rows.some(r=>r.kind==='chg'||r.kind==='add'||r.kind==='del')))return unifiedPre(body);   // « aucun changement », diff illisible…
  const box=el('div','pvbox'),bar=el('div','pvbar'),content=el('div');
  const mode=()=>{let m=null;try{m=localStorage.getItem('agentos-diffview')}catch{}return m||(innerWidth<700?'unified':'split')};
  const b1=el('button','','CÔTE À CÔTE'),b2=el('button','','UNIFIÉ');b1.type=b2.type='button';
  const paint=()=>{const m=mode();content.replaceChildren(m==='split'?splitView(files):unifiedPre(body));b1.classList.toggle('sel',m==='split');b2.classList.toggle('sel',m!=='split')};
  const set=m=>()=>{try{localStorage.setItem('agentos-diffview',m)}catch{}paint()};
  b1.onclick=set('split');b2.onclick=set('unified');bar.append(b1,b2);box.append(bar,content);paint();return box}
const el=(tag,cls,text)=>{const e=document.createElement(tag);if(cls)e.className=cls;if(text!==undefined)e.textContent=text;return e};

const cleanTask=t=>{t=String(t||'');const m=/^role=[^,]*,\s*task=([\s\S]*?)(?:,\s*context=[\s\S]*)?$/.exec(t);return (m?m[1]:t).replace(/⏎/g,' ').replace(/\s+/g,' ').trim()};
function stepSum(sum){const f=document.createDocumentFragment(),re=/(?:^|, )([A-Za-z_]\w*)=/g;const hits=[];let m;while((m=re.exec(sum))){hits.push({k:m[1],at:m.index,vs:m.index+m[0].length})}
  if(!hits.length){f.append(sum);return f}
  hits.forEach((h,i)=>{const end=i+1<hits.length?hits[i+1].at:sum.length;if(i)f.append(' ');f.append(el('span','k',h.k+' '),el('span','v',sum.slice(h.vs,end)))});return f}
const fmtDur=ms=>ms<1000?'<1 s':ms<60000?Math.round(ms/1000)+' s':Math.floor(ms/60000)+' min '+Math.round(ms%60000/1000)+' s';

// Geometric role identities are stable across cards and inspector, separate from execution status.
const agentIdentities={
 architect:{path:'M12 3 21 8l-9 5-9-5 9-5Zm-9 9 9 5 9-5M3 16l9 5 9-5'},
 developer:{path:'M4 4h10v10H4zM10 10h10v10H10zM7 17l10-10'},
 reviewer:{path:'m12 3 8 4.5v9L12 21l-8-4.5v-9L12 3Zm0 5-4 4 4 4 4-4-4-4Z'},
 researcher:{path:'M12 4a8 8 0 1 0 0 16 8 8 0 0 0 0-16Zm-9 8h18M12 3v18m-5-7 10-4'},
 writer:{path:'M5 5h14M5 12h10M5 19h14M19 9v6'},
 devops:{path:'M4 7h10l4 5-4 5H4l4-5-4-5Zm12-3 5 8-5 8'},
 data:{path:'M4 15h4v6H4zM10 9h4v12h-4zM16 3h4v18h-4z'},
 challenger:{path:'m8 3 6 9-6 9-6-9 6-9Zm8 0 6 9-6 9'},
 worker:{path:'M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z'}
};
for(const [key,value] of Object.entries(agentIdentities))iconPaths['agent-'+key]=value.path;
function agentIdentity(block){const role=block.agent==='coder'?'developer':block.agent;const names=Object.keys(agentIdentities);if(role==='worker'&&block.label||!agentIdentities[role]){const key=(role||'agent')+':'+(block.label||'');let hash=0;for(const char of key)hash=(Math.imul(hash,31)+char.charCodeAt(0))|0;return names[(hash>>>0)%names.length]}return role}
function agentMark(block){const mark=el('span','agent-mark');mark.dataset.agentIdentity=agentIdentity(block);mark.setAttribute('aria-hidden','true');mark.append(icon('agent-'+mark.dataset.agentIdentity));return mark}
function shortModel(model){const full=modelDisplay(model);const parts=String(full||'').split(' · '),raw=parts.pop()||'',name=raw.split('/').pop(),free=name.endsWith(':free');const known={'north-mini-code:free':'North Mini Code','lfm-2.5-2.6b:free':'LFM 2.5 · 2.6B','gemma-4-31b-it:free':'Gemma 4 · 31B','nemotron-3-ultra-550b-a55b:free':'Nemotron Ultra · 550B'};return [...parts,known[name]||name.replace(/:free$/,''),free?'gratuit':''].filter(Boolean).join(' · ')}
function agentActivity(block){const last=[...(block.steps||[])].reverse().find(step=>step.k==='tool');if(!last)return block.status==='run'?'Prépare sa prochaine action…':block.result?'Rapport disponible':'En attente de démarrage';const path=/(?:^|,\s*)path=([^,]+)/.exec(last.summary||'')?.[1]?.trim(),file=path?.split('/').pop();const actions={read_file:'Lit '+(file||'un fichier'),grep:'Recherche dans '+(path||'le projet'),glob:'Repère les fichiers du projet',scan_stack:'Analyse la structure du projet',git_diff:'Vérifie les modifications',execute_bash:'Exécute une commande',run_check:'Lance une vérification',fetch_url:'Consulte une page web',latest_version:'Vérifie une dépendance',write_file:'Écrit '+(file||'un fichier'),edit_file:'Modifie '+(file||'un fichier')};return actions[last.name]||last.name.replace(/_/g,' ')}
function agentOverview(message,agents){const view=message.agentFilter||'all',run=agents.filter(a=>a.status==='run').length,ok=agents.filter(a=>a.status==='ok').length,err=agents.filter(a=>a.status==='error').length,wait=agents.length-run-ok-err;const box=el('div','orch');box.dataset.viewKey='overview';const summary=el('div','orch-summary');summary.append(el('span','orch-title',agents.length+' agent'+(agents.length>1?'s':'')),el('span','orch-counts',[run?run+' en cours':'',ok?ok+' terminé'+(ok>1?'s':''):'',err?err+' en échec':'',wait?wait+' en attente':''].filter(Boolean).join(' · ')));box.append(summary);
 if(agents.length>1){const filters=el('nav','agent-filters');filters.setAttribute('aria-label','Filtrer les agents');for(const [value,title] of [['all','Tous'],['run','En cours'],['ok','Terminés'],['error','Erreurs']]){const button=el('button',view===value?'selected':'',title);button.type='button';button.setAttribute('aria-pressed',String(view===value));button.onclick=()=>{message.agentFilter=value;schedule()};filters.append(button)}box.append(filters)}return box}
function agentVisible(block,message){const filter=message.agentFilter||'all';return filter==='all'||block.status===filter}

// Inspecteur latéral : aucune requête supplémentaire, seules les données reçues sont affichées.
let inspectedAgent=null,inspectorTab='activity';
function openAgentInspector(block){inspectedAgent=block;inspectorTab='activity';$('agentInspector').hidden=false;refreshInspector()}
function closeAgentInspector(){inspectedAgent=null;$('agentInspector').hidden=true}
function refreshInspector(){
 const a=inspectedAgent,p=$('agentInspector');if(!a||!p||p.hidden)return;
 $('aiTitle').replaceChildren(agentMark(a),document.createTextNode(label(a.agent)+(a.label?' · '+a.label:'')));
 $('aiSubtitle').textContent=[modelDisplay(a.model),a.status==='run'?'EN COURS':a.status==='error'?'ÉCHEC':'TERMINÉ',a.start?fmtDur((a.end||Date.now())-a.start):''].filter(Boolean).join(' · ');
 const area=$('aiBody');if(!area)return;
 const tabContent={activity:'ACTIVITÉ',logs:'LOGS',files:'FICHIERS',metrics:'PERFORMANCES'};
 p.querySelectorAll('[data-inspector-tab]').forEach(btn=>{btn.classList.toggle('selected',btn.dataset.inspectorTab===inspectorTab);btn.setAttribute('aria-selected',btn.dataset.inspectorTab===inspectorTab?'true':'false')});
 const savedTop=area.scrollTop,content=document.createDocumentFragment();
 const add=(kind,title,value)=>{const sect=el('section','ai-section');sect.append(el('div','ai-label',title));const body=el('div',kind,value);sect.append(body);content.append(sect)};
 if(inspectorTab==='activity'){
   add('ai-task','MISSION',cleanTask(a.summary||'Mission non communiquée'));
   const steps=(a.steps||[]).filter(s=>s.k==='tool');add('ai-count','OUTILS',String(steps.length));
   const recent=el('div','ai-events');
   for(const st of steps.slice(-30)){const line=el('div','ai-event');line.append(el('span','ai-event-state '+(st.status||'run'),st.status==='ok'?'✓':st.status==='error'?'✕':'•'),el('b','',st.name||'Outil'),el('span','',st.summary||''));recent.append(line)}
   if(!steps.length)recent.append(el('div','ai-muted','En attente des premiers événements d’outils.'));
   content.append(recent);
   if(a.result)add('ai-report','RÉSULTAT',a.result);
 }else if(inspectorTab==='logs'){
   const steps=a.steps||[];
   if(!steps.length)add('ai-muted','LOGS','Aucun log détaillé transmis.');
   for(const s of steps){const sect=el('section','ai-section');sect.append(el('div','ai-label',(s.k==='thought'?'ACTIVITÉ':s.name||'OUTIL')+' · '+(s.status||'en cours')));const pre=el('pre','ai-pre',s.text||s.summary||'');sect.append(pre);content.append(sect)}
   if(a.live)add('ai-report','FLUX EN COURS',a.live);
 }else if(inspectorTab==='files'){
   const matches=new Set();for(const s of a.steps||[]){const str=s.summary||'';for(const m of str.matchAll(/(?:path|file|filename)=([^,\s]+)/g))matches.add(m[1])}
   if(!matches.size)add('ai-muted','FICHIERS','Aucun chemin de fichier identifié dans les événements reçus.');
   for(const f of matches){const btn=el('button','ai-file',f);btn.type='button';btn.onclick=()=>openFile(f,0);content.append(btn)}
 }else{
   add('ai-count','DURÉE',a.start?fmtDur((a.end||Date.now())-a.start):'Indisponible');
   add('ai-count','MODÈLE',modelDisplay(a.model)||'Non communiqué');
   add('ai-count','OUTILS',(a.steps||[]).filter(s=>s.k==='tool').length+'');
   add('ai-muted','TTFT / TOKENS PAR SECONDE','Non mesurés par appel LLM dans le protocole actuel. Instrumentation serveur requise.');
   add('ai-muted','CONTEXTE', 'Non disponible individuellement pour cet agent.');
 }
 area.replaceChildren(content);area.scrollTop=savedTop;
}
$('aiClose').onclick=closeAgentInspector;
$('agentInspector').querySelectorAll('[data-inspector-tab]').forEach(btn=>btn.onclick=()=>{inspectorTab=btn.dataset.inspectorTab;refreshInspector()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('agentInspector').hidden)closeAgentInspector()});
function agentCard(b){
  const run=b.status==='run',st=run?'run':b.status==='error'?'err':'ok',steps=b.steps||[];
  const card=el('section','agent-card '+st+(b.expanded?' is-expanded':''));if(b.id)card.dataset.viewKey='agent-'+b.id;card.dataset.agentIdentity=agentIdentity(b);
  const head=el('header');const tg=el('button','ac-tg',b.expanded?'▾':'▸');tg.type='button';tg.title=b.expanded?'Masquer les détails':'Afficher les détails';tg.setAttribute('aria-expanded',b.expanded?'true':'false');head.append(agentMark(b),el('b','',label(b.agent)+(b.label?' · '+b.label:'')+(b.panel?' · PANEL':'')));if(b.panel)card.classList.add('wide');
  if(b.model&&!b.panel){const model=el('span','pill model',shortModel(b.model));model.title=modelDisplay(b.model);head.append(model)}
  const nTools=steps.filter(x=>x.k==='tool').length;
  head.append(el('span','pill',(run?'● EN COURS':st==='err'?'! ÉCHEC':'✓ TERMINÉ')+(nTools?' · '+nTools+' outil'+(nTools>1?'s':''):'')));
  if(b.start)head.append(el('span','dur',fmtDur((b.end||Date.now())-b.start)));
  card.append(head);
  if(b.summary){const tk=cleanTask(b.summary);if(tk){const d=el('div','task',tk);d.title=tk;card.append(d)}}
  const det=el('div','ac-det');
  if(steps.length){
    const shown=run?steps.slice(-8):steps,list=el('ol','steps');
    if(run&&steps.length>shown.length)list.append(el('li','more','… '+(steps.length-shown.length)+' étape(s) précédente(s)'));
    for(const x of shown){
      if(x.k==='thought'){list.append(el('li','thought',x.text));continue}
      const li=el('li','step '+(x.status||'run'));const ss=el('span','s-sum');ss.title=x.summary||'';ss.append(stepSum(x.summary||''));li.append(el('span','s-name',x.name),ss,el('span','s-st',x.status==='ok'?'✓':x.status==='error'?'✗':'…'));
      if(x.status==='error'&&x.text)li.append(el('div','s-err',x.text));
      list.append(li)}
    det.append(list);
  }else if(run)det.append(el('div','thinking','Réflexion…'));
  if(run&&b.live){const lt=el('div','live-text'),inner=el('div');richText(inner,b.live);lt.append(inner);det.append(lt)}
  if(b.result){const d=el('details','report');d.open=b.open??true;d.ontoggle=()=>{b.open=d.open};d.append(el('summary','','RAPPORT'));const body=el('div','agent-body');const nm=/^\[Rapport du sous-agent[^\]\n]*\]\s*\n/.exec(b.result);if(nm)body.append(el('div','agent-note',nm[0].trim().replace(/^\[|\]$/g,'')));richText(body,nm?b.result.slice(nm[0].length):b.result);d.append(body);det.append(d)}
  if(b.expanded)card.append(det);
  else{const peek=el('div','ac-peek',agentActivity(b)),last=[...steps].reverse().find(x=>x.k==='tool');peek.title=last?last.name+' '+(last.summary||''):peek.textContent;card.append(peek)}
  const flip=()=>{b.expanded=!b.expanded;schedule()};
  tg.onclick=e=>{e.stopPropagation();flip()};
  const inspect=el('button','agent-inspect','Inspecter');inspect.append(icon('arrow'));inspect.type='button';
  inspect.setAttribute('aria-label','Inspecter '+label(b.agent));
  inspect.onclick=e=>{e.stopPropagation();openAgentInspector(b)};
  tg.classList.add('agent-details');tg.replaceChildren(document.createTextNode(b.expanded?'Réduire':'Détails'));const footer=el('div','agent-footer');footer.append(tg,inspect);card.append(footer);
  head.classList.add('ac-head');head.onclick=e=>{if(e.target.closest('a,.fp,button'))return;openAgentInspector(b)};
  return card;
}
function renderBlock(b,m){
  if(b.t==='text'){
    const flat=String(b.text||'').replace(/```[\s\S]*?```/g,' [code] ').replace(/[*`]|^#+\s*|\]\([^)]*\)/gm,'').replace(/\[/g,'').replace(/\s+/g,' ').trim();
    if(b.narr&&(flat.length>140||String(b.text).includes('\n\n'))){   // commentaire intermédiaire (avant un outil) : replié sur une ligne, clic pour tout lire
      const d=el('details','blk narr fold');d.open=!!b.unfold;d.ontoggle=()=>{b.unfold=d.open};
      const sm=el('summary');sm.append(el('span','pv',flat.length>130?flat.slice(0,130).replace(/\s\S*$/,'')+'…':flat));d.append(sm);const body=el('div','fold-body');richText(body,b.text);d.append(body);return d}
    const d=el('div','blk'+(b.narr?' narr':''));richText(d,b.text);return d}
  if(b.t==='notice'){
    const quota=/free-models-per-day|Quota quotidien OpenRouter épuisé/i.test(b.text||'');
    const text=quota?'Quota quotidien OpenRouter épuisé pour les variantes gratuites (:free). Changer de variante :free ne rétablit pas ce quota. Attends sa réinitialisation, ou choisis un modèle auquel ton compte a accès.':b.text;
    const n=el('div','notice'+(b.level==='warn'?' warn':''),text);
    if(quota){const actions=el('div','choices'),models=el('button','choice','Choisir un modèle');models.type='button';models.onclick=()=>openStudio('models');actions.append(models);n.append(actions)}
    if(/^Budget d.étapes atteint/.test(b.text)){const go=el('button','choice','CONTINUER ↗');go.type='button';go.style.marginLeft='12px';go.disabled=busy;go.title='Reprend avec un nouveau budget d’étapes';go.onclick=()=>{if(!busy){prompt.value='continue';submit()}};n.append(go)}
    return n}
  if(b.t==='plan'){const d=el('div','plan');d.append(el('div','small-label','PLAN'));for(const t of b.todos){const r=el('div',t.status==='completed'?'done':t.status==='in_progress'?'doing':'todo',(t.status==='completed'?'✔ ':t.status==='in_progress'?'▶ ':'○ ')+t.content);d.append(r)}return d}
  if(b.t==='tool'){
    const run=b.status==='run';
    if(b.agent)return agentCard(b);
    const d=el('details','tool '+(b.status==='error'?'err':b.status==='ok'?'ok':'')+(b.denied?' denied':'')+(b.name==='execute_bash'?' bash':'')+(b.name==='read_file'?' read':''));d.open=!!b.open;d.ontoggle=()=>{b.open=d.open};
    const s=el('summary');
    const badge=b.name==='execute_bash'?'BASH':b.name==='read_file'?'LECTURE':b.name.toUpperCase();
    const status=run?'EN COURS':b.denied?'REFUSÉ':b.status==='error'?'ÉCHEC':'TERMINÉ';
    const dur=b.end?el('span','tool-dur',fmtDur(b.end-b.start)):null;
    s.append(el('span','tool-badge',badge),el('span','tool-status',status),dur);
    d.append(s);
    if(b.name==='execute_bash'){
      const cmd=b.summary.replace(/^command=/,'').replace(/⏎/g,'\n');
      const cmdBlock=el('div','tool-cmd',cmd);
      const copyBtn=el('button','tool-copy','COPIER');
      copyBtn.type='button';
      copyBtn.onclick=async()=>{
        try{await navigator.clipboard.writeText(cmd);copyBtn.textContent='COPIÉ ✓';setTimeout(()=>copyBtn.textContent='COPIER',1500)}catch{copyBtn.textContent='COPIE ✗'}
      };
      cmdBlock.append(copyBtn);
      d.append(cmdBlock);
    }else if(b.name==='read_file'){
      d.append(el('div','tool-path',b.summary.replace(/^path=/,'')));
    }
    if(b.result){
      const details=el('details','result-block');
      details.open=run||b.status==='error';
      const sm=el('summary','result-sum');
      sm.append(document.createTextNode(run?'Sortie en cours…':b.status==='error'?'Sortie (échec)':'Sortie'));
      const rcopy=el('button','tool-copy','COPIER');
      rcopy.type='button';
      rcopy.onclick=async()=>{try{await navigator.clipboard.writeText(b.result);rcopy.textContent='COPIÉ ✓';setTimeout(()=>rcopy.textContent='COPIER',1500)}catch{rcopy.textContent='COPIE ✗'}};
      sm.append(rcopy);
      details.append(sm);
      const pre=el('pre','result');
      pre.textContent=b.result+(b.truncated?'\n… [tronqué]':'');
      details.append(pre);
      d.append(details);
    }
    return d
  }
  if(b.t==='approval'){
    const d=el('div','approval'+(b.resolved?' resolved':'')+(b.auto?' auto':''));
    if(!b.auto)d.append(el('h4','',b.resolved?'CONFIRMATION — TRAITÉE':'CONFIRMATION REQUISE'));
    for(const c of b.calls){
      const w=el('div','call');
      const title=c.preview?.title||c.name;
      const cmd=c.summary?.replace(/^command=/,'')||'';
      const cmdShort=cmd.length>60?cmd.slice(0,57)+'…':cmd;
      const callTitle=el('div','call-title');
      if(c.name==='execute_bash'){
        callTitle.append(el('span','tool-badge','BASH'),el('span','',': '+cmdShort));
      }else if(c.name==='read_file'){
        callTitle.append(el('span','tool-badge','LECTURE'),el('span','',': '+cmdShort));
      }else{
        callTitle.textContent=title;
      }
      w.append(callTitle);
      if(c.reason)w.append(el('div','reason',(b.resolved?'⚡ AUTO : confirmation était requise — ':'⚡ AUTO : confirmation requise — ')+c.reason));
      if(c.preview)w.append(pvBody(c.preview.kind,c.preview.body));
      if(b.auto){d.append(w);continue}
      const ch=el('div','choices');
      const cur=(b.resolved?b.decided:b.choices)?.[c.id];
      for(const [v,t,cls] of [['approve','AUTORISER','ok'],['deny','REFUSER',''],['always','TOUJOURS','ok']]){
        const bt=el('button','choice'+(cur===v?' sel '+cls:''),t);
        bt.type='button';
        bt.disabled=!!b.resolved||busy;
        bt.title=v==='always'?(c.name==='execute_bash'?'Commande sûre : toutes les commandes de la liste blanche passent ensuite sans question. Sinon : cette commande exacte seulement.':'Ne plus demander pour ce fichier (jusqu’à la fin de la session)'):'';
        bt.onclick=()=>{b.choices[c.id]=v;schedule()};
        ch.append(bt)
      }
      w.append(ch);
      d.append(w)
    }
    if(!b.resolved){
      const acts=el('div','approval-actions');
      const all=(v)=>()=>{
        for(const c of b.calls)b.choices[c.id]=v;
        schedule()
      };
      if(b.calls.length>1){
        for(const [v,t] of [['approve','TOUT AUTORISER'],['deny','TOUT REFUSER']]){
          const bt=el('button','choice',t);
          bt.type='button';
          bt.disabled=busy;
          bt.onclick=all(v);
          acts.append(bt)
        }
      }
      const ok=b.calls.every(c=>b.choices[c.id]);
      const go=el('button','send','VALIDER ↗');
      go.type='button';
      go.disabled=!ok||busy;
      go.onclick=()=>resume(m,b);
      acts.append(go);
      d.append(acts)
    }
    return d
  }
  return document.createDocumentFragment();
}

// --- Ctrl+clic sur un nom de fichier (src/api.py:42) : ouvre un visionneur en lecture seule, ligne en évidence ---
const FP=/(?<![\w./@~-])((?:[\w.-]+\/)*[\w.-]+\.(?:py|jsx?|tsx?|json|md|html?|css|scss|sql|sh|ya?ml|toml|cfg|ini|txt|java|kt|go|rs|rb|php|vue|svelte|tf|xml|lock|example|ipynb|csv))(?::(\d+)(?::\d+)?)?(?![\w/-])/g;
function linkifyPaths(root){
  const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT,{acceptNode:n=>{const p=n.parentElement;if(!p||p.closest('pre,.code,textarea,input,.fp,a,script,style,.sbs'))return NodeFilter.FILTER_REJECT;FP.lastIndex=0;const ok=FP.test(n.nodeValue);FP.lastIndex=0;return ok?NodeFilter.FILTER_ACCEPT:NodeFilter.FILTER_REJECT}});
  const nodes=[];while(walker.nextNode())nodes.push(walker.currentNode);
  for(const n of nodes){const text=n.nodeValue,frag=document.createDocumentFragment();let last=0,m;FP.lastIndex=0;
    while((m=FP.exec(text))){if(m.index>last)frag.append(text.slice(last,m.index));const a=document.createElement('span');a.className='fp';a.dataset.path=m[1];if(m[2])a.dataset.line=m[2];a.title='Ctrl+clic : ouvrir '+m[1]+(m[2]?' (ligne '+m[2]+')':'');a.textContent=m[0];frag.append(a);last=m.index+m[0].length}
    if(last<text.length)frag.append(text.slice(last));n.replaceWith(frag)}}
const fileview=$('fileview'),fvBody=$('fvBody');let fvPath='',fvLine=0;
function toast(t){document.querySelectorAll('.toast').forEach(x=>x.remove());const e=el('div','toast',t);document.body.append(e);setTimeout(()=>e.remove(),3500)}
function closeFile(){fileview.hidden=true}
function showFile(j,line){
  fvPath=j.path;fvLine=line||0;$('fvPath').textContent=j.path+(line?':'+line:'');$('fvEditor').href=j.editor;fvBody.replaceChildren();
  const lines=j.text.split('\n'),cap=6000,isMd=/\.(md|markdown|mdx)$/i.test(j.path),tk=hlLines(j.text,langOfPath(j.path));
  let mode=isMd&&!line?'render':'source',hit=null;const body=el('div');
  const bar=el('div','fv-bar'),bR=el('button','','RENDU'),bS=el('button','','SOURCE');bR.type=bS.type='button';
  const paint=()=>{body.replaceChildren();hit=null;bR.classList.toggle('on',mode==='render');bS.classList.toggle('on',mode==='source');
    if(mode==='render'){const d=el('div','fv-md');richText(d,j.text);body.append(d);return}
    lines.slice(0,cap).forEach((t,i)=>{const row=el('div','fv-row'+(String(i+1)===String(line)?' hit':'')),sp=el('span');if(tk&&tk[i])hlPaint(sp,tk[i]);else sp.textContent=t;row.append(el('i','',String(i+1)),sp);if(row.classList.contains('hit'))hit=row;body.append(row)});
    if(lines.length>cap||j.truncated)body.append(el('div','fv-note','… affichage tronqué ('+(j.truncated?'fichier de plus de 400 Ko':lines.length+' lignes')+'). Ouvre-le dans VS Code pour le voir en entier.'))};
  if(isMd){bR.onclick=()=>{mode='render';paint();fvBody.scrollTop=0};bS.onclick=()=>{mode='source';paint();fvBody.scrollTop=0};bar.append(bR,bS);fvBody.append(bar)}
  fvBody.append(body);paint();
  fileview.hidden=false;if(hit)hit.scrollIntoView({block:'center'});else fvBody.scrollTop=0}
function showCandidates(path,cands,line){
  $('fvPath').textContent=path+' — plusieurs fichiers';$('fvEditor').href='#';fvBody.replaceChildren();const note=el('div','fv-note','« '+path+' » existe à plusieurs endroits : choisis.');
  for(const c of cands){const b=el('button','',c);b.type='button';b.onclick=()=>openFile(c,line);note.append(b)}fvBody.append(note);fileview.hidden=false}
async function openFile(path,line){
  try{const r=await api('/api/file?path='+encodeURIComponent(path)+(line?'&line='+encodeURIComponent(line):''));const j=await r.json().catch(()=>({}));
    if(r.status===409&&j.candidates){showCandidates(path,j.candidates,line);return}
    if(!r.ok){toast(typeof j.detail==='string'?j.detail:'Ouverture impossible ('+r.status+')');return}
    showFile(j,line)}catch(e){toast('Ouverture impossible : '+e.message)}}
document.addEventListener('click',e=>{const a=e.target.closest&&e.target.closest('.fp');if(a&&(e.ctrlKey||e.metaKey)){e.preventDefault();openFile(a.dataset.path,a.dataset.line)}});
document.addEventListener('keydown',e=>{if(e.key==='Control'||e.key==='Meta')document.body.classList.add('ctrl');if(e.key==='Escape'&&!fileview.hidden)closeFile()});
document.addEventListener('keyup',e=>{if(e.key==='Control'||e.key==='Meta')document.body.classList.remove('ctrl')});window.addEventListener('blur',()=>document.body.classList.remove('ctrl'));
$('fvEditor').addEventListener('click',async e=>{e.preventDefault();const href=$('fvEditor').getAttribute('href');if(!fvPath||!href||href==='#')return;
  try{const r=await api('/api/open-editor',{method:'POST',body:JSON.stringify({path:fvPath,line:parseInt(fvLine||0,10)||0})});const j=await r.json().catch(()=>({}));
    if(r.ok){toast('Ouvert dans VS Code');return}
    toast((typeof j.detail==='string'?j.detail:'Ouverture impossible')+' — essai via le lien du navigateur…');setTimeout(()=>{location.href=j.fallback||href},700)}
  catch(err){toast('Ouverture impossible : '+err.message);location.href=href}});
$('fvClose').onclick=closeFile;$('fvCopy').onclick=async()=>{try{await navigator.clipboard.writeText(fvPath);toast('Chemin copié')}catch{toast('Copie impossible')}};
const WALKER_SVG='<svg viewBox="-4 0 108 122" aria-hidden="true"><line class="ground" x1="-4" y1="117" x2="104" y2="117" fill="none"/><g class="bodyg"><g class="headg"><polygon class="c1" points="70,38 78,38.5 78.5,25.5 71.5,25"/><polygon class="c2" points="75,2.5 99,5 96.5,26.5 73.5,24"/></g><polygon class="c1" points="0,55 80,36 84,83 0,88"/><rect class="eye" x="57" y="49" width="15" height="16" transform="rotate(-6 64 57)"/><rect class="leg a c1" x="1" y="84" width="19" height="29"/><rect class="leg b c1" x="40" y="82" width="16" height="31"/><rect class="leg a c1" x="72" y="80" width="16" height="25"/></g></svg>';
function walker(){const w=el('span','moulinette walker');w.innerHTML=WALKER_SVG;return w}   // SVG statique et de confiance : aucun contenu venant d'un modèle
function phaseOf(m){
  const tools=m.blocks.filter(b=>b.t==='tool'&&b.name!=='update_plan'),last=m.blocks[m.blocks.length-1];
  if(tools.some(b=>b.agent&&b.status==='run'))return 'agents au travail…';
  if(tools.some(b=>!b.agent&&b.status==='run'))return 'outil en cours…';
  if(last&&last.t==='text'&&last.open)return 'rédige…';
  return /^Reprise/.test(m.status||'')?'reprise…':'réfléchit…'}
function whoLine(m,live){
  if(live)return null;
  const tools=m.blocks.filter(b=>b.t==='tool'&&b.name!=='update_plan'),direct=tools.filter(b=>!b.agent),agents=tools.filter(b=>b.agent);
  if(!live&&!tools.length)return null;
  const cnt={};for(const b of direct)cnt[b.name]=(cnt[b.name]||0)+1;
  const d=Object.entries(cnt).map(([k,v])=>k+(v>1?' ×'+v:'')).join(', ');
  const mark=b=>live?(b.status==='run'?' ▶':' ✔'):'';
  const ag=agents.map(b=>label(b.agent)+(b.label?' '+b.label:'')+(b.panel?' (panel)':'')+(b.model&&!b.panel?' · '+b.model:'')+mark(b)).join(' ; ');
  let phase='';
  if(live){const last=m.blocks[m.blocks.length-1];
    if(agents.some(b=>b.status==='run'))phase='attend ses sous-agents…';
    else if(direct.some(b=>b.status==='run'))phase='exécute un outil…';
    else if(last&&last.t==='text'&&last.open)phase='rédige la réponse…';
    else phase='réfléchit…'}
  if(agents.length)return null;
  const w=el('div','who');
  w.append(document.createTextNode([direct.length?'outils : '+d:'','aucun sous-agent : le superviseur a tout fait lui-même'].filter(Boolean).join(' — ')));
  return w}
function renderMessage(m){
  const art=el('article','message '+(m.role==='user'?'user':'agent'));if(m.id)art.dataset.mid=m.id;
  const rl=el('div','message-role',m.role==='user'?'TOI':(m.agent||'SUPERVISEUR').toUpperCase());if(m._old){art.classList.add('compacted');rl.append(el('small','','dans le résumé'))}if(m.role!=='user'&&(m.model||supModel))rl.append(el('small','',modelDisplay(m.model||supModel)));art.append(rl);
  const content=el('div');const body=el('div','message-content');
  if(m.role==='user'||!m.blocks){body.textContent=m.text||''}else{const agents=m.blocks.filter(b=>b.t==='tool'&&b.agent);if(agents.length){body.append(agentOverview(m,agents));if(!agents.some(a=>agentVisible(a,m)))body.append(el('p','agents-empty','Aucun agent dans cette catégorie.'))}let grid=null;for(const b of m.blocks){const isAg=b.t==='tool'&&b.agent;if(isAg&&!agentVisible(b,m))continue;const n=renderBlock(b,m);if(isAg){if(!grid){grid=el('div','agent-grid');grid.dataset.viewKey='agents-'+b.id;body.append(grid)}grid.append(n)}else{grid=null;body.append(n)}}if(m.status){const sl=el('div','status-line'),mo=walker();sl.append(mo,el('span','status-phase',phaseOf(m)),el('span','status-time',tickText()));const st=el('div','status-stats'),bar=el('div','status-bar');sl.dataset.viewKey='progress';const currentProgress=progress(m),visible=!!(currentProgress&&currentProgress.pct!==null),fill=document.createElement('i');bar.hidden=!visible;fill.style.width=(visible?currentProgress.pct:0)+'%';bar.append(fill);st.textContent=currentProgress?currentProgress.text:'';sl.append(bar,st);body.append(sl)}}
  content.append(body);
  if(m.role!=='user'&&m.blocks){const cv=current(),live=busy&&cv&&cv.messages[cv.messages.length-1]===m,w=whoLine(m,live);if(w){if(live)content.prepend(w);else content.append(w)}}
  if(m.role!=='user'&&m.usage)content.append(el('div','usage',m.usage));
  const text=m.role==='user'||!m.blocks?(m.text||''):m.blocks.filter(b=>b.t==='text'||(b.t==='tool'&&b.agent&&b.result)).map(b=>b.text||b.result).join('\n\n');
  if(text){const actions=el('div','message-actions');const copy=el('button','tiny-action','Copier');copy.prepend(icon('copy'));copy.type='button';copy.onclick=async()=>{try{await navigator.clipboard.writeText(text);copy.textContent='COPIÉ ✓'}catch{copy.textContent='COPIE IMPOSSIBLE'}};actions.append(copy);content.append(actions)}
  art.append(content);return art;
}
function renderThreads(){threads.replaceChildren();for(const c of state.conversations){const row=el('div','thread'+(c.id===state.active?' active':''));const open=el('button','thread-title',c.title);open.title=c.title;open.onclick=()=>select(c.id);const del=el('button','delete-thread');del.append(icon('close'));del.title='Supprimer';del.setAttribute('aria-label','Supprimer '+c.title);del.onclick=()=>remove(c.id);const tab=el('button','delete-thread');tab.append(icon('window'));tab.title='Ouvrir dans une nouvelle fenêtre';tab.setAttribute('aria-label','Ouvrir '+c.title+' dans une nouvelle fenêtre');tab.onclick=()=>openTab(c.id);row.append(open,tab,del);threads.append(row)}}
let stickNext=false;
function render(){
  const c=current();renderThreads();$('conversationTitle').textContent=c?.title?.toUpperCase()||'NOUVELLE CONVERSATION';
  // on ne suit le bas que si on y était déjà : sinon on garde la position de lecture (l'utilisateur peut remonter pendant la génération)
  const prevTop=feed.scrollTop,follow=stickNext||feed.scrollHeight-feed.scrollTop-feed.clientHeight<80;stickNext=false;
  const inner=[...feed.querySelectorAll('.agent-grid,.agent-body,.tool pre.result,.sbs-wrap')].map(e=>[e.scrollLeft,e.scrollTop]);
  let oldTurns=0;if(c&&ctxInfo&&ctxInfo.summary_chars>0&&ctxInfo.turns>0){oldTurns=Math.max(0,c.messages.filter(x=>x.role==='user').length-ctxInfo.turns)}
  {let u=0;for(const x of (c?c.messages:[])){if(x.role==='user')u++;x._old=u>0&&u<=oldTurns}}
  feed.replaceChildren();
  document.querySelector('.content').classList.toggle('wide',!!c&&c.messages.some(m=>m.blocks&&m.blocks.filter(b=>b.t==='tool'&&b.agent).length>=2));
  sendBtn.replaceChildren(document.createTextNode(busy?'STOP':'ENVOYER'),icon(busy?'stop':'send'));sendBtn.classList.toggle('stop',busy);
  if(!c||c.messages.length===0){
    feed.innerHTML=`<div class="empty"><div class="empty-kicker">/ TON ESPACE DE TRAVAIL</div><h1>Des idées.<br><em>Du concret.</em></h1><p>Écris ta demande, choisis un agent si besoin. Une interface brute, conçue pour penser et produire sans se perdre dans les menus.</p><div class="suggestions"><button class="suggestion" data-agent="architect" data-prompt="Conçois l’architecture de mon système multi-agents."><span>Concevoir une architecture</span><small>01 / RÉFLEXION ↗</small></button><button class="suggestion" data-agent="reviewer" data-prompt="Relis mon code et détecte les problèmes critiques."><span>Faire une review de code</span><small>02 / REVIEWER ↗</small></button><button class="suggestion" data-agent="developer" data-prompt="Crée un endpoint FastAPI pour exécuter une mission."><span>Écrire un endpoint API</span><small>03 / CODEUR ↗</small></button><button class="suggestion" data-agent="auto" data-prompt="Décompose mon objectif en tâches prioritaires."><span>Préparer un plan d’action</span><small>04 / ROUTEUR ↗</small></button></div></div>`;
    feed.querySelectorAll('[data-prompt]').forEach(b=>b.addEventListener('click',()=>{prompt.value=b.dataset.prompt;agent.value=b.dataset.agent;savePrefs();prompt.focus()}));return}
  const wrap=el('div','conversation');let prevOld=false;for(const m of c.messages){if(prevOld&&!m._old){const dv=el('div','compact-divider');dv.append(el('b','','🗜 CONTEXTE COMPACTÉ'),el('span','','Au-dessus : résumé pour le modèle (tu le vois encore ici). En dessous : ce qu’il a sous les yeux.'));wrap.append(dv)}prevOld=!!m._old;wrap.append(renderMessage(m))}feed.append(wrap);
  linkifyPaths(feed);
  feed.querySelectorAll('.agent-grid,.agent-body,.tool pre.result,.sbs-wrap').forEach((e,i)=>{if(inner[i]){e.scrollLeft=inner[i][0];e.scrollTop=inner[i][1]}});
  feed.scrollTop=follow?feed.scrollHeight:prevTop;
}
// Pendant la génération, seul le message en cours change : on ne reconstruit que lui (et pas toute la conversation), ~15 images/s.
const SCROLLERS='.agent-grid,.agent-body,.tool pre.result,.sbs-wrap';let lastR=0,needFull=false;
// Réconciliation DOM : conserve identité des éléments, scroll, focus et détails ouverts.
// Les événements NDJSON restent inchangés : seules les vues sont recalculées.
function reconcileDom(oldNode,newNode){
  if(oldNode.nodeType!==newNode.nodeType||oldNode.nodeName!==newNode.nodeName){oldNode.replaceWith(newNode);return newNode}
  if(oldNode.nodeType===Node.TEXT_NODE){if(oldNode.nodeValue!==newNode.nodeValue)oldNode.nodeValue=newNode.nodeValue;return oldNode}
  if(oldNode.nodeType!==Node.ELEMENT_NODE)return oldNode;
  const focused=oldNode===document.activeElement;
  const detailOpen=oldNode.tagName==='DETAILS'?oldNode.open:null;
  for(const a of [...oldNode.attributes])if(!(focused&&a.name==='value')&&!newNode.hasAttribute(a.name))oldNode.removeAttribute(a.name);
  for(const a of [...newNode.attributes])if(a.name!=='open'&&!(focused&&a.name==='value')&&oldNode.getAttribute(a.name)!==a.value)oldNode.setAttribute(a.name,a.value);
  if(oldNode.tagName==='INPUT'||oldNode.tagName==='TEXTAREA')return oldNode;
  const incomingChildren=[...newNode.childNodes];
  let i=0;
  while(i<incomingChildren.length){
    const incoming=incomingChildren[i];let existing=oldNode.childNodes[i];
    const key=incoming.nodeType===Node.ELEMENT_NODE?incoming.dataset.viewKey:null;
    if(key&&existing?.dataset?.viewKey!==key){const match=[...oldNode.children].find(child=>child.dataset.viewKey===key);if(match){oldNode.insertBefore(match,existing||null);existing=match}else{oldNode.insertBefore(incoming,existing||null);i++;continue}}
    else if(!key&&existing?.dataset?.viewKey){oldNode.insertBefore(incoming,existing);i++;continue}
    if(!existing){oldNode.append(incoming);i++;continue}
    reconcileDom(existing,incoming);i++;
  }
  while(oldNode.childNodes.length>i)oldNode.lastChild.remove();
  if(detailOpen!==null)oldNode.open=detailOpen;
  return oldNode;
}
function renderLive(){
  const m=liveM,c=current();if(!m||!c||!m.id||!c.messages.includes(m))return render();
  const old=feed.querySelector('[data-mid="'+m.id+'"]');if(!old)return render();
  const prevTop=feed.scrollTop,follow=feed.scrollHeight-feed.scrollTop-feed.clientHeight<80;
  const n=renderMessage(m);linkifyPaths(n);
  const positions=new Map();old.querySelectorAll(SCROLLERS).forEach((e,i)=>positions.set(i,[e.scrollLeft,e.scrollTop]));
  reconcileDom(old,n);
  old.querySelectorAll(SCROLLERS).forEach((e,i)=>{const p=positions.get(i);if(p){e.scrollLeft=p[0];e.scrollTop=p[1]}});
  if(m.blocks?.some(b=>b.t==='tool'&&b.agent))document.querySelector('.content').classList.add('wide');
  feed.scrollTop=follow?feed.scrollHeight:prevTop;
  refreshInspector();
}
function schedule(){if(raf)return;raf=1;setTimeout(()=>requestAnimationFrame(()=>{raf=0;lastR=performance.now();if(busy&&liveM&&!needFull)renderLive();else{needFull=false;render()}}),Math.max(0,66-(performance.now()-lastR)))}

// --- jauge de contexte + compaction manuelle ---
const kfmt=n=>n==null?'?':n>=1e6?(n/1e6).toFixed(1)+' M':Math.round(n/1000)+' k';
function paintCtx(i){if(!i)return;ctxInfo=i;const pct=Math.min(100,i.pct_compact);ctxFill.style.width=pct+'%';ctxLine.classList.toggle('warn',i.pct_compact>=80);
  ctxText.textContent='CONTEXTE ≈ '+kfmt(i.tokens)+(i.window?' / '+kfmt(i.window)+' ('+i.pct_window+' %)':'')+' · AUTO-COMPACTION À '+kfmt(i.compact_at)+(i.summary_chars?' · RÉSUMÉ ✓':'')}
async function refreshCtx(full){const c=current();if(!c)return;try{const r=await api('/api/threads/'+encodeURIComponent(c.id)+'/context');if(r.ok){paintCtx(await r.json());if(full===true)needFull=true;schedule()}}catch{}}
compactBtn.onclick=async()=>{const c=current();if(!c||busy)return;const focus=window.prompt('Compacter maintenant. Consigne optionnelle pour le résumé (ex. « garde surtout les décisions ») :','');if(focus===null)return;
  compactBtn.disabled=true;compactBtn.textContent='COMPACTAGE…';
  try{const r=await api('/api/threads/'+encodeURIComponent(c.id)+'/compact',{method:'POST',body:JSON.stringify({focus})});const j=await r.json().catch(()=>({}));
    const m=[...c.messages].reverse().find(x=>x.role==='assistant');const text=r.ok?j.message:(j.detail||'Erreur '+r.status);
    if(m&&m.blocks){m.blocks.push({t:'notice',level:r.ok&&j.ok?'info':'warn',text:(r.ok&&j.ok?'🗜 ':'')+text})}else alert(text);
    if(j.context)paintCtx(j.context);save();render()}catch(e){alert('Compaction impossible : '+e.message)}
  finally{compactBtn.disabled=false;compactBtn.textContent='COMPACTER ↗'}};

// --- événements du backend (NDJSON) ---
function appendText(m,t){const last=m.blocks[m.blocks.length-1];if(last&&last.t==='text'&&last.open)last.text+=t;else m.blocks.push({t:'text',text:t,open:true})}
function closeText(m){const last=m.blocks[m.blocks.length-1];if(last&&last.t==='text')last.open=false}
function handle(ev,m){
  lastEvent=Date.now();
  switch(ev.type){
    case 'status':m.status=ev.text;break;
    case 'token':if(firstOutputMs===null&&runStart)firstOutputMs=Date.now()-runStart;m.status='';m.chars=(m.chars||0)+ev.text.length;appendText(m,ev.text);break;
    case 'ai':if(ev.text&&!ev.streamed){closeText(m);m.blocks.push({t:'text',text:ev.text,open:false})}closeText(m);break;
    case 'tool_call':if(ev.name==='update_plan')break;{const lb=m.blocks[m.blocks.length-1];if(lb&&lb.t==='text')lb.narr=true}closeText(m);if(ev.name==='spawn_workers'){m.status='Workers en cours…';break}m.blocks.push({t:'tool',id:ev.id,name:ev.name,summary:ev.summary,agent:ev.agent,panel:ev.panel,model:ev.model,status:'run',start:Date.now(),steps:[]});m.status=ev.agent?label(ev.agent)+(ev.model?' · '+ev.model:'')+(ev.panel?' (panel : plusieurs relecteurs en parallèle)':'')+' au travail…':ev.name+'…';break;
    case 'tool_result':{if(ev.name==='update_plan')break;if(ev.name==='spawn_workers'){if(ev.status==='error')m.blocks.push({t:'notice',level:'warn',text:ev.text});break}const b=m.blocks.find(x=>x.t==='tool'&&x.id===ev.id);if(b){b.status=ev.status==='error'?'error':'ok';b.result=ev.text;b.truncated=ev.truncated;b.agent=ev.agent||b.agent;b.panel=ev.panel||b.panel;b.model=ev.model||b.model;b.end=Date.now();if(b.steps)for(const x of b.steps)if(x.status==='run')x.status='ok'}else m.blocks.push({t:'tool',id:ev.id,name:ev.name,agent:ev.agent,panel:ev.panel,model:ev.model,status:ev.status==='error'?'error':'ok',result:ev.text,truncated:ev.truncated})}break;
    case 'agent_start':closeText(m);m.blocks.push({t:'tool',id:ev.id,name:ev.agent,agent:ev.agent,label:ev.label,summary:ev.summary,model:ev.model,status:'run',start:Date.now(),steps:[],member:true});m.status=label(ev.agent)+' · '+(ev.label||'')+' au travail…';break;
    case 'agent_token':{if(firstOutputMs===null&&runStart)firstOutputMs=Date.now()-runStart;m.chars=(m.chars||0)+ev.text.length;const b=m.blocks.find(x=>x.t==='tool'&&x.id===ev.id);if(b)b.live=((b.live||'')+ev.text).slice(-700)}break;
    case 'agent_done':{const b=m.blocks.find(x=>x.t==='tool'&&x.id===ev.id);if(b){b.status=ev.status==='error'?'error':'ok';b.result=ev.text;b.end=Date.now();b.live='';if(ev.model)b.model=ev.model;if(b.steps)for(const x of b.steps)if(x.status==='run')x.status='ok'}}break;
    case 'agent_step':{const b=m.blocks.find(x=>x.t==='tool'&&x.id===ev.id);if(!b)break;b.steps=b.steps||[];
      if(ev.kind==='thought'){b.steps.push({k:'thought',text:ev.text})}
      else if(ev.kind==='tool_call'){b.steps.push({k:'tool',call:ev.call,name:ev.name,summary:ev.summary,status:'run'})}
      else if(ev.kind==='tool_result'){const x=b.steps.find(y=>y.k==='tool'&&y.call===ev.call);if(x){x.status=ev.status;x.text=ev.text}}
      if(b.steps.length>80)b.steps.splice(0,b.steps.length-80);m.status=label(b.agent)+(b.model?' · '+b.model:'')+(b.panel?' · PANEL':'')+' au travail… ('+b.steps.filter(x=>x.k==='tool').length+' outils)'}break;
    case 'plan':{const i=m.blocks.findIndex(x=>x.t==='plan');const b={t:'plan',todos:ev.todos};if(i>=0)m.blocks[i]=b;else m.blocks.push(b)}break;
    case 'notice':m.blocks.push({t:'notice',level:ev.level,text:ev.text});if(/^Contexte compacté/.test(ev.text||''))refreshCtx(true);break;
    case 'auto':if(ev.preview){closeText(m);m.blocks.push({t:'approval',calls:[{id:ev.id||('auto-'+Date.now()),name:ev.name,summary:ev.summary,preview:ev.preview}],choices:{},resolved:true,auto:true,decided:{}})}else m.blocks.push({t:'notice',level:'info',text:'⚡ auto : '+ev.name+'('+ev.summary+') — '+ev.why});break;
    case 'approval':closeText(m);m.blocks.push({t:'approval',calls:ev.calls,choices:{},resolved:false});break;
    case 'done':if(ev.supervisor_model)m.model=ev.supervisor_model;m.usage=(ev.usage||'')+((ev.models||[]).length?' · modèles : '+ev.models.join(' · '):'');m.status='';if(ev.context)paintCtx(ev.context);break;
    case 'error':m.blocks.push({t:'notice',level:'warn',text:'Erreur : '+ev.text});m.status='';break;
  }
}
async function run(endpoint,payload,m){
  busy=true;liveM=m;m.chars=0;controller=new AbortController();runStart=lastEvent=Date.now();firstOutputMs=null;m.status=m.status||'Réflexion…';render();
  try{
    const r=await api(endpoint,{method:'POST',body:JSON.stringify(payload),signal:controller.signal});
    if(!r.ok){let d='';try{d=(await r.json()).detail||''}catch{}
      m.blocks.push({t:'notice',level:'warn',text:r.status===401?(askToken()?'Jeton mis à jour : renvoie ton message.':'Jeton absent ou invalide : recharge la page pour le saisir.'):r.status===409?(d||'Un tour est déjà en cours.'):'Erreur '+r.status+(d?' : '+d:'')});return}
    const reader=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){const {value,done}=await reader.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;while((i=buf.indexOf('\n'))>=0){const line=buf.slice(0,i).trim();buf=buf.slice(i+1);if(line){try{handle(JSON.parse(line),m)}catch(e){console.warn('Événement illisible',line)}schedule()}}}
  }catch(e){m.blocks.push({t:'notice',level:'warn',text:e.name==='AbortError'?'Interrompu.':'Connexion perdue : '+e.message});if(e.name!=='AbortError')setStatus(false)}
  finally{busy=false;liveM=null;controller=null;runStart=0;m.status='';closeText(m);save();render()}
}
function body(c,extra){return {thread_id:c.id,agent:agent.value,mode:modeSel.value,delegation:deleg.value,auto:autoBox.checked,...extra}}
function submit(){
  if(busy){controller?.abort();return}
  const value=prompt.value.trim();if(!value)return;let c=current();if(!c){start();c=current()}
  if(c.messages.length===0)c.title=value.replace(/\s+/g,' ').slice(0,45)+(value.length>45?'…':'');
  c.messages.push({id:makeId(),role:'user',text:value});
  const m={id:makeId(),role:'assistant',model:modelDisplay(configuredModel()),agent:agent.value==='auto'?'Superviseur':label(agent.value),blocks:[],status:'Réflexion…'};c.messages.push(m);
  prompt.value='';stickNext=true;save();run('/api/chat',body(c,{message:value}),m);
}
function resume(m,b){
  if(busy)return;const c=current();b.resolved=true;b.decided={...b.choices};save();
  m.status='Reprise…';stickNext=true;run('/api/resume',body(c,{decisions:b.choices}),m);
}
// AUTO coché alors qu'une confirmation est affichée : on la réévalue (elle peut disparaître ou se réduire).
async function reevaluate(){
  const c=current();if(!c||busy)return;const m=[...c.messages].reverse().find(x=>x.role==='assistant');const b=m?.blocks?.find(x=>x.t==='approval'&&!x.resolved);if(!b)return;
  try{const r=await api('/api/threads/'+encodeURIComponent(c.id)+'/pending?auto=true');if(!r.ok)return;const {pending,waiting}=await r.json();
    if(!waiting){b.resolved=true;b.decided={};m.blocks.push({t:'notice',level:'info',text:'Confirmation expirée ou déjà traitée.'});save();render();return}
    if(!pending){m.blocks.push({t:'notice',level:'info',text:'⚡ AUTO : plus rien à confirmer, reprise automatique.'});b.choices={};resume(m,b);return}
    const keep=b.choices;b.calls=pending;b.choices=Object.fromEntries(pending.filter(p=>keep[p.id]).map(p=>[p.id,keep[p.id]]));save();render()}catch{}
}
// Au rechargement : une confirmation affichée peut avoir été traitée ou périmée côté serveur.
async function syncPending(){
  const c=current();if(!c||busy)return;const m=[...c.messages].reverse().find(x=>x.role==='assistant');const b=m?.blocks?.find(x=>x.t==='approval'&&!x.resolved);if(!b)return;
  try{const r=await api('/api/threads/'+encodeURIComponent(c.id)+'/pending');if(!r.ok)return;const {pending}=await r.json();
    if(!pending){b.resolved=true;b.decided={};m.blocks.push({t:'notice',level:'info',text:'Confirmation expirée ou déjà traitée.'})}else{b.calls=pending}
    save();render()}catch{}
}
function useSkill(n){if(busy)return;prompt.value='Applique le skill « '+n+' ».';prompt.focus();prompt.setSelectionRange(prompt.value.length,prompt.value.length);closeMenu()}
async function loadSkills(){const list=$('skillList');skillSel.querySelectorAll('option:not([value=""])').forEach(o=>o.remove());
  const msg=t=>{list.replaceChildren();const b=el('button','',t);b.type='button';b.disabled=true;b.style.opacity='.6';list.append(b)};
  try{const r=await api('/api/skills');if(!r.ok){msg(r.status===404?'indisponible — relance cc-web':'erreur '+r.status);return}
    const {skills,warnings}=await r.json();list.replaceChildren();
    if(!skills.length)msg('aucun skill');
    for(const s of skills){const b=document.createElement('button');b.type='button';b.title=s.description;const sym=el('span','nav-symbol');sym.append(icon('skills'));b.append(sym,' '+s.name);b.onclick=()=>useSkill(s.name);list.append(b);
      const o=document.createElement('option');o.value=s.name;o.textContent=s.name.toUpperCase();o.title=s.description;skillSel.append(o)}
    for(const w of warnings||[]){const b=el('button','','⚠ '+w);b.type='button';b.disabled=true;b.style.cssText='opacity:.7;color:var(--pink);font-size:10px';list.append(b)}
  }catch{msg('indisponible')}}
skillSel.onchange=()=>{const n=skillSel.value;if(!n)return;useSkill(n);skillSel.value=''};
function setStatus(ok,text){statusEl.textContent=ok?'● CONNECTÉ':'● HORS LIGNE';statusEl.className='status '+(ok?'ok':'bad');if(text)foot.innerHTML=text}

// --- méta (rôles) : le sélecteur d'agent suit la liste réelle, agents personnalisés compris ---
let metaCache={readonly_tools:[],models:[]},connectionSetupShown=false;
function configuredModel(){return (metaCache.role_models||{})[agent.value==='auto'?'supervisor':agent.value]||supModel||''}
function modelDisplay(model){const routes=metaCache.model_routes||{};let route=routes[model];if(!route&&typeof model==='string'){const matches=Object.values(routes).filter(r=>r.model===model||r.model.split('/').pop()===model||model.endsWith('/'+r.model)),unique=new Map(matches.map(r=>[r.provider+'/'+r.model,r]));if(unique.size===1)route=unique.values().next().value}return route?({deepseek:'DeepSeek',openrouter:'OpenRouter',groq:'Groq',cerebras:'Cerebras',ollama:'Ollama'}[route.provider]||route.provider)+' · '+route.model:model}

function updateModelIndicator(){const model=configuredModel(),button=$('activeModel');$('activeModelName').textContent=(metaCache.free_only?'0 $ · ':'')+(model?modelDisplay(model):'Modèles');button.title=model?'Modèle configuré : '+modelDisplay(model)+' · modifier les modèles':'Configurer les modèles'}

function applyMeta(meta){metaCache=meta;for(const k of Object.keys(roleTitles))if(k!=='coder')delete roleTitles[k];for(const x of meta.roles||[])roleTitles[x.key]=x.title;roleTitles.worker='Worker';
  const cur=agent.value;agent.replaceChildren(...[['auto','AUTO / SUPERVISEUR'],...(meta.roles||[]).map(x=>[x.key,x.title.toUpperCase()])].map(([v,t])=>{const o=document.createElement('option');o.value=v;o.textContent=t;return o}));
  agent.value=[...agent.options].some(o=>o.value===cur)?cur:'auto';updateModelIndicator()}
async function refreshRoles(){try{const r=await api('/api/meta');if(r.ok)applyMeta(await r.json())}catch{}}

// --- Studio : créer / modifier / supprimer skills et agents ---
const studio=$('studio'),studioList=$('studioList'),studioForm=$('studioForm');
let sTab='skills',sSel=null,flash='';
function studioTabs(){studio.querySelectorAll('.studio-tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===sTab))}
function openStudio(tab){sTab=tab||sTab;studio.hidden=false;studioTabs();loadStudioList()}
function closeStudio(){studio.hidden=true}
studio.querySelectorAll('.studio-tabs button').forEach(b=>b.onclick=()=>{sTab=b.dataset.tab;sSel=null;flash='';studioTabs();loadStudioList()});
$('studioClose').onclick=closeStudio;studio.addEventListener('click',e=>{if(e.target===studio)closeStudio()});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!studio.hidden)closeStudio()});
$('openStudio').onclick=()=>{openStudio();closeMenu()};
$('openModels').onclick=()=>{openStudio('models');closeMenu()};
$('activeModel').onclick=()=>openStudio('models');
$('openConnections').onclick=()=>{openStudio('connections');closeMenu()};
const lbl=t=>el('label','lbl',t);
function inp(type,val,dis){const i=document.createElement('input');i.type=type;i.value=val??'';i.disabled=!!dis;return i}
function ta(val,dis,tall){const t=document.createElement('textarea');t.value=val??'';t.disabled=!!dis;if(tall)t.className='tall';return t}
function detailMsg(j,status){const d=j&&j.detail;return d?(typeof d==='string'?d:d.map(x=>((x.loc||[]).slice(-1)[0]||'champ')+' : '+x.msg).join('\n')):'Erreur '+status}
async function loadStudioList(select){
  if(sTab==='models')return modelsPane();
  if(sTab==='connections')return gatewayPane();
  studioList.replaceChildren();studioForm.replaceChildren();
  const nb=el('button','choice new','＋ NOUVEAU');nb.type='button';nb.onclick=()=>{sSel='__new__';[...studioList.querySelectorAll('.item')].forEach(x=>x.classList.remove('active'));showForm(null)};studioList.append(nb);
  try{
    const r=await api(sTab==='skills'?'/api/skills':'/api/agents');if(!r.ok)throw new Error('HTTP '+r.status);
    const j=await r.json();const items=sTab==='skills'?j.skills:j.agents;let target=null;
    for(const it of items){const key=it.name||it.key;const b=el('button','item'+(sSel===key?' active':''));b.type='button';b.append(document.createTextNode(sTab==='skills'?key:it.title));
      b.append(el('small','',(it.builtin?'intégré':'à toi')+(it.overrides_builtin?' · remplace l’intégré':'')+(sTab==='agents'?' · '+key:'')));
      b.onclick=()=>{sSel=key;flash='';[...studioList.querySelectorAll('.item')].forEach(x=>x.classList.toggle('active',x===b));showForm(it)};studioList.append(b);if(key===select)target=[b,it]}
    for(const w of j.warnings||[])studioList.append(el('div','studio-msg','⚠ '+w));
    if(target){target[0].classList.add('active');sSel=select;showForm(target[1])}
    else studioForm.append(el('p','studio-note',sTab==='skills'?'Un skill est une procédure que le superviseur charge à la demande (par exemple « cartographie de stack »). Choisis-en un à gauche pour le lire ou le modifier, ou crée le tien.':'Un agent personnalisé est un spécialiste que le superviseur peut appeler. Il est en lecture seule : tu choisis ses outils parmi ceux qui ne modifient rien. Choisis-en un à gauche ou crée le tien.'));
  }catch(e){studioForm.append(el('div','studio-msg','Chargement impossible : '+e.message))}
}
function actions(msg,buttons){const a=el('div','studio-actions');for(const b of buttons)a.append(b);studioForm.append(a,msg);if(flash){msg.className='studio-msg ok';msg.textContent=flash;flash=''}}
function btn(text,cls,fn){const b=el('button',cls,text);b.type='button';b.onclick=fn;return b}
async function put(url,payload,msg){msg.className='studio-msg';msg.textContent='Enregistrement…';
  try{const r=await api(url,{method:'PUT',body:JSON.stringify(payload)});const j=await r.json().catch(()=>({}));if(!r.ok){msg.textContent=detailMsg(j,r.status);return false}return true}catch(e){msg.textContent='Connexion impossible : '+e.message;return false}}
async function del(url,msg,what){if(!confirm(what))return false;try{const r=await api(url,{method:'DELETE'});const j=await r.json().catch(()=>({}));if(!r.ok){msg.textContent=detailMsg(j,r.status);return false}return j}catch(e){msg.textContent='Connexion impossible : '+e.message;return false}}


// --- Connexions : private provider keys and routing aliases for the local LiteLLM gateway. ---
async function gatewayPane(){
 studioList.replaceChildren();studioForm.replaceChildren();
 studioList.append(el('p','studio-note','AgentOS → LiteLLM local → fournisseurs. Le superviseur et les workers appellent des alias ; LiteLLM choisit le fournisseur et gère les modèles de secours.'));
 studioList.append(el('p','studio-note','Deux variantes gratuites OpenRouter partagent leur quota quotidien. Pour un secours indépendant, choisis un autre fournisseur. Un modèle payant peut entraîner des frais.'));
 let data;try{const response=await api('/api/gateway');if(!response.ok)throw new Error('HTTP '+response.status);data=await response.json()}catch(error){studioForm.append(el('p','studio-msg','Connexion impossible : '+error.message));return}
 const heading=el('div','gateway-heading');heading.append(el('h2','','Passerelle LiteLLM'),el('span','gateway-state',data.free_only?(data.enabled&&data.running?'Budget 0 $ actif':'Budget 0 $ · à activer'):(data.enabled?(data.running?'Active':'Activée · arrêtée'):'Connexion directe')));studioForm.append(heading);
 studioForm.append(el('p','studio-note',data.installed?'LiteLLM est installé. Adresse locale : '+data.url:'Installe la passerelle avec cc-gateway --install, puis actualise ce panneau.'));
 const providers={deepseek:'DeepSeek',openrouter:'OpenRouter',groq:'Groq',cerebras:'Cerebras',ollama:'Ollama local'};
 const budgetWrap=el('label','gateway-budget'),budget=inp('checkbox','');budget.checked=!!data.free_only;budgetWrap.append(budget,el('b','','Budget strict 0 $ — superviseur et secours compris'));studioForm.append(budgetWrap);
 studioForm.append(el('p','catalog-note','Ce mode accepte uniquement la liste gratuite autorisée. Les clés payantes sont conservées localement, mais ne sont pas transmises au proxy.'));

 studioForm.append(el('div','mhead','CLÉS DES FOURNISSEURS'));
 const keyFields={};
 for(const [provider,title] of Object.entries(providers)){if(provider==='ollama')continue;const wrap=el('label','gateway-key');wrap.append(el('span','',title),el('small','',data.keys[provider]?'Clé disponible · laisser vide pour conserver':'Clé non renseignée'));const input=inp('password','');input.autocomplete='new-password';input.placeholder=data.keys[provider]?'Clé déjà configurée':'Clé API '+title;input.setAttribute('aria-label','Clé API '+title);wrap.append(input);studioForm.append(wrap);keyFields[provider]=input}
 studioForm.append(el('div','mhead','ROUTES ET MODÈLES DE SECOURS'));
 studioForm.append(el('p','catalog-note','Le fournisseur et l’identifiant définissent la destination de chaque alias. Exemple OpenRouter : nvidia/nom-du-modele:free ; exemple DeepSeek : deepseek-flash.'));
 const routeList=el('div','gateway-routes');studioForm.append(routeList);const routes=[];
 function select(label,options){const control=document.createElement('select');control.setAttribute('aria-label',label);for(const [value,text] of options){const option=document.createElement('option');option.value=value;option.textContent=text;control.append(option)}return control}
 function fallbacks(){for(const row of routes){const current=row.fallback.value;row.fallback.replaceChildren();for(const value of ['',...routes.filter(other=>other!==row).map(other=>other.alias.value)]){const option=document.createElement('option');option.value=value;option.textContent=value||'Aucun secours';row.fallback.append(option)}row.fallback.value=[...row.fallback.options].some(option=>option.value===current)?current:''}}
 function addRoute(route){const card=el('div','gateway-route'),alias=inp('text',route.alias),provider=select('Fournisseur de '+route.alias,Object.entries(providers)),model=inp('text',route.model),fallback=select('Secours de '+route.alias,[['','Aucun secours']]);provider.value=route.provider;const core=['superviseur','worker','worker_secours','agent_code','agent_recherche','agent_redaction','agent_validation'].includes(route.alias);alias.readOnly=core;alias.setAttribute('aria-label','Alias '+route.alias);model.setAttribute('aria-label','Modèle de '+route.alias);model.spellcheck=false;model.autocomplete='off';
  const title=el('div','gateway-route-title');title.append(alias);if(!core)title.append(btn('Supprimer','tiny-action',()=>{routes.splice(routes.indexOf(row),1);card.remove();fallbacks()}));card.append(title);
  for(const [label,control] of [['Fournisseur',provider],['Modèle',model],['Secours',fallback]]){const wrap=el('label','gateway-field');wrap.append(el('span','',label),control);card.append(wrap)}
  const row={alias,provider,model,fallback};routes.push(row);routeList.append(card);alias.onchange=fallbacks;fallbacks();return row}
 for(const route of data.routes)addRoute(route);
 for(let i=0;i<routes.length;i++)routes[i].fallback.value=data.routes[i].fallback||'';
 budget.onchange=()=>{if(budget.checked&&data.free_routes){routes.length=0;routeList.replaceChildren();for(const route of data.free_routes)addRoute(route);for(let i=0;i<routes.length;i++)routes[i].fallback.value=data.free_routes[i].fallback||''}};

 studioForm.append(btn('Ajouter une route','choice',()=>addRoute({alias:'route_'+(routes.length+1),provider:'openrouter',model:''})));
 const msg=el('div','studio-msg');msg.setAttribute('role','status');let saving=false;
 async function save(activate){if(saving)return;saving=true;saveBtn.disabled=startBtn.disabled=true;
  const payload={free_only:budget.checked,routes:routes.map(row=>({alias:row.alias.value.trim(),provider:row.provider.value,model:row.model.value.trim(),fallback:row.fallback.value||null})),keys:{}};
  for(const [provider,input] of Object.entries(keyFields))if(input.value.trim())payload.keys[provider]=input.value.trim();
  try{if(!await put('/api/gateway',payload,msg))return;for(const input of Object.values(keyFields))input.value='';
   if(activate){msg.textContent='Démarrage de LiteLLM…';const response=await api('/api/gateway/start',{method:'POST'});if(!response.ok){msg.textContent=detailMsg(await response.json().catch(()=>({})),response.status);return}}
   flash=activate?'LiteLLM est actif. Les prochains appels passent par la passerelle.':'Configuration enregistrée.';await gatewayPane();init();
  }catch(error){msg.className='studio-msg';msg.textContent='Connexion impossible : '+error.message}
  finally{saving=false;saveBtn.disabled=startBtn.disabled=false}
 }
 const saveBtn=btn('Enregistrer la configuration','choice',()=>save(false)),startBtn=btn('Enregistrer et activer LiteLLM','send',()=>save(true));startBtn.disabled=!data.installed;
 const direct=btn('Revenir à la connexion directe','choice',async()=>{msg.textContent='Retour à la connexion directe…';try{const response=await api('/api/gateway/stop',{method:'POST'});if(!response.ok){msg.textContent=detailMsg(await response.json().catch(()=>({})),response.status);return}flash='Connexion directe rétablie ; les anciens choix de modèles sont conservés.';await gatewayPane();init()}catch(error){msg.textContent=error.message}});direct.disabled=!data.enabled||!!data.free_only;
 actions(msg,[saveBtn,startBtn,direct,btn('Actualiser','tiny-action',()=>gatewayPane())]);
}

// --- Studio · MODÈLES : suggestions du fournisseur et identifiants libres ---
async function modelsPane(force){
  studioList.replaceChildren();studioForm.replaceChildren();
  studioList.append(el('p','studio-note','Choisis ou saisis le modèle de chaque agent. Les suggestions viennent de ton fournisseur ; tu peux aussi entrer un identifiant complet, par exemple fournisseur/modele. Les modèles de repli restent inchangés.'));
  studioList.append(btn('Configurer LiteLLM','choice',()=>openStudio('connections')));
  let j;try{const r=await api('/api/models'+(force?'?refresh=true':''));if(!r.ok)throw new Error('HTTP '+r.status);j=await r.json()}catch(e){studioForm.append(el('div','studio-msg','Chargement impossible : '+e.message));return}
  metaCache.available=j.available||[];const avail=metaCache.available;
  const suggestions=document.createElement('datalist');suggestions.id='availableModels';
  for(const m of avail){const option=document.createElement('option');option.value=m.id;option.label=m.window?Math.round(m.window/1000)+' k tokens':m.id;suggestions.append(option)}
  studioForm.append(suggestions);
  function picker(cur,optional){
    const input=inp('text',cur);input.setAttribute('list','availableModels');input.autocomplete='off';input.spellcheck=false;
    input.placeholder=optional?'Facultatif — fournisseur/modele':'fournisseur/modele';return input}
  const msg=el('div','studio-msg');
  if(j.error)studioForm.append(el('p','studio-note',j.error+' — liste des modèles indisponible : saisie libre.'));
  const assignmentsHeading=el('div','mhead','MODÈLE PAR AGENT');studioForm.append(assignmentsHeading);
  const rows=[];
  for(const r of j.roles){const row=el('div','mrow');const nm=el('div','nm',r.title);nm.append(el('small','',r.key+(r.default!==r.model?' · défaut : '+r.default.split('/').pop():'')));
    if(j.routing==='litellm')nm.append(el('small','',modelDisplay(r.model)));
    const ctl=picker(r.model);ctl.setAttribute('aria-label','Modèle — '+r.title);
    row.append(nm,ctl,el('div','src '+(r.source==='modifié'?'mod':r.source==='env'?'env':''),r.source==='env'?'VARIABLE D’ENV':r.source.toUpperCase()));studioForm.append(row);rows.push([r,ctl])}
  studioForm.append(el('div','mhead','PANEL DE REVUE (2 à 4 relecteurs de familles différentes)'));
  const pctl=[],pv=j.panel.models;
  for(let i=0;i<4;i++){const row=el('div','mrow'),c=picker(pv[i]||'',i>=2);c.setAttribute('aria-label','Modèle du relecteur '+'ABCD'[i]);pctl.push(c);row.append(el('div','nm','Relecteur '+'ABCD'[i]),c,el('div','src '+(j.panel.source==='modifié'?'mod':j.panel.source==='env'?'env':''),i===0?(j.panel.source==='env'?'VARIABLE D’ENV':j.panel.source.toUpperCase()):''));studioForm.append(row)}
  mountModelCatalog(j, suggestions, rows.map(([r,control])=>({title:r.title,control})).concat(pctl.map((control,i)=>({title:'Relecteur '+'ABCD'[i],control}))), assignmentsHeading, msg);
  const save=btn('ENREGISTRER ↗','send',async()=>{
    const assignments={};for(const [r,c] of rows)if(c.value.trim()!==r.model){if(!c.value.trim()){msg.className='studio-msg';msg.textContent='Renseigne le modèle de '+r.title+'.';c.focus();return}assignments[r.key]=c.value.trim();}
    const panel=pctl.map(c=>c.value.trim()).filter(Boolean),panelChanged=JSON.stringify(panel)!==JSON.stringify(pv);
    if(!Object.keys(assignments).length&&!panelChanged){msg.className='studio-msg';msg.textContent='Rien à enregistrer.';return}
    if(await put('/api/models',{assignments,panel:panelChanged?panel:null},msg)){flash='✓ Enregistré — pris en compte immédiatement (les tours en cours finissent avec l’ancien modèle).';await modelsPane();init()}});
  const reset=btn('RÉINITIALISER TOUT','choice',async()=>{if(!confirm('Effacer les choix enregistrés et reprendre les modèles de l’environnement ou les valeurs par défaut ?'))return;
    try{const r=await api('/api/models/reset',{method:'POST',body:'{}'});if(r.ok){flash='Modèles par défaut restaurés.';await modelsPane();init()}else msg.textContent=detailMsg(await r.json().catch(()=>({})),r.status)}catch(e){msg.textContent='Connexion impossible : '+e.message}});
  actions(msg,[save,reset]);
}
// Catalogue filters only affect discovery; existing assignments and typed IDs are retained.
function mountModelCatalog(initial,suggestions,targets,before,msg){
 const section=el('section','model-catalog');section.setAttribute('aria-label','Catalogue des modèles');before.before(section);
 const heading=el('div','catalog-heading');heading.append(el('h2','','Catalogue des modèles'));section.append(heading);
 function select(label,options){const control=document.createElement('select');control.setAttribute('aria-label',label);for(const [value,text] of options){const option=document.createElement('option');option.value=value;option.textContent=text;control.append(option)}return control}
 const source=select('Source du catalogue',initial.routing==='litellm'?[['configured','Alias LiteLLM']]:[['configured',initial.openrouter?'OpenRouter connecté':'Fournisseur configuré'],['openrouter','Catalogue public OpenRouter']]);heading.append(source);
 const search=inp('search','');search.placeholder='Rechercher un modèle…';search.setAttribute('aria-label','Rechercher un modèle');
 const family=select('Famille du modèle',[['','Toutes les familles']]);
 const context=select('Contexte minimum',[['0','Tous les contextes'],['32000','32 k tokens minimum'],['128000','128 k tokens minimum']]);
 function toggle(label){const wrap=el('label','catalog-toggle'),input=inp('checkbox','');wrap.append(input,document.createTextNode(label));return {wrap,input}}
 const tools=toggle('Appels d’outils'),free=toggle('Gratuits');
 const filters=el('div','catalog-filters');filters.append(search,family,context,tools.wrap,free.wrap);section.append(filters);
 const assign=el('div','catalog-target'),target=select('Affecter le modèle à',targets.map((t,i)=>[String(i),t.title]));assign.append(el('span','','Affecter à'),target);section.append(assign);
 const note=el('p','catalog-note'),count=el('div','catalog-count'),results=el('div','catalog-results');count.setAttribute('aria-live','polite');section.append(note,count,results);
 const more=btn('Afficher plus','choice',()=>{limit+=40;render()});section.append(more);
 let models=initial.available||[],limit=40,requestId=0,catalogError=initial.error;
 function families(){family.replaceChildren();const all=document.createElement('option');all.value='';all.textContent='Toutes les familles';family.append(all);for(const value of [...new Set(models.map(m=>m.provider||m.id.split('/')[0]))].sort()){const option=document.createElement('option');option.value=value;option.textContent=value;family.append(option)}}
 function render(){
  const terms=search.value.toLowerCase().trim().split(/\s+/).filter(Boolean);
  const filtered=models.filter(m=>terms.every(term=>(m.id+' '+(m.name||'')).toLowerCase().includes(term))&&(!family.value||(m.provider||m.id.split('/')[0])===family.value)&&(!tools.input.checked||m.tools===true)&&(!free.input.checked||m.free===true)&&Number(m.window||0)>=Number(context.value));
  count.textContent=filtered.length+' modèle'+(filtered.length===1?'':'s')+' · '+models.length+' dans le catalogue';
  note.textContent=catalogError?(catalogError+(models.length?' — dernier catalogue conservé.':' — la saisie libre reste disponible.')):source.value==='openrouter'&&!initial.openrouter?'Catalogue public OpenRouter. Pour utiliser ces modèles, configure la connexion OpenRouter ; la sélection ne change pas ton fournisseur.':'Choisis un modèle pour remplir le rôle sélectionné, puis enregistre tes choix.';
  suggestions.replaceChildren();for(const m of filtered){const option=document.createElement('option');option.value=m.id;option.label=m.name||m.id;suggestions.append(option)}
  results.replaceChildren();
  if(!filtered.length)results.append(el('p','catalog-note','Aucun modèle ne correspond aux filtres.'));
  for(const m of filtered.slice(0,limit)){
   const button=btn('','catalog-model',()=>{const selected=targets[Number(target.value)];selected.control.value=m.id;selected.control.dispatchEvent(new Event('input',{bubbles:true}));msg.className='studio-msg';msg.textContent=m.id+' affecté à '+selected.title+' — enregistre pour appliquer.';announce.textContent=msg.textContent;});
   button.setAttribute('aria-label','Choisir '+m.id);button.append(el('b','',m.name||m.id),el('small','catalog-id',m.id));
   const facts=[m.window?Math.round(m.window/1000)+' k tokens':null,m.tools===true?'Outils':null,m.free===true?'Gratuit':null].filter(Boolean);button.append(el('small','',facts.join(' · ')||'Capacités non renseignées'));results.append(button);
  }
  more.hidden=filtered.length<=limit;
 }
 const announce=el('div','catalog-selection');announce.setAttribute('aria-live','polite');section.append(announce);
 async function load(force=false){const id=++requestId;source.disabled=true;count.textContent='Chargement du catalogue…';results.replaceChildren();more.hidden=true;
  try{const response=await api('/api/models/catalog?source='+source.value+(force?'&refresh=true':''));if(!response.ok)throw new Error('HTTP '+response.status);const data=await response.json();if(id!==requestId||!section.isConnected)return;models=data.available||[];catalogError=data.error;families();limit=40;render()}
  catch(error){if(id!==requestId||!section.isConnected)return;models=[];catalogError='Catalogue indisponible ('+error.message+')';families();render()}
  finally{if(id===requestId)source.disabled=false}
 }
 source.onchange=()=>{tools.input.checked=source.value==='openrouter'||!!initial.openrouter;load()};
 const refresh=btn('Actualiser','choice',()=>load(true));heading.append(refresh);
 for(const control of [search,family,context,tools.input,free.input])control.addEventListener('input',()=>{limit=40;render()});
 tools.input.checked=!!initial.openrouter;families();render();
}
async function showForm(it){studioForm.replaceChildren();return sTab==='skills'?skillForm(it):agentForm(it)}
async function skillForm(it){
  let d=it;if(it){try{const r=await api('/api/skills/'+encodeURIComponent(it.name));if(r.ok)d=await r.json()}catch{}}
  const ro=!!d&&!d.editable;
  const name=inp('text',d?.name,!!d),desc=ta(d?.description,ro),tools=inp('text',(d?.tools||[]).join(', '),ro),body=ta(d?.body,ro,true);
  name.placeholder='mon-skill  (a-z, 0-9, tirets)';desc.rows=2;tools.placeholder='scan_stack, read_file';body.placeholder='# Procédure\n1. …\n2. …';
  const msg=el('div','studio-msg');
  if(ro)studioForm.append(el('p','studio-note','Skill intégré : lecture seule. « PERSONNALISER » crée ta propre version, qui remplace l’intégrée ; l’original n’est jamais modifié et revient si tu supprimes la tienne.'));
  studioForm.append(lbl('Nom'),name,lbl('Description (ce que le superviseur lit pour choisir ce skill — quoi + quand)'),desc,lbl('Outils utilisés (informatif, séparés par des virgules)'),tools,lbl('Instructions (Markdown)'),body);
  const save=btn('ENREGISTRER ↗','send',async()=>{const n=(name.value||'').trim();if(await put('/api/skills/'+encodeURIComponent(n),{description:desc.value.trim(),tools:tools.value.split(',').map(x=>x.trim()).filter(Boolean),body:body.value},msg)){sSel=n;flash='✓ Enregistré — pris en compte immédiatement.';await loadSkills();await loadStudioList(n)}});
  const bs=[];
  if(ro){save.hidden=true;const cust=btn('PERSONNALISER','send',()=>{for(const x of[desc,tools,body])x.disabled=false;cust.hidden=true;save.hidden=false;desc.focus()});bs.push(cust)}
  bs.push(save);
  if(d&&d.editable){bs.push(btn(it&&it.overrides_builtin?'RESTAURER L’INTÉGRÉ':'SUPPRIMER','choice',async()=>{const j=await del('/api/skills/'+encodeURIComponent(d.name),msg,'Supprimer le skill « '+d.name+' » ?');if(j){sSel=null;flash=j.restored_builtin?'Version intégrée restaurée.':'Supprimé.';await loadSkills();await loadStudioList(j.restored_builtin?d.name:undefined)}}))}
  actions(msg,bs);
}
async function agentForm(it){
  const ro=!!it&&!it.editable,d=it||{};
  const key=inp('text',d.key,!!it),title=inp('text',d.title,ro),desc=ta(d.description||(ro?d.summary:''),ro),model=inp('text',d.model||'',ro),steps=inp('number',d.max_steps??8,ro),temp=inp('number',d.temperature??'',ro),prompt=ta(d.prompt,ro,true);
  key.placeholder='auditeur-secu  (a-z, 0-9, tirets)';title.placeholder='Auditeur sécurité';desc.rows=2;desc.placeholder='Ce que fait l’agent et quand l’utiliser (le superviseur le lit pour le choisir)';
  model.placeholder=ro?(d.effective_model||''):'vide = '+((metaCache.models||[])[0]||'modèle par défaut');model.setAttribute('list','modelList');
  try{if(!(metaCache.available||[]).length){const r=await api('/api/models');if(r.ok)metaCache.available=(await r.json()).available||[]}}catch{}
  const dl=document.createElement('datalist');dl.id='modelList';for(const m of (metaCache.available||[]).map(x=>x.id).concat(metaCache.models||[])){const o=document.createElement('option');o.value=m;dl.append(o)}
  steps.min=1;steps.max=16;temp.min=0;temp.max=1;temp.step=0.1;temp.placeholder='défaut';prompt.placeholder='Tu es … Ta méthode : …';
  const msg=el('div','studio-msg');
  if(ro)studioForm.append(el('p','studio-note','Agent intégré : consultable, non modifiable. Permissions imposées par le code : '+(d.permissions||'')+(d.effective_model?'  ·  Modèle : '+d.effective_model:'')));
  studioForm.append(lbl('Clé'),key,lbl('Titre'),title,lbl('Description'),desc);
  const r2=el('div','row2');const c1=el('div'),c2=el('div');c1.append(lbl('Modèle (optionnel)'),model,dl);c2.append(lbl('Tours d’outils max (1–16)'),steps);r2.append(c1,c2);studioForm.append(r2);
  studioForm.append(lbl('Température (0–1, vide = défaut)'),temp,lbl('Outils (tous en lecture seule : cet agent ne peut rien modifier)'));
  const grid=el('div','tools-grid');const boxes=[];
  for(const t of metaCache.readonly_tools||[]){const l=document.createElement('label');const c=document.createElement('input');c.type='checkbox';c.value=t.name;c.checked=(d.tools||[]).includes(t.name);c.disabled=ro;boxes.push(c);const sp=document.createElement('span');sp.append(document.createTextNode(t.name),el('small','',t.description||''));l.append(c,sp);grid.append(l)}
  studioForm.append(grid,lbl('Prompt (instructions de l’agent)'),prompt);
  const save=btn('ENREGISTRER ↗','send',async()=>{const k=(key.value||'').trim();const payload={title:title.value.trim(),description:desc.value.trim(),model:model.value.trim(),tools:boxes.filter(b=>b.checked).map(b=>b.value),max_steps:parseInt(steps.value||'8',10),temperature:temp.value===''?null:parseFloat(temp.value),prompt:prompt.value};
    if(await put('/api/agents/'+encodeURIComponent(k),payload,msg)){sSel=k;flash='✓ Enregistré — l’agent est utilisable tout de suite (sélecteur du haut, ou le superviseur l’appelle de lui-même).';await refreshRoles();await loadStudioList(k)}});
  const bs=[];if(!ro)bs.push(save);
  if(it&&it.editable)bs.push(btn('SUPPRIMER','choice',async()=>{const j=await del('/api/agents/'+encodeURIComponent(it.key),msg,'Supprimer l’agent « '+it.title+' » ?');if(j){sSel=null;flash='Supprimé.';await refreshRoles();await loadStudioList()}}));
  actions(msg,bs);
}
async function init(){
  try{const r=await api('/api/meta');if(r.status===401){if(askToken())return init();setStatus(false,'<b>JETON REQUIS</b><br>Recharge la page pour le saisir');return}
    const meta=await r.json();applyMeta(meta);if(meta.setup_required&&!connectionSetupShown){connectionSetupShown=true;openStudio('connections')};supModel=(meta.role_models||{}).supervisor||(meta.models&&meta.models.superviseur?String(meta.models.superviseur):'');updateModelIndicator();
    const esc=s=>String(s).replace(/[&<>"']/g,ch=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]));
    setStatus(true,'<b>STUDIO / LOCAL</b><br>'+esc(meta.workdir)+'<br>'+(meta.sandbox?'bash isolé':'bash NON isolé')+(meta.models&&meta.models.superviseur?'<br>superviseur : '+esc(modelDisplay(supModel)):''));
    syncPending();refreshCtx();loadSkills()}catch{setStatus(false,'<b>HORS LIGNE</b><br>Serveur injoignable')}
}
$('chatForm').addEventListener('submit',e=>{e.preventDefault();submit()});
prompt.addEventListener('keydown',e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();submit()}});
prompt.addEventListener('input',()=>{prompt.style.height='auto';prompt.style.height=Math.min(prompt.scrollHeight,220)+'px'});
$('newConversation').onclick=()=>{if(!busy)start()};$('newTab').onclick=()=>openTab();$('menuToggle').onclick=toggleSidebar;
$('sidebarClose').onclick=()=>{if(sidebarMedia.matches)closeMenu();else toggleSidebar();$('menuToggle').focus()};
if(!current()||wantNew)start();else render();
init();
})();
