"""Registry navigation and a read-only inspector over existing saved card HTML.

No API or authorization changes. All card links remain genuine navigation links
without JavaScript; modified clicks keep their browser behavior.
"""
from html import escape


def icon(name):
    paths={
        'menu':'<path d="M4 6h16M4 12h16M4 18h16"/>',
        'grid':'<rect x="4" y="4" width="6" height="6" rx="1"/><rect x="14" y="4" width="6" height="6" rx="1"/><rect x="4" y="14" width="6" height="6" rx="1"/><rect x="14" y="14" width="6" height="6" rx="1"/>',
        'list':'<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>',
        'check':'<path d="m5 12 4 4L19 6"/>',
        'layers':'<path d="m12 3 10 5-10 5L2 8l10-5Zm-9 10 9 5 9-5M3 18l9 5 9-5"/>',
        'file':'<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9l-6-6Z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>',
        'star':'<path d="m12 3 2.8 5.7 6.2.9-4.5 4.4 1.1 6.2-5.6-3-5.6 3 1.1-6.2L3 9.6l6.2-.9L12 3Z"/>',
        'people':'<circle cx="9" cy="8" r="3"/><path d="M3 21v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6M21 21v-3a6 6 0 0 0-4-5"/>',
        'profile':'<circle cx="12" cy="8" r="4"/><path d="M4 21v-2a8 8 0 0 1 16 0v2"/>',
        'chevron':'<path d="m14 6-6 6 6 6"/>',
        'close':'<path d="m6 6 12 12M18 6 6 18"/>',
    }
    return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'+paths.get(name,paths['list'])+'</svg>'


STYLE=r'''<style id="eis-workspace-panels-style">
.eis-shell-nav[hidden],.eis-quicklook[hidden],#eis-shell-shade[hidden]{display:none!important}
body.eis-shell-ready{--shell-nav:220px;--shell-bar:64px}
body.eis-shell-ready.eis-shell-collapsed{--shell-nav:68px}
body.eis-shell-ready .eis223-workspace{padding-top:var(--shell-bar)}
body.eis-shell-ready .eis223-workspace>.eis223-menu{position:fixed;inset:0 0 auto;z-index:40;
 box-sizing:border-box;width:100%;min-height:60px;background:#fffffffa;border-bottom:1px solid var(--ui-line,#dedee3)}
body.eis-shell-ready:has(>.eis223-workspace)>main{width:calc(100% - var(--shell-nav))!important;
 max-width:none!important;margin-left:var(--shell-nav)!important;padding:24px clamp(16px,2.6vw,36px) 48px!important}
body.eis-shell-ready .eis223-workspace .eis223-personal{width:calc(100% - var(--shell-nav));
 margin-left:var(--shell-nav);padding-inline:clamp(16px,2.6vw,36px)}
body.eis-shell-ready>main[hidden]{display:none!important}
body.eis-shell-ready[data-eis-view=cabinet] .eis223-workspace .eis223-personal{min-height:calc(100dvh - var(--shell-bar));box-sizing:border-box}
body.eis-shell-ready .eis223-cabinet-extra{scroll-margin-top:calc(var(--shell-bar) + 20px)}
.eis-shell-nav{position:fixed;top:var(--shell-bar,64px);left:0;bottom:0;z-index:30;
 width:var(--shell-nav,220px);background:#f9f9fb;border-right:1px solid #dedee3;
 padding:20px 12px;overflow:auto;box-sizing:border-box;font:13px/1.45 var(--ui-font,system-ui);color:#1d1d1f;display:flex;flex-direction:column}
.eis-shell-nav>nav{display:flex;flex-direction:column;flex:1 0 auto}
.eis-shell-nav>button{flex-shrink:0}
.eis-shell-group{margin:0 0 20px;flex-shrink:0}.eis-shell-group>p{margin:0 10px 9px;color:#636366;
 font-size:10px;letter-spacing:.07em;font-weight:600;text-transform:uppercase}
.eis-shell-nav :is(a,button){display:flex;align-items:center;gap:10px;width:100%;min-height:40px;
 box-sizing:border-box;padding:9px 10px;margin:3px 0;border:1px solid transparent;border-radius:8px;
 text-decoration:none;background:transparent;color:#515154;font:500 13px/1.4 var(--ui-font,system-ui);text-align:left;cursor:pointer}
.eis-shell-nav :is(a,button):hover{background:#ededf1;text-decoration:none}
.eis-shell-nav a[aria-current=page]{background:#eaf2ff;color:#0055ad}
.eis-shell-nav svg{width:18px;height:18px;flex:none}
/* The restored registry styles all buttons with !important. Scope these overrides to the new controls. */
body.eis-shell-ready :is(.eis-shell-nav,.eis-quicklook) button{background:#f5f5f7!important;
 color:#515154!important;border-color:#dedee3!important;box-shadow:none!important;font-weight:500!important}
body.eis-shell-ready .eis-shell-nav button{background:transparent!important}
body.eis-shell-ready .eis-shell-nav button{border-color:transparent!important}
body.eis-shell-ready .eis-shell-nav a{color:#515154}
body.eis-shell-ready .eis-shell-nav a[aria-current=page]{color:#0055ad}
body.eis-shell-ready .eis-shell-nav button:hover{background:#ededf1!important}
body.eis-shell-ready .eis-shell-nav button[aria-current=page],
body.eis-shell-ready .eis-shell-nav button[aria-current=page]:hover{background:#eaf2ff!important;color:#0055ad!important}
body.eis-shell-ready .eis-quicklook-tabs button[aria-selected=true]{background:#eaf2ff!important;color:#0055ad!important}
body.eis-shell-ready .eis-quicklook-heading button{background:#fff!important}
body.eis-shell-ready .eis-quicklook-error button{background:#0066cc!important;color:#fff!important}
.eis-shell-nav .eis-shell-nav-footer{border-top:1px solid #dedee3;padding-top:10px;margin-top:auto;flex-shrink:0}
.eis-shell-ready .eis223-menu button#eis-shell-nav-toggle{width:34px;padding:7px!important;min-width:34px;
 background:transparent!important;border:1px solid #dedee3!important;color:#515154!important;flex:none}
#eis-shell-nav-toggle svg{display:block;width:18px;height:18px}
#eis-shell-nav-close{display:none}
.eis-shell-collapsed .eis-shell-nav .eis-shell-label,.eis-shell-collapsed .eis-shell-group>p{display:none}
.eis-shell-collapsed .eis-shell-nav{padding-inline:9px}
.eis-shell-collapsed .eis-shell-nav :is(a,button){justify-content:center;padding-inline:8px}
body.eis-shell-ready>main .filters{position:sticky;top:calc(var(--shell-bar) + 8px);z-index:15;background:#fff!important}
.eis-filter-shortcuts{display:flex;flex-wrap:wrap;gap:6px;flex-basis:100%;grid-column:1/-1;margin:0 0 4px}
body.eis-shell-ready .eis-filter-shortcuts button{min-height:30px!important;border:1px solid #dedee3!important;
 background:#fff!important;color:#636366!important;border-radius:7px!important;padding:5px 9px!important;font-size:12px!important}
body.eis-shell-ready .eis-filter-shortcuts button[aria-pressed=true]{background:#eaf2ff!important;color:#0055ad!important;border-color:#b9d3f4!important}
.eis-copy-id{display:inline-flex;align-items:center;justify-content:center;width:25px;height:25px;
 min-height:25px!important;padding:3px!important;margin-left:5px;vertical-align:middle;border:0!important;border-radius:5px!important;
 background:transparent!important;color:#636366!important;cursor:pointer}
.eis-copy-id:hover{background:#ededf1!important;color:#0055ad!important}.eis-copy-id svg{width:14px;height:14px}
body.eis-shell-ready>main table button.eis-copy-id{background:transparent!important;color:#636366!important;
 border:0!important;min-height:25px!important;padding:3px!important}
body.eis-shell-ready table tr.eis-row-selected td{background:#eaf2ff!important}
body.eis-shell-ready table tbody tr:has(a[href^="/check-new/"]){cursor:pointer}
body.eis-shell-ready table .eis-money{white-space:nowrap;text-align:right!important;font-variant-numeric:tabular-nums}
#eis-shell-toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%);z-index:100;
 max-width:calc(100vw - 32px);background:#1d1d1f;color:white;border-radius:10px;font:13px/1.5 var(--ui-font,system-ui);padding:10px 16px}
#eis-shell-toast:empty{display:none}
.eis-quicklook{position:fixed;inset:var(--shell-bar,64px) 0 0 auto;width:min(460px,46vw);z-index:70;
 background:#fff;border-left:1px solid #dedee3;box-shadow:-8px 0 28px #1d1d1f0c;
 display:flex;flex-direction:column;box-sizing:border-box;color:#1d1d1f;font:13px/1.55 var(--ui-font,system-ui)}
.eis-quicklook-heading{padding:20px 20px 14px;display:flex;align-items:start;gap:12px;justify-content:space-between}
.eis-quicklook-heading p{margin:0 0 4px;color:#636366;font-size:11px}.eis-quicklook h2{font-size:18px;line-height:1.35;letter-spacing:-.025em;margin:0;font-weight:650}
.eis-quicklook :is(button,a){font-family:inherit;touch-action:manipulation}
.eis-quicklook-heading button{width:32px;height:32px;flex:none;display:grid;place-items:center;
 padding:7px;border:1px solid #dedee3;border-radius:8px;background:#fff;color:#636366;cursor:pointer}
.eis-quicklook-heading svg{width:16px;height:16px}
.eis-quicklook-tabs{display:flex;padding:0 20px 12px;gap:3px;border-bottom:1px solid #ebebef}
.eis-quicklook-tabs button{flex:1;min-height:36px;border:0;border-radius:7px;background:#f5f5f7;
 color:#636366;cursor:pointer;font-size:12px;font-weight:500;padding:7px 8px}
.eis-quicklook-tabs button[aria-selected=true]{background:#eaf2ff;color:#0055ad}
#eis-quicklook-content{padding:18px 20px;overflow:auto;flex:1;min-height:0;overscroll-behavior:contain}
#eis-quicklook-content:focus-visible{outline-offset:-4px}
.eis-quicklook .panel{border:0;box-shadow:none;padding:0;margin:0 0 20px;background:#fff}
.eis-quicklook :is(h2,h3,h4){color:#1d1d1f}.eis-quicklook h3{font-size:14px;font-weight:600;margin:12px 0 8px}
.eis-quicklook p{overflow-wrap:anywhere;margin:8px 0}.eis-quicklook .meta{color:#636366;font-size:12px}
.eis-quicklook a{color:#0066cc;text-decoration:none}.eis-quicklook a:hover{text-decoration:underline}
.eis-quicklook details{border-bottom:1px solid #ebebef;margin-bottom:12px;padding:0 0 10px}
.eis-quicklook summary{cursor:pointer;line-height:1.5;padding:8px 0;color:#1d1d1f}
.eis-quicklook summary h2{display:inline;font-size:16px}.eis-quicklook .result-count{color:#636366;margin-left:8px}
.eis-quicklook .grid{display:grid;grid-template-columns:1fr;gap:3px}
.eis-quicklook .card-summary-grid{display:grid;grid-template-columns:1fr 1fr;gap:12px;border-top:1px solid #ebebef;padding-top:12px;margin-top:12px}
body.eis-shell-ready .eis-quicklook h2{font-size:18px;line-height:1.35}
.eis-quicklook .card-summary h2{font-size:19px!important;line-height:1.35}
.eis-quicklook dt,.eis-quicklook .card-state-label{font-size:11px;color:#636366}
.eis-quicklook .card-state-label{display:block;margin-bottom:5px}
.eis-quicklook .card-state-grid strong{display:block;font-weight:600}
.eis-quicklook dd{margin:4px 0 0;font-weight:600;overflow-wrap:anywhere}
.eis-quicklook .card-state-grid{display:grid;gap:10px;padding-top:12px;margin-top:12px;border-top:1px solid #ebebef}
.eis-quicklook .card-result-strip{display:flex;gap:14px;flex-wrap:wrap;padding:12px 0;border-top:1px solid #ebebef;margin-top:12px}
.eis-quicklook .card-result-strip b{font-size:18px;margin-right:5px}
.eis-quicklook .card-result-caption{font-size:11px;color:#636366}
.eis-quicklook :is(.legal,.review){border:1px solid #dedee3;border-left:3px solid #b42332;border-radius:9px;padding:10px 12px;margin:12px 0;background:#fffbfb}
.eis-quicklook .review{border-left-color:#865b00;background:#fffdf7}
.eis-quicklook :is(.evidence,.source-fragment){white-space:pre-wrap;overflow-wrap:anywhere}
.eis-quicklook table,.eis-quicklook tbody,.eis-quicklook tr,.eis-quicklook td{display:block;width:100%;box-sizing:border-box}
.eis-quicklook table{border:0}.eis-quicklook thead{display:none}.eis-quicklook tr{padding:12px;border:1px solid #dedee3;border-radius:9px;margin-bottom:10px}
.eis-quicklook td{padding:3px 0;min-width:0;border:0;overflow-wrap:anywhere;font-size:12px}
.eis-quicklook td[data-label]::before{content:attr(data-label) ': ';color:#636366}
.eis-quicklook-footer{padding:14px 20px;border-top:1px solid #ebebef;flex:none;background:#fff}
#eis-quicklook-full{display:flex;justify-content:center;align-items:center;min-height:38px;border-radius:8px;
 background:#0066cc;color:#fff;font-weight:500;text-decoration:none;padding:8px 12px;box-sizing:border-box}
#eis-quicklook-full:hover{background:#0055ad}
.eis-quicklook-error{background:#fff6ee;border:1px solid #eadabc;border-radius:9px;padding:14px;color:#694c16}
#eis-shell-shade{position:fixed;inset:0;background:#1d1d1f55;z-index:80}
:is(.eis-shell-nav,.eis-quicklook) :is(a,button,summary):focus-visible{outline:3px solid #0066cc;outline-offset:2px}
@media(max-width:1099px){body.eis-shell-ready,body.eis-shell-ready.eis-shell-collapsed{--shell-nav:0px}
 .eis-shell-nav{top:0;z-index:90;width:min(290px,85vw);padding:16px;box-shadow:8px 0 30px #1d1d1f12}
 .eis-shell-nav:not(.eis-nav-open){display:none}
 #eis-shell-nav-close{display:flex;justify-content:center;width:44px;margin:0 0 16px auto}
 .eis-shell-collapsed .eis-shell-nav .eis-shell-label,.eis-shell-collapsed .eis-shell-group>p{display:block}
 .eis-shell-collapsed .eis-shell-nav :is(a,button){justify-content:flex-start}
 body.eis-shell-ready:has(>.eis223-workspace)>main{padding:18px 14px 36px!important}}
@media(max-width:999px){.eis-quicklook{inset:0;width:100%;z-index:95;border:0;height:100dvh}
 body.eis-shell-ready>main .filters{position:static!important}
 .eis-quicklook-heading{padding-top:max(16px,env(safe-area-inset-top))}
 .eis-quicklook-footer{padding-bottom:max(14px,env(safe-area-inset-bottom))}}
@media(pointer:coarse){.eis-shell-nav :is(a,button),.eis-quicklook :is(button,#eis-quicklook-full),
 .eis-shell-ready .eis223-menu button#eis-shell-nav-toggle{min-height:44px!important}
 body.eis-shell-ready .eis-filter-shortcuts button{min-height:44px!important}
 body.eis-shell-ready>main table button.eis-copy-id{min-height:44px!important}
 .eis-copy-id{min-width:44px;min-height:44px!important}}
@media(prefers-reduced-motion:reduce){.eis-shell-nav,.eis-quicklook{transition:none}}
@media(forced-colors:active){.eis-shell-nav a[aria-current=page],.eis-quicklook-tabs button[aria-selected=true]{outline:2px solid Highlight}}
</style>'''

SCRIPT=r'''<script id="eis-workspace-panels-script">
(()=>{
'use strict';
const nav=document.getElementById('eis-shell-nav'),pane=document.getElementById('eis-quicklook');
if(!nav||!pane||nav.dataset.ready)return;nav.dataset.ready='1';
const by=id=>document.getElementById(id),body=document.body,menu=document.querySelector('.eis223-menu');
if(!menu)return;
const mobileNav=matchMedia('(max-width:1099px)'),mobilePane=matchMedia('(max-width:999px)');
const shade=by('eis-shell-shade'),content=by('eis-quicklook-content'),title=by('eis-quicklook-title'),full=by('eis-quicklook-full');
const make=(tag,text)=>{const e=document.createElement(tag);if(text!==undefined)e.textContent=text;return e};
const toggle=make('button');toggle.id='eis-shell-nav-toggle';toggle.type='button';
const ns='http://www.w3.org/2000/svg',toggleSvg=document.createElementNS(ns,'svg'),togglePath=document.createElementNS(ns,'path');
for(const [key,value] of Object.entries({viewBox:'0 0 24 24',fill:'none',stroke:'currentColor','stroke-width':'1.6','stroke-linecap':'round','aria-hidden':'true'}))toggleSvg.setAttribute(key,value);
togglePath.setAttribute('d','M4 6h16M4 12h16M4 18h16');toggleSvg.append(togglePath);toggle.append(toggleSvg);toggle.setAttribute('aria-controls','eis-shell-nav');
menu.prepend(toggle);body.classList.add('eis-shell-ready');nav.hidden=false;
try{body.classList.toggle('eis-shell-collapsed',localStorage.getItem('eis223.nav.collapsed.v1')==='1')}catch{}
let navOpen=false,lastNavFocus=null,lastCardFocus=null,controller=null,request=0,card=null,activeTab='summary',cardUrl='',selectedRow=null;
let inertBefore=new Map(),overflowBefore=null,toastTimer;
let currentView='registry',viewSync=false,viewFrame=0;
const viewScroll={registry:0,cabinet:0};
function toast(message){clearTimeout(toastTimer);by('eis-shell-toast').textContent=message;toastTimer=setTimeout(()=>{by('eis-shell-toast').textContent=''},3500)}
function restoreBackground(){for(const [node,value] of inertBefore)node.inert=value;inertBefore.clear();if(overflowBefore!==null){body.style.overflow=overflowBefore;overflowBefore=null}}
function syncModal(){
 restoreBackground();const paneModal=!pane.hidden&&mobilePane.matches,navModal=navOpen&&mobileNav.matches;
 shade.hidden=!(paneModal||navModal);pane.setAttribute('aria-modal',String(paneModal));
 if(paneModal||navModal){
  const nodes=[...document.querySelectorAll('body>main,body>header,.eis223-workspace'),paneModal?nav:pane];
  for(const node of nodes){inertBefore.set(node,node.inert);node.inert=true}
  overflowBefore=body.style.overflow;body.style.overflow='hidden';
 }
 nav.classList.toggle('eis-nav-open',navModal);
 nav.setAttribute('role',navModal?'dialog':'complementary');
 if(navModal)nav.setAttribute('aria-modal','true');else nav.removeAttribute('aria-modal');
 toggle.setAttribute('aria-expanded',String(mobileNav.matches?navOpen:!body.classList.contains('eis-shell-collapsed')));
 toggle.setAttribute('aria-label',mobileNav.matches?'Открыть меню':body.classList.contains('eis-shell-collapsed')?'Развернуть меню':'Свернуть меню');
}
function closeNav(returnFocus=true){navOpen=false;syncModal();if(returnFocus&&lastNavFocus?.isConnected)lastNavFocus.focus({preventScroll:true})}
toggle.addEventListener('click',()=>{
 if(mobileNav.matches){lastNavFocus=toggle;navOpen=!navOpen;syncModal();if(navOpen)by('eis-shell-nav-close').focus()}
 else{body.classList.toggle('eis-shell-collapsed');try{localStorage.setItem('eis223.nav.collapsed.v1',body.classList.contains('eis-shell-collapsed')?'1':'0')}catch{}syncModal()}
});
by('eis-shell-nav-close').addEventListener('click',()=>closeNav());
function viewFromUrl(){try{return new URL(document.baseURI).hash==='#cabinet'?'cabinet':'registry'}catch{return 'registry'}}
function showView(view,{focus=true,historyMode='push',top=false}={}){
 const main=document.querySelector('body>main'),cabinet=by('eis223-cabinet'),originalToggle=by('eis223-toggle-cabinet');
 if(!main||!cabinet)return;
 const next=view==='cabinet'?'cabinet':'registry',changed=next!==currentView;
 if(changed)viewScroll[currentView]=window.scrollY;
 if(!pane.hidden)closePane(false);
 viewSync=true;
 if(next==='cabinet'&&cabinet.hidden)originalToggle?.click();
 cabinet.hidden=next!=='cabinet';main.hidden=next!=='registry';
 originalToggle?.setAttribute('aria-expanded',String(!cabinet.hidden));
 viewSync=false;currentView=next;body.dataset.eisView=next;
 const registry=by('eis-shell-registry'),overview=by('eis-shell-overview');
 if(next==='registry'){registry?.setAttribute('aria-current','page');overview.removeAttribute('aria-current')}
 else{registry?.removeAttribute('aria-current');overview.setAttribute('aria-current','page')}
 overview.setAttribute('aria-pressed',String(next==='cabinet'));
 if(historyMode!=='none')try{
  const url=new URL(document.baseURI),hash=next==='cabinet'?'#cabinet':'';
  if(url.hash!==hash){url.hash=hash;history[historyMode==='replace'?'replaceState':'pushState'](null,'',url.pathname+url.search+url.hash)}
 }catch{}
 cancelAnimationFrame(viewFrame);viewFrame=requestAnimationFrame(()=>{
  window.scrollTo({top:top?0:viewScroll[next],behavior:'instant'});
  if(focus){const heading=next==='cabinet'?by('eis223-cabinet-title'):document.querySelector('#eis361-heading h2');
   const visible=node=>{if(!node||!node.getClientRects().length)return false;const r=node.getBoundingClientRect();return r.top>=menu.getBoundingClientRect().bottom&&r.bottom<=innerHeight};
   if(visible(heading)){heading.tabIndex=-1;heading.focus({preventScroll:true})}
   else{const selected=next==='cabinet'?overview:registry;(visible(selected)?selected:toggle).focus({preventScroll:true})}}
 });
 document.dispatchEvent(new CustomEvent('eis:viewchange',{detail:{view:next}}));
}
function showCabinet(favorites=false){
 closeNav(false);showView('cabinet',{top:!favorites});
 if(favorites){const target=by('eis223-favorite-list')?.closest('details');if(target){
  target.open=true;requestAnimationFrame(()=>{target.scrollIntoView({block:'start',behavior:'instant'});target.querySelector('summary')?.focus({preventScroll:true})})}}
}
by('eis-shell-overview').addEventListener('click',()=>showCabinet());by('eis-shell-favorites').addEventListener('click',()=>showCabinet(true));
by('eis-shell-registry')?.addEventListener('click',event=>{
 if(event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey)return;
 event.preventDefault();closeNav(false);showView('registry');
});
by('eis223-toggle-cabinet')?.addEventListener('click',()=>{
 if(!viewSync)showView(by('eis223-cabinet').hidden?'registry':'cabinet',{top:true});
});
function restoreView(){showView(viewFromUrl(),{focus:true,historyMode:'none'})}
window.addEventListener('popstate',restoreView);window.addEventListener('hashchange',restoreView);
function updateBar(){body.style.setProperty('--shell-bar',Math.ceil(menu.getBoundingClientRect().height)+'px')}
if(window.ResizeObserver)new ResizeObserver(updateBar).observe(menu);updateBar();syncModal();
function viewportChanged(){if(!mobileNav.matches)navOpen=false;syncModal();if(!pane.hidden&&mobilePane.matches&&!pane.contains(document.activeElement))by('eis-quicklook-close').focus();if(!mobileNav.matches&&document.activeElement===by('eis-shell-nav-close'))toggle.focus()}
mobileNav.addEventListener('change',viewportChanged);mobilePane.addEventListener('change',viewportChanged);
const base=new URL(document.baseURI);
function cardLink(href){try{const u=new URL(href,base);return u.origin===base.origin&&!u.search&&!u.hash&&/^\/check-new\/3[0-9]{10}\/?$/.test(u.pathname)?u:null}catch{return null}}
function closePane(returnFocus=true){request++;controller?.abort();pane.hidden=true;card=null;content.replaceChildren();pane.removeAttribute('aria-busy');
 if(selectedRow)selectedRow.classList.remove('eis-row-selected');selectedRow=null;syncModal();
 if(returnFocus&&lastCardFocus?.isConnected)lastCardFocus.focus({preventScroll:true});
}
by('eis-quicklook-close').addEventListener('click',()=>closePane());shade.addEventListener('click',()=>!pane.hidden?closePane():closeNav());
const allowed=new Set('section article div p span strong b em small ul ol li h2 h3 h4 details summary table thead tbody tr td th a br blockquote time dl dt dd'.split(' '));
function clean(node){
 if(node.nodeType===Node.TEXT_NODE)return document.createTextNode(node.textContent);
 if(node.nodeType!==Node.ELEMENT_NODE||!allowed.has(node.localName))return null;
 const out=make(node.localName);
 if(node.className)out.className=String(node.className).split(/\s+/).filter(x=>/^[a-zA-Z][\w-]*$/.test(x)).join(' ');
 if(node.localName==='a'){
  try{const url=new URL(node.getAttribute('href')||'',cardUrl);
   if(['https:','http:'].includes(url.protocol)&&!url.username&&!url.password){out.href=url.href;if(url.origin!==base.origin){out.target='_blank';out.rel='noopener noreferrer'}}
  }catch{}
 }
 if(node.hasAttribute('data-label'))out.setAttribute('data-label',node.getAttribute('data-label'));
 if(['table','rowgroup','row','cell','columnheader'].includes(node.getAttribute('role')))out.setAttribute('role',node.getAttribute('role'));
 if(node.localName==='table'&&node.hasAttribute('aria-label'))out.setAttribute('aria-label',node.getAttribute('aria-label'));
 if(node.localName==='details'&&node.hasAttribute('open'))out.open=true;
 if(['td','th'].includes(node.localName)&&/^[1-9][0-9]?$/.test(node.getAttribute('colspan')||''))out.colSpan=Number(node.getAttribute('colspan'));
 for(const child of node.childNodes){const next=clean(child);if(next)out.append(next)}return out;
}
function renderTab(tab){
 activeTab=tab;const tabs=pane.querySelectorAll('[data-quicklook-tab]');for(const button of tabs){const active=button.dataset.quicklookTab===tab;button.setAttribute('aria-selected',String(active));button.tabIndex=active?0:-1}
 content.setAttribute('aria-labelledby','eis-quicklook-tab-'+tab);if(!card)return;
 content.replaceChildren();const selectors={summary:'[data-eis-section="summary"],[data-eis-section="overview"],[data-eis-section="notices"]',findings:'[data-eis-section="notices"],[data-eis-section="findings"],[data-eis-section="review"]',documents:'[data-eis-section="documents"]'};
 const sections=card.querySelectorAll(selectors[tab]);
 for(const section of sections){const copy=clean(section);if(copy)content.append(copy)}
 if(!sections.length)content.append(make('p','В сохранённой карточке нет данных этого раздела.'));
 content.scrollTop=0;
}
for(const button of pane.querySelectorAll('[data-quicklook-tab]')){
 button.addEventListener('click',()=>renderTab(button.dataset.quicklookTab));
 button.addEventListener('keydown',event=>{const tabs=[...pane.querySelectorAll('[data-quicklook-tab]')],index=tabs.indexOf(button);let next;
  if(event.key==='ArrowRight')next=(index+1)%tabs.length;if(event.key==='ArrowLeft')next=(index+tabs.length-1)%tabs.length;
  if(event.key==='Home')next=0;if(event.key==='End')next=tabs.length-1;
  if(next!==undefined){event.preventDefault();tabs[next].focus();renderTab(tabs[next].dataset.quicklookTab)}
 });
}
function showError(message,login=false){card=null;content.replaceChildren();const note=make('div');note.className='eis-quicklook-error';note.setAttribute('role','status');note.append(make('p',message));
 if(login){const link=make('a','Перейти ко входу');link.href=new URL('/account/login',base).href;note.append(link)}
 else{const retry=make('button','Повторить');retry.type='button';retry.addEventListener('click',()=>loadCard(cardUrl));note.append(retry)}content.append(note)
}
async function loadCard(url){
 const id=++request;controller?.abort();controller=new AbortController();card=null;cardUrl=url;renderTab('summary');
 content.replaceChildren(make('p','Загружаем сохранённую карточку…'));pane.setAttribute('aria-busy','true');
 try{
  const response=await fetch(url,{credentials:'same-origin',cache:'no-store',signal:controller.signal});
  if(id!==request)return;
  if(response.status===401||response.status===403||(response.redirected&&new URL(response.url,base).pathname==='/account/login')){showError('Сессия завершена или доступ к карточке ограничен. Войдите снова.',true);return}
  if(!response.ok)throw Error('Не удалось открыть сохранённую карточку.');
  const html=await response.text();if(id!==request)return;if(html.length>2000000)throw Error('Карточка слишком велика для быстрого просмотра. Откройте полную карточку.');
  const parsed=new DOMParser().parseFromString(html,'text/html'),reg=new URL(url,base).pathname.split('/').filter(Boolean).pop();
  if(!parsed.querySelector('[data-eis-reg="'+reg+'"]'))throw Error('Сохранённая карточка недоступна для быстрого просмотра. Откройте полную карточку.');
  card=parsed;renderTab(activeTab);
 }catch(error){if(id===request&&error.name!=='AbortError')showError(error.message||'Ошибка соединения. Попробуйте ещё раз.')}
 finally{if(id===request)pane.removeAttribute('aria-busy')}
}
function openCard(url,trigger,row){
 closeNav(false);lastCardFocus=trigger;const reg=url.pathname.split('/').filter(Boolean).pop();
 if(selectedRow)selectedRow.classList.remove('eis-row-selected');selectedRow=row||null;selectedRow?.classList.add('eis-row-selected');
 title.textContent='Закупка № '+reg;full.href=url.href;pane.hidden=false;syncModal();by('eis-quicklook-close').focus({preventScroll:true});loadCard(url.href);
}
document.addEventListener('click',event=>{
 if(event.defaultPrevented||event.button!==0||event.ctrlKey||event.metaKey||event.shiftKey||event.altKey||pane.contains(event.target)||nav.contains(event.target))return;
 const anchor=event.target.closest('a'),row=event.target.closest('body>main table tbody tr');
 if(anchor){if(anchor.target==='_blank'||anchor.hasAttribute('download'))return;const url=cardLink(anchor.href);if(url){event.preventDefault();openCard(url,anchor,row)}return}
 if(!row||event.target.closest('button,input,select,textarea,summary')||String(window.getSelection()||'').trim())return;
 const link=[...row.querySelectorAll('a[href]')].find(a=>cardLink(a.href));if(link){event.preventDefault();openCard(cardLink(link.href),link,row)}
});
document.addEventListener('keydown',event=>{
 if(event.key==='Escape'){if(!pane.hidden){event.preventDefault();closePane()}else if(navOpen){event.preventDefault();closeNav()}return}
 if(event.key!=='Tab')return;const modal=!pane.hidden&&mobilePane.matches?pane:navOpen&&mobileNav.matches?nav:null;if(!modal)return;
 const focusable=[...modal.querySelectorAll('a[href],button:not(:disabled),summary,[tabindex="0"]')].filter(e=>e.getClientRects().length&&!e.closest('[hidden]'));
 if(!focusable.length){event.preventDefault();return}const first=focusable[0],last=focusable[focusable.length-1];
 if(event.shiftKey&&(document.activeElement===first||!modal.contains(document.activeElement))){event.preventDefault();last.focus()}
 else if(!event.shiftKey&&(document.activeElement===last||!modal.contains(document.activeElement))){event.preventDefault();first.focus()}
});
const copyIcon='<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 4H6a2 2 0 0 0-2 2v10"/></svg>';
function prepareFilters(){
 const filters=document.querySelector('body>main .filters');if(!filters||filters.dataset.eisShortcuts)return;
 const apply=[...filters.querySelectorAll('button,input[type=submit]')].find(e=>/^(Применить|Найти|Показать|Поиск)$/i.test((e.textContent||e.value||'').trim()));
 if(!apply)return;
 const select=[...filters.querySelectorAll('select')].find(e=>[...e.options].filter(o=>/наруш|провер|документ|вниман/i.test(o.text)).length>=2);
 if(!select)return;
 filters.dataset.eisShortcuts='1';const group=make('div');group.className='eis-filter-shortcuts';group.setAttribute('role','group');group.setAttribute('aria-label','Быстрые фильтры');
 const choices=[...select.options].filter(o=>!o.disabled&&(/наруш|провер|документ|вниман|все/i.test(o.text))).slice(0,5);
 for(const option of choices){const button=make('button',option.text);button.type='button';button.dataset.eisFilterValue=option.value;
  button.addEventListener('click',()=>{select.value=option.value;select.dispatchEvent(new Event('change',{bubbles:true}));apply.click()});group.append(button)}
 const sync=()=>{for(const b of group.querySelectorAll('button'))b.setAttribute('aria-pressed',String(b.dataset.eisFilterValue===select.value))};
 select.addEventListener('change',sync);filters.prepend(group);sync();
}
function decorate(){
 prepareFilters();
 for(const row of document.querySelectorAll('body>main table tbody tr')){
  for(const anchor of row.querySelectorAll('a[href]')){if(cardLink(anchor.href))anchor.title='Быстрый просмотр закупки. Ctrl или ⌘ + клик — полная карточка.'}
  for(const cell of row.querySelectorAll('td')){
   if(/(?:₽|руб\.?)/i.test(cell.textContent)&&!cell.querySelector('a,button')&&cell.textContent.trim().length<45)cell.classList.add('eis-money');
   const match=cell.textContent.match(/(?:ИНН\s*[:№]?\s*|№\s*)([0-9]{10,12})(?![0-9])/);if(!match||cell.querySelector('.eis-copy-id'))continue;
   const button=make('button');button.className='eis-copy-id';button.type='button';button.setAttribute('aria-label','Скопировать '+match[1]);button.title='Скопировать '+match[1];
   const svg=new DOMParser().parseFromString(copyIcon,'image/svg+xml').documentElement;button.append(document.importNode(svg,true));
   button.addEventListener('click',async event=>{event.stopPropagation();try{if(!navigator.clipboard)throw Error();await navigator.clipboard.writeText(match[1]);toast('Номер скопирован')}catch{toast('Не удалось скопировать номер. Выделите его и скопируйте вручную.')}});cell.append(button);
  }
 }
}
function observeRows(){decorate();const main=document.querySelector('body>main');if(main)new MutationObserver(decorate).observe(main,{childList:true,subtree:true});showView(viewFromUrl(),{focus:false,historyMode:'none'})}
if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',observeRows,{once:true});else observeRows();
})();
</script>'''


def workspace_panels(role='user'):
    def link(url,label,symbol,current=False,identifier=None):
        return '<a '+('id="'+escape(identifier,quote=True)+'" ' if identifier else '')+'href="'+escape(url,quote=True)+'" title="'+escape(label,quote=True)+'" aria-label="'+escape(label,quote=True)+'"'+(' aria-current="page"' if current else '')+'>'+icon(symbol)+'<span class="eis-shell-label">'+escape(label)+'</span></a>'
    manager=link('/account/manager','Кабинет руководителя','people') if role in ('manager','admin') else ''
    admin=link('/account/admin','Заявки на регистрацию','people') if role=='admin' else ''
    nav='''<aside id="eis-shell-nav" class="eis-shell-nav" hidden aria-label="Главные разделы">
<button id="eis-shell-nav-close" type="button" aria-label="Закрыть меню">'''+icon('close')+'''</button>
<nav aria-label="Основные разделы"><div class="eis-shell-group" data-eis-group="primary"><p>Основное</p>'''+link('/','Реестр закупок','list',True,'eis-shell-registry')+'''
<button type="button" id="eis-shell-overview" title="Мой кабинет" aria-label="Мой кабинет" aria-controls="eis223-cabinet" aria-pressed="false">'''+icon('grid')+'''<span class="eis-shell-label">Мой кабинет</span></button>
'''+link('/account/workbench','Задачи и поручения','check')+link('/account/home','Мои проверки','list')+link('/account/thematic','Тематические проверки','layers')+link('/account/memos','Служебные записки','file')+'''
<button type="button" id="eis-shell-favorites" title="Избранное" aria-label="Избранное">'''+icon('star')+'''<span class="eis-shell-label">Избранное</span></button></div>'''+('<div class="eis-shell-group"><p>Управление</p>'+manager+admin+'</div>' if manager or admin else '')+'''
<div class="eis-shell-nav-footer">'''+link('/account/profile','Профиль','profile')+'</div></nav></aside>'
    panel='''<div id="eis-shell-shade" hidden></div>
<aside id="eis-quicklook" class="eis-quicklook" role="dialog" aria-labelledby="eis-quicklook-title" aria-modal="false" hidden>
<header class="eis-quicklook-heading"><div><p>Быстрый просмотр</p><h2 id="eis-quicklook-title">Закупка</h2></div>
<button id="eis-quicklook-close" type="button" aria-label="Закрыть быстрый просмотр">'''+icon('close')+'''</button></header>
<div class="eis-quicklook-tabs" role="tablist" aria-label="Разделы закупки">'''+''.join('<button id="eis-quicklook-tab-'+key+'" type="button" role="tab" data-quicklook-tab="'+key+'" aria-selected="'+('true' if key=='summary' else 'false')+'" aria-controls="eis-quicklook-content" tabindex="'+('0' if key=='summary' else '-1')+'">'+label+'</button>' for key,label in [('summary','Сводка'),('findings','Замечания'),('documents','Документы')])+'''</div>
<div id="eis-quicklook-content" role="tabpanel" aria-labelledby="eis-quicklook-tab-summary" tabindex="0"></div>
<footer class="eis-quicklook-footer"><a id="eis-quicklook-full">Открыть полную карточку</a></footer></aside>
<div id="eis-shell-toast" role="status" aria-live="polite"></div>'''
    return STYLE+nav+panel+SCRIPT
