"""EIS-inspired responsive main page for the independent 223-FZ auditor.

Styles and a compact, semantic page heading only. The official EIS website
is a visual reference, not an implied institutional affiliation.
All real counters, filters, document links and legal checks remain untouched.
"""
STYLE = r"""
<style id="eis361-home-style">
:root{
 --eis-blue:#165989;--eis-deep:#123e62;--eis-navy:#173d5d;
 --eis-text:#253a4d;--eis-muted:#677c8d;--eis-line:#dce5ed;
 --eis-pale:#f1f6fa;--eis-panel:#fff;--eis-bg:#f4f7fa;
 --eis-success:#267353;--eis-warning:#a4671c;--eis-danger:#a34043;
 --eis-shadow:0 3px 13px rgba(22,58,88,.055);
}
html{scroll-behavior:smooth}
body:has(> .eis223-workspace){
 background:var(--eis-bg)!important;color:var(--eis-text)!important;
 font-family:Inter,"Segoe UI",system-ui,-apple-system,Arial,sans-serif!important;
 font-size:14px!important;letter-spacing:0!important;line-height:1.5}
body:has(> .eis223-workspace)>header{
 background:#fff!important;color:var(--eis-text)!important;
 border-bottom:1px solid var(--eis-line)!important;
 box-shadow:none!important;padding:13px clamp(14px,3vw,45px)!important}
body:has(> .eis223-workspace)>header h1{
 font-size:clamp(16px,1.6vw,22px)!important;letter-spacing:-.025em!important;
 color:#184a73!important;font-weight:750!important;margin:0 0 5px!important}
body:has(> .eis223-workspace)>header a,
body:has(> .eis223-workspace)>header nav a{
 color:var(--eis-blue)!important;font-weight:650!important}
body:has(> .eis223-workspace)>main{
 width:100%!important;max-width:1570px!important;
 padding:clamp(14px,2.3vw,28px) clamp(12px,3vw,44px) 65px!important;
 margin:0 auto!important;box-sizing:border-box}
.eis361-heading{
 display:flex;gap:15px;justify-content:space-between;align-items:center;
 flex-wrap:wrap;margin:0 0 18px!important;
 padding:16px 21px!important;border:1px solid var(--eis-line);
 background:#fff;border-radius:12px;box-shadow:var(--eis-shadow)}
.eis361-heading-text{display:flex;gap:13px;align-items:center;min-width:0}
.eis361-heading-icon{width:43px;height:43px;flex:none;display:grid;place-items:center;
 color:#fff;background:linear-gradient(155deg,#2479b3,#164c7c);border-radius:10px;
 box-shadow:0 3px 7px #1748731c}
.eis361-heading-icon svg{width:23px;height:23px}
.eis361-heading h2{font-size:clamp(19px,2.2vw,25px)!important;
 line-height:1.2;color:#1b4568!important;font-weight:780!important;
 letter-spacing:-.025em!important;margin:0 0 4px!important}
.eis361-heading p{margin:0;color:#6a7c8a;font-size:12px;line-height:1.5}
.eis361-right{display:flex;gap:9px;align-items:center;flex-wrap:wrap}
.eis361-source{
 border:1px solid #d1e2ef;border-radius:8px;background:#f1f7fc;
 color:#2b668e;font-size:11px;font-weight:720;padding:8px 11px;white-space:nowrap}
.eis361-not-official{color:#738593;font-size:11px;white-space:nowrap}
body:has(> .eis223-workspace) .stats{
 display:grid!important;grid-template-columns:repeat(auto-fit,minmax(175px,1fr));
 gap:11px!important;margin:0 0 17px!important}
body:has(> .eis223-workspace) .stat{
 background:#fff!important;min-height:93px!important;position:relative;
 padding:17px 19px 14px!important;border-radius:11px!important;
 border:1px solid var(--eis-line)!important;box-shadow:var(--eis-shadow)!important;
 overflow:hidden}
body:has(> .eis223-workspace) .stat::before{
 content:"";position:absolute;top:0;left:0;width:3px;height:100%;
 background:#5c9ac6}
body:has(> .eis223-workspace) .stat small{
 display:block;color:var(--eis-muted)!important;font-size:12px!important;
 font-weight:640!important;margin:0 0 7px!important}
body:has(> .eis223-workspace) .stat b{
 font-size:clamp(23px,2vw,32px)!important;color:#214e73!important;
 letter-spacing:-.025em!important;line-height:1.13;font-weight:780!important;
 font-variant-numeric:tabular-nums}
body:has(> .eis223-workspace) :is(.card,.filters,.detail,details,#v302-search-panel,#v300-eis-status){
 border:1px solid var(--eis-line)!important;border-radius:11px!important;
 background:#fff!important;box-shadow:var(--eis-shadow)!important;
 color:var(--eis-text)!important}
body:has(> .eis223-workspace) :is(.card,.filters,.detail){margin-bottom:14px!important}
body:has(> .eis223-workspace) :is(h2,h3,h4){
 color:#1c4b71!important;letter-spacing:-.02em}
body:has(> .eis223-workspace) h2{font-size:clamp(18px,2vw,23px)}
body:has(> .eis223-workspace) .filters{padding:17px!important}
body:has(> .eis223-workspace) .filters label{
 font-size:12px;color:#536c80!important;font-weight:650}
body:has(> .eis223-workspace) :is(input,select,textarea){
 background:#fff!important;border:1px solid #cddbe6!important;color:#284459!important;
 border-radius:8px!important;min-height:41px!important;
 font-size:13px!important;accent-color:var(--eis-blue)}
body:has(> .eis223-workspace) :is(input,select,textarea):focus{
 border-color:#4986b8!important;outline:3px solid #d1e8f7!important;outline-offset:1px}
body:has(> .eis223-workspace) :is(button,input[type=submit],.btn){
 min-height:40px!important;border-radius:8px!important;
 background:var(--eis-blue)!important;border:1px solid var(--eis-blue)!important;
 color:#fff!important;font-size:13px!important;font-weight:680!important;
 box-shadow:none!important;transition:background .15s,border-color .15s}
body:has(> .eis223-workspace) button:hover:not(:disabled){
 background:#104b79!important;border-color:#104b79!important}
body:has(> .eis223-workspace) button:disabled{opacity:.55}
body:has(> .eis223-workspace) :is(a,button,input,textarea,select):focus-visible{
 outline:3px solid #8dbfdf!important;outline-offset:2px}
body:has(> .eis223-workspace) a{color:var(--eis-blue)}
body:has(> .eis223-workspace) table{
 width:100%!important;font-size:13px!important;border-collapse:separate!important;
 border-spacing:0!important;border:1px solid var(--eis-line)!important;
 border-radius:10px!important;overflow:hidden;box-shadow:var(--eis-shadow)!important;
 background:#fff!important;font-variant-numeric:tabular-nums}
body:has(> .eis223-workspace) table th{
 background:#eaf2f9!important;color:#315b7c!important;font-weight:750!important;
 border-bottom:1px solid #d3e0ec!important;border-top:0!important;
 padding:12px 13px!important;line-height:1.45;text-align:left!important}
body:has(> .eis223-workspace) table td{
 background:#fff!important;color:#2c4254!important;
 padding:11px 13px!important;border-top:0!important;
 border-bottom:1px solid #e9eff4!important;line-height:1.5;vertical-align:top}
body:has(> .eis223-workspace) table tr:last-child td{border-bottom:0!important}
body:has(> .eis223-workspace) table tbody tr:nth-child(even) td{background:#fbfcfe!important}
body:has(> .eis223-workspace) table tbody tr:hover td{background:#edf6fc!important}
body:has(> .eis223-workspace) table td:first-child{font-weight:650}
body:has(> .eis223-workspace) .v302-header{
 background:linear-gradient(105deg,#eaf3fa,#f8fbfe)!important;
 border-bottom:1px solid #d5e4ee!important}
body:has(> .eis223-workspace) .v302-emblem{
 background:#1d5b8a!important;border-radius:9px!important}
body:has(> .eis223-workspace) .v302-title strong{color:#194e77!important}
body:has(> .eis223-workspace) .v302-box{
 background:#f8fbfe!important;border-color:#dbe6ee!important;border-radius:9px!important}
body:has(> .eis223-workspace) .v302-links{
 background:#fafcff!important;border-color:#e3edf4!important}
body:has(> .eis223-workspace) #v257-tabs a{
 background:#f4f8fc!important;border:1px solid #d2e1ed!important;
 border-radius:8px!important;color:#215b88!important}
body:has(> .eis223-workspace) #v257-tabs a:hover{background:#e7f2fa!important}
body:has(> .eis223-workspace) :is(.violation,.risk-red,.status-violation){
 color:var(--eis-danger)}
body:has(> .eis223-workspace) :is(.ok,.status-ok){color:var(--eis-success)}
@media(max-width:880px){
 .eis361-heading{align-items:flex-start;padding:15px!important}
 .eis361-right{width:100%;padding-left:55px}
 body:has(> .eis223-workspace) .stats{grid-template-columns:repeat(2,minmax(0,1fr))!important}
}
@media(max-width:620px){
 body:has(> .eis223-workspace)>header{padding:10px 13px!important}
 body:has(> .eis223-workspace)>main{padding:12px 10px 35px!important}
 .eis361-heading{margin:0 0 14px!important;gap:10px;padding:14px 12px!important}
 .eis361-heading-text{gap:10px}
 .eis361-heading-icon{width:37px;height:37px}
 .eis361-heading-icon svg{width:19px;height:19px}
 .eis361-heading h2{font-size:19px!important}
 .eis361-heading p{font-size:11px}
 .eis361-right{padding-left:0}
 .eis361-not-official{white-space:normal}
 .eis361-source{font-size:10px}
 body:has(> .eis223-workspace) .stats{gap:8px!important}
 body:has(> .eis223-workspace) .stat{min-height:81px!important;padding:12px 13px!important}
 body:has(> .eis223-workspace) .stat b{font-size:24px!important}
 body:has(> .eis223-workspace) :is(.card,.filters,.detail){padding:13px!important}
 body:has(> .eis223-workspace) table{display:block!important;max-width:100%;overflow-x:auto!important;
 -webkit-overflow-scrolling:touch;border-radius:9px!important}
 body:has(> .eis223-workspace) table th,body:has(> .eis223-workspace) table td{
 padding:10px!important;min-width:90px}
}
@media(max-width:350px){
 body:has(> .eis223-workspace) .stats{grid-template-columns:1fr!important}
}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}
 body:has(> .eis223-workspace) *{transition:none!important}}
</style>
"""

SCRIPT = r"""
<script id="eis361-home-script">
(function(){
 function prepare(){
  if(location.pathname!=="/"||document.getElementById("eis361-heading"))return;
  const main=document.querySelector("body > main")||document.querySelector("main");
  if(!main)return;
  const old=document.getElementById("eis361-hero");
  if(old)old.remove();
  const heading=document.createElement("section");
  heading.className="eis361-heading";heading.id="eis361-heading";
  heading.setAttribute("aria-label","Общий реестр закупок");
  heading.innerHTML='<div class="eis361-heading-text">'+
   '<span class="eis361-heading-icon" aria-hidden="true">'+
   '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round">'+
   '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M9 8h6M9 12h6M9 16h4"/></svg></span>'+
   '<div><h2>Реестр закупок по 223-ФЗ</h2>'+
   '<p>Проверка документов и подтверждённые нарушения</p></div></div>'+
   '<div class="eis361-right"><span class="eis361-source">Источник: ЕИС</span>'+
   '<span class="eis361-not-official">Независимый аналитический сервис</span></div>';
  main.insertBefore(heading,main.firstChild);
 }
 if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",prepare,{once:true});
 else prepare();
})();
</script>
"""


def home_shell():
    return (STYLE + SCRIPT).encode("utf-8")
