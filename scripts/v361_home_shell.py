"""Responsive application shell for the existing working EIS homepage.

Progressive enhancement ONLY: existing forms, filters, callbacks and the
server-rendered procurement table are left intact. No fabricated metrics.
"""
STYLE = r"""
<style id="eis361-home-style">
body:has(> .eis223-workspace){background:#f3f6fa!important;color:#253b52!important}
body:has(> .eis223-workspace)>header{
 background:#fff!important;color:#213f5c!important;box-shadow:none!important;
 padding:15px clamp(17px,3.6vw,55px)!important;border-bottom:1px solid #e0e8f2!important}
body:has(> .eis223-workspace)>header h1{
 color:#203e5c!important;font-size:clamp(17px,2.1vw,23px)!important;
 line-height:1.4;margin:0 0 5px!important}
body:has(> .eis223-workspace)>header nav a{color:#2b669b!important}
body:has(> .eis223-workspace)>main{width:min(1600px,100%)!important;
 max-width:1600px!important;margin:0 auto 68px!important;padding:18px clamp(14px,3.3vw,52px)!important}
.eis361-hero{position:relative;isolation:isolate;overflow:hidden;display:grid;
 grid-template-columns:minmax(0,1.65fr) minmax(255px,.8fr);gap:24px;
 min-height:270px;padding:clamp(24px,4vw,44px);margin:7px 0 25px;
 border-radius:22px;background:linear-gradient(112deg,#122e4e 0%,#1b4e7f 68%,#2678ab 100%);
 color:#fff;box-shadow:0 16px 45px #163e642a}
.eis361-hero:before{content:"";position:absolute;z-index:-1;right:-9%;top:-58%;
 width:55%;aspect-ratio:1;border:1px solid #b1d9ff30;border-radius:50%;
 box-shadow:0 0 0 58px #c6e5ff0a,0 0 0 126px #c6e5ff09}
.eis361-copy{min-width:0;align-self:center}
.eis361-kicker{display:inline-flex;align-items:center;gap:7px;
 padding:6px 10px;border-radius:100px;background:#b5d9f321;border:1px solid #b2d6ef42;
 font-weight:780;letter-spacing:.09em;font-size:10px;color:#d4eeff}
.eis361-copy h1{margin:13px 0 12px;font-size:clamp(26px,3vw,40px);
 line-height:1.14;letter-spacing:-.045em;font-weight:810;color:#fff!important;max-width:670px}
.eis361-copy p{margin:0 0 22px;color:#d4e5f5;font-size:clamp(13px,1.4vw,15px);
 line-height:1.65;max-width:610px}
.eis361-actions{display:flex;gap:9px;flex-wrap:wrap}
.eis361-actions a{min-height:43px;display:inline-flex;align-items:center;justify-content:center;
 gap:7px;padding:11px 16px;border-radius:10px;background:#fff;color:#174a74!important;
 font-size:13px;font-weight:750;text-decoration:none;border:1px solid #fff;
 transition:transform .14s,background .14s}
.eis361-actions a:hover{background:#edf7ff;transform:translateY(-1px)}
.eis361-actions a.eis361-secondary{background:#ffffff12;color:#fff!important;
 border:1px solid #a6d0ee6b}
.eis361-actions a.eis361-secondary:hover{background:#ffffff25}
.eis361-aside{align-self:center;justify-self:end;width:min(100%,305px);
 background:#ffffff13;border:1px solid #ffffff33;border-radius:18px;padding:20px;
 backdrop-filter:blur(7px)}
.eis361-aside strong{display:block;font-size:15px;color:white;font-weight:750;margin-bottom:13px}
.eis361-aside ul{list-style:none;margin:0;padding:0;display:grid;gap:12px}
.eis361-aside li{display:flex;align-items:start;gap:10px;font-size:12px;line-height:1.5;
 color:#dceaf9}
.eis361-aside li span{width:20px;height:20px;display:grid;place-items:center;
 color:#d0edff;background:#bce2ff26;border-radius:7px;flex:none;font-weight:750}
.eis361-overview-head{display:flex;align-items:end;justify-content:space-between;
 gap:12px;flex-wrap:wrap;margin:16px 0 17px}
.eis361-overview-head h2{margin:0 0 4px;color:#183f62!important;
 font-weight:800;font-size:clamp(20px,2.2vw,27px);letter-spacing:-.025em}
.eis361-overview-head p{margin:0;color:#647991;font-size:13px}
.eis361-source-note{font-size:12px;color:#49708d;background:#e8f2fa;
 border:1px solid #d3e4f2;border-radius:100px;padding:7px 12px;white-space:nowrap}
body:has(> .eis223-workspace) .stats{gap:14px!important;margin-bottom:19px!important}
body:has(> .eis223-workspace) .stat{position:relative;overflow:hidden;min-height:94px;
 background:linear-gradient(160deg,#fff,#f8fbff)!important;border:1px solid #dce5ef!important;
 border-radius:15px!important;padding:17px 20px!important;
 box-shadow:0 5px 18px #1239590b!important}
body:has(> .eis223-workspace) .stat small{font-size:12px!important;
 font-weight:650;color:#72849a!important;display:block;margin-bottom:8px}
body:has(> .eis223-workspace) .stat b{font-size:clamp(22px,2.4vw,32px)!important;
 font-weight:800!important;letter-spacing:-.04em!important;color:#193e60!important}
body:has(> .eis223-workspace) :is(.card,.filters,.detail,details,#v302-search-panel){
 background:#fff!important;border-radius:17px!important;
 border:1px solid #dce6f0!important;box-shadow:0 5px 24px #1a42640b!important}
body:has(> .eis223-workspace) :is(.filters,.card){margin-bottom:15px!important}
body:has(> .eis223-workspace) :is(input,select,textarea){accent-color:#2976b2}
body:has(> .eis223-workspace) :is(input,select,textarea):focus-visible{
 border-color:#498fc4!important;outline:3px solid #bfddf6!important;outline-offset:1px}
body:has(> .eis223-workspace) button:hover:not(:disabled){filter:brightness(1.04)}
body:has(> .eis223-workspace) table{width:100%;border-radius:14px!important;
 border:1px solid #dce7f0!important;overflow:hidden;box-shadow:0 5px 25px #213e5709;
 font-variant-numeric:tabular-nums}
body:has(> .eis223-workspace) table th{font-size:12px!important;
 letter-spacing:.015em;background:#f0f5fb!important;color:#45627f!important;
 padding:13px 11px!important;font-weight:760!important}
body:has(> .eis223-workspace) table td{padding:12px 11px!important;
 border-bottom:1px solid #ecf0f5!important;line-height:1.5}
body:has(> .eis223-workspace) table td:first-child{font-weight:650}
body:has(> .eis223-workspace) .v302-box{border-radius:13px!important}
body:has(> .eis223-workspace) :is(button,a,input,select,textarea):focus-visible{
 outline:3px solid #98c8ef!important;outline-offset:2px}
@media(max-width:980px){
 .eis361-hero{grid-template-columns:minmax(0,1fr);min-height:0}
 .eis361-aside{display:none}
}
@media(max-width:720px){
 body:has(> .eis223-workspace)>header{padding:12px 15px!important}
 body:has(> .eis223-workspace)>main{padding:12px!important}
 .eis361-hero{padding:23px 18px;border-radius:17px;margin:4px 0 20px}
 .eis361-copy h1{font-size:clamp(25px,7vw,32px)}
 .eis361-actions{gap:8px}
 .eis361-actions a{width:100%;justify-content:center}
 .eis361-overview-head{margin:15px 0 13px}
 .eis361-source-note{white-space:normal}
 body:has(> .eis223-workspace) .stats{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:9px!important}
 body:has(> .eis223-workspace) .stat{padding:14px!important;min-height:87px}
 body:has(> .eis223-workspace) table{display:block;overflow-x:auto;
 -webkit-overflow-scrolling:touch;border-radius:12px!important}
 body:has(> .eis223-workspace) table th,body:has(> .eis223-workspace) table td{
 padding:11px 10px!important}
}
@media(max-width:370px){
 body:has(> .eis223-workspace) .stats{grid-template-columns:1fr!important}
}
@media(prefers-reduced-motion:reduce){.eis361-actions a{transition:none}}
</style>
"""

SCRIPT = r"""
<script id="eis361-home-script">
(function(){
  function render(){
    if(location.pathname!=='/'||document.getElementById('eis361-hero'))return;
    const main=document.querySelector('body > main')||document.querySelector('main');
    if(!main)return;
    const hero=document.createElement('section');
    hero.id='eis361-hero';hero.className='eis361-hero';
    hero.setAttribute('aria-label','Главная панель аудитора');
    hero.innerHTML=[
      '<div class="eis361-copy">',
      '<span class="eis361-kicker">АНАЛИТИКА ЕИС · 223-ФЗ</span>',
      '<h1>Закупки под контролем.<br>Нарушения — по документам.</h1>',
      '<p>Поиск закупок, анализ оригиналов и подтверждённые нарушения — в едином рабочем пространстве.</p>',
      '<nav class="eis361-actions" aria-label="Основные действия">',
      '<a href="/thematic">Новая тематическая проверка →</a>',
      '<a class="eis361-secondary" href="/account/thematic">Мои тематические проверки</a>',
      '<a class="eis361-secondary" href="/account/home">Мои закупки</a>',
      '</nav></div>',
      '<aside class="eis361-aside"><strong>Как формируются результаты</strong>',
      '<ul><li><span>1</span>Получаем доступные оригиналы из ЕИС</li>',
      '<li><span>2</span>Сопоставляем документы и сведения о закупке</li>',
      '<li><span>3</span>Фиксируем нарушения с правовым основанием</li></ul></aside>'
    ].join('');
    main.insertBefore(hero,main.firstChild);
    const head=document.createElement('div');head.className='eis361-overview-head';
    head.innerHTML='<div><h2>Общий реестр закупок</h2>'+
      '<p>Данные и результаты проверок, сохранённые в системе</p></div>'+
      '<span class="eis361-source-note">Источник: ЕИС закупки</span>';
    const stats=main.querySelector('.stats');
    if(stats&&stats.parentElement===main)main.insertBefore(head,stats);
    else hero.insertAdjacentElement('afterend',head);
  }
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',render,{once:true});
  else render();
})();
</script>
"""


def home_shell():
    return (STYLE + SCRIPT).encode("utf-8")
