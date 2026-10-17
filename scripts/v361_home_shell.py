"""Minimal modern, responsive design for the existing EIS audit homepage.

No promotional hero, duplicate cabinet buttons, fake counters, DOM replacement
of forms, or changes to EIS/audit logic. Layout CSS is injected after sign-in.
"""
STYLE = r"""
<style id="eis361-home-style">
:root{
 --aud-ink:#172b34;--aud-muted:#657781;--aud-green:#087f73;
 --aud-green-dark:#04695e;--aud-accent-bg:#e8f7f3;
 --aud-line:#dee7e5;--aud-surface:#fff;--aud-back:#f5f7f6;
 --aud-radius:16px;--aud-shadow:0 8px 30px #172f2a0a;
}
body:has(> .eis223-workspace){background:var(--aud-back)!important;color:var(--aud-ink)!important;
 font-family:Inter,"Segoe UI",system-ui,-apple-system,sans-serif!important;letter-spacing:-.012em}
body:has(> .eis223-workspace)>header{background:#fff!important;box-shadow:none!important;
 border-bottom:1px solid var(--aud-line)!important;padding:14px clamp(16px,3vw,46px)!important;
 color:var(--aud-ink)!important}
body:has(> .eis223-workspace)>header h1{color:#193b36!important;
 font-size:clamp(16px,1.7vw,22px)!important;margin:0 0 4px!important;letter-spacing:-.025em}
body:has(> .eis223-workspace)>header a,body:has(> .eis223-workspace)>header nav a{
 color:var(--aud-green-dark)!important}
body:has(> .eis223-workspace)>main{box-sizing:border-box;max-width:1560px!important;
 width:100%!important;margin:0 auto 65px!important;
 padding:clamp(16px,2.5vw,34px) clamp(14px,3vw,45px)!important}
.eis361-heading{display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;
 gap:10px;margin:0 0 22px!important}
.eis361-heading h2{font-size:clamp(23px,2.5vw,31px);font-weight:800;letter-spacing:-.045em;
 color:#1a3431!important;margin:0 0 4px}
.eis361-heading p{color:var(--aud-muted);font-size:13px;margin:0}
.eis361-source{border:1px solid #d4e5df;color:#37776b;background:#eff8f4;
 padding:7px 12px;border-radius:999px;font-size:12px;font-weight:650}
body:has(> .eis223-workspace) .stats{gap:12px!important;margin-bottom:20px!important}
body:has(> .eis223-workspace) .stat{background:#fff!important;border:1px solid var(--aud-line)!important;
 border-radius:var(--aud-radius)!important;box-shadow:var(--aud-shadow)!important;
 padding:17px 20px!important;min-height:88px;position:relative}
body:has(> .eis223-workspace) .stat::before{content:"";display:block;height:3px;width:29px;
 border-radius:9px;background:#18a595;margin-bottom:11px}
body:has(> .eis223-workspace) .stat small{display:block;font-size:12px!important;
 font-weight:660!important;color:var(--aud-muted)!important;margin-bottom:6px}
body:has(> .eis223-workspace) .stat b{color:#1c4039!important;font-size:clamp(23px,2vw,31px)!important;
 font-weight:800!important;letter-spacing:-.045em!important;font-variant-numeric:tabular-nums}
body:has(> .eis223-workspace) :is(.card,.filters,.detail,details,#v302-search-panel,
 #v300-eis-status){background:var(--aud-surface)!important;border:1px solid var(--aud-line)!important;
 border-radius:var(--aud-radius)!important;box-shadow:var(--aud-shadow)!important}
body:has(> .eis223-workspace) :is(.card,.filters,.detail){margin-bottom:16px!important}
body:has(> .eis223-workspace) h2,body:has(> .eis223-workspace) h3{color:#1b463e!important;
 letter-spacing:-.025em}
body:has(> .eis223-workspace) :is(input,select,textarea){color:#203b36!important;
 border:1px solid #ccdad5!important;border-radius:10px!important;background:#fff!important;
 accent-color:var(--aud-green)}
body:has(> .eis223-workspace) :is(input,select,textarea):focus{outline:3px solid #c4ebe0!important;
 border-color:var(--aud-green)!important}
body:has(> .eis223-workspace) :is(button,input[type=submit],.btn){
 border-radius:10px!important;background:var(--aud-green)!important;
 border:1px solid var(--aud-green)!important;color:#fff!important;font-weight:680!important;
 box-shadow:none!important;transition:background .15s,transform .15s}
body:has(> .eis223-workspace) button:hover:not(:disabled){background:var(--aud-green-dark)!important}
body:has(> .eis223-workspace) button:disabled{opacity:.55}
body:has(> .eis223-workspace) :is(button,a,input,select,textarea):focus-visible{
 outline:3px solid #78ccba!important;outline-offset:2px}
body:has(> .eis223-workspace) a{color:#087769}
body:has(> .eis223-workspace) #v302-search-panel{overflow:hidden}
body:has(> .eis223-workspace) .v302-header{
 background:linear-gradient(120deg,#edf8f3,#f9fcfb)!important;border-color:#dcebe5!important}
body:has(> .eis223-workspace) .v302-emblem{background:linear-gradient(145deg,#0e9987,#0a6059)!important;
 border-radius:12px}
body:has(> .eis223-workspace) .v302-title strong{color:#1f4039!important}
body:has(> .eis223-workspace) .v302-box{
 background:#f9fcfa!important;border:1px solid #e1ece6!important;border-radius:13px!important}
body:has(> .eis223-workspace) .v302-links{background:#f9fcfa!important;border-color:#e4eeea!important}
body:has(> .eis223-workspace) .v302-connect.online{background:#e4f6ee!important;color:#087554!important}
body:has(> .eis223-workspace) #v257-tabs a{background:#f5faf8!important;
 border:1px solid #d8e7e1!important;color:#1c695a!important;border-radius:10px!important}
body:has(> .eis223-workspace) table{width:100%;border:1px solid var(--aud-line)!important;
 border-radius:14px!important;border-spacing:0!important;
 box-shadow:0 4px 16px #1b403707!important;font-variant-numeric:tabular-nums}
body:has(> .eis223-workspace) table th{background:#eef5f1!important;color:#48665e!important;
 font-weight:760!important;font-size:12px!important;padding:12px 13px!important;
 border-bottom:1px solid #d8e6df!important}
body:has(> .eis223-workspace) table td{color:#26423c!important;padding:12px 13px!important;
 border-bottom:1px solid #eaf0ed!important;line-height:1.5}
body:has(> .eis223-workspace) table tbody tr:hover td{background:#f7fbf9!important}
body:has(> .eis223-workspace) table td:first-child{font-weight:650}
@media(max-width:720px){
 body:has(> .eis223-workspace)>header{padding:11px 14px!important}
 body:has(> .eis223-workspace)>main{padding:16px 12px!important}
 .eis361-heading{margin-bottom:16px!important}
 .eis361-heading h2{font-size:23px}
 .eis361-source{font-size:11px}
 body:has(> .eis223-workspace) .stats{grid-template-columns:repeat(2,minmax(0,1fr))!important;
 gap:9px!important}
 body:has(> .eis223-workspace) .stat{padding:13px!important;min-height:81px}
 body:has(> .eis223-workspace) table{display:block;overflow-x:auto;-webkit-overflow-scrolling:touch;
 max-width:100%;border-radius:12px!important}
 body:has(> .eis223-workspace) table th,body:has(> .eis223-workspace) table td{
 padding:10px 11px!important}
}
@media(max-width:390px){body:has(> .eis223-workspace) .stats{
 grid-template-columns:minmax(0,1fr)!important}}
@media(prefers-reduced-motion:reduce){body:has(> .eis223-workspace) *{transition:none!important}}
</style>
"""

SCRIPT = r"""
<script id="eis361-home-script">
(function(){
 function prepare(){
  if(location.pathname!=="/"||document.getElementById("eis361-heading"))return;
  const main=document.querySelector("body > main")||document.querySelector("main");
  if(!main)return;
  // The previously added promotional banner is deliberately removed.
  const previous=document.getElementById("eis361-hero");
  if(previous)previous.remove();
  const heading=document.createElement("div");
  heading.id="eis361-heading";heading.className="eis361-heading";
  heading.innerHTML='<div><h2>Реестр закупок</h2><p>Поиск, документы и подтверждённые результаты проверок</p></div>'+
   '<span class="eis361-source">Источник данных · ЕИС</span>';
  main.insertBefore(heading,main.firstChild);
 }
 if(document.readyState==="loading")document.addEventListener("DOMContentLoaded",prepare,{once:true});
 else prepare();
})();
</script>
"""


def home_shell():
    return (STYLE + SCRIPT).encode("utf-8")
