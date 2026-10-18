"""Small, dependency-free presentation layer for the existing account screens.

These builders contain CSS only. They do not fetch data, change page structure,
hide features, or alter authentication, roles, navigation or audit decisions.
Keep them after the original screen stylesheet so the visual system is shared
without coupling it to the workbench and manager JavaScript.
"""


def workspace_style():
    """Shared homepage navigation and the expandable personal-account shell."""
    return r"""<style id="eis223-workspace-polish">
.eis223-workspace{--ui-ink:#243747;--ui-muted:#536b7c;--ui-line:#dce4eb;
 --ui-accent:#175d8d;-webkit-font-smoothing:antialiased}
.eis223-workspace .eis223-menu{background:#18384f;gap:10px;
 min-height:76px;padding-block:13px;border-bottom:3px solid #4d91bd}
.eis223-workspace .eis223-brand-icon{background:#ffffff0f;border-color:#ffffff40;
 border-radius:10px;width:42px;height:42px;letter-spacing:-.04em}
.eis223-workspace .eis223-brand-name{font-size:19px;letter-spacing:-.025em}
.eis223-workspace .eis223-brand-sub{font-size:11px;color:#c8dce9}
.eis223-workspace .eis223-auth-user{background:#ffffff08;border-color:#ffffff2c;
 padding:7px 12px;max-width:min(33vw,360px)}
.eis223-workspace .eis223-auth-user small{font-size:11px;color:#d0e0ea}
.eis223-workspace .eis223-menu :is(button,a){min-height:42px}
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet[aria-expanded=true],
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet[aria-expanded=true]:hover{
 background:#28678f!important;border-color:#96c4e1!important;color:#fff!important}
.eis223-workspace .eis223-menu a:hover{background:#ffffff14}
.eis223-workspace .eis223-personal{background:#f1f5f8;
 padding-inline:max(clamp(12px,3vw,44px),calc((100% - 1482px)/2));padding-block:27px 32px}
.eis223-workspace .eis223-cabinet-heading{margin-bottom:21px;gap:18px}
.eis223-workspace .eis223-personal h2{color:var(--ui-ink)!important;letter-spacing:-.025em}
.eis223-workspace .eis223-kicker{color:#496b84;letter-spacing:.11em;font-size:11px}
.eis223-workspace .eis223-subtle{color:var(--ui-muted)}
.eis223-workspace .eis223-panel-mark{color:#48677d;background:#eaf1f6}
.eis223-workspace :is(.eis223-tile,.eis223-feature){border-color:var(--ui-line);
 box-shadow:0 2px 5px #173c5505;border-radius:12px;padding:21px}
.eis223-workspace :is(.eis223-tile h3,.eis223-feature h3){
 color:var(--ui-ink)!important;font-size:16px!important;line-height:1.4;letter-spacing:-.015em}
.eis223-workspace :is(.eis223-tile-sub,.eis223-feature p,.eis223-check-desc small,
 .eis223-feature li small){color:var(--ui-muted);font-size:12px;line-height:1.6}
.eis223-workspace .eis223-personal .eis223-tile:hover{
 box-shadow:0 2px 5px #173c5505;border-color:#c6d6e2}
.eis223-workspace .eis223-overview{gap:12px;margin:18px 0}
.eis223-workspace .eis223-overview>span{padding:16px 18px;border-color:var(--ui-line);
 box-shadow:none;border-radius:10px}
.eis223-workspace .eis223-overview b{font-size:27px;letter-spacing:-.03em}
.eis223-workspace .eis223-overview small{font-size:12px;color:var(--ui-muted)}
.eis223-workspace .eis223-tile-head{gap:12px;margin-bottom:16px}
.eis223-workspace .eis223-tile-number{background:#f0f5f9;color:#3d6c8c;border-color:#dbe6ee}
.eis223-workspace :is(.eis223-tile ul,.eis223-feature ul){scrollbar-gutter:stable;
 overscroll-behavior:contain;padding-right:4px}
.eis223-workspace .eis223-tile li{padding-block:13px;gap:10px}
.eis223-workspace .eis223-tile li a{white-space:normal!important;overflow-wrap:anywhere}
.eis223-workspace .eis223-all-checks{min-height:40px;line-height:1.45}
.eis223-workspace .eis223-employee-links{gap:8px;margin-top:16px}
.eis223-workspace .eis223-employee-links a{min-height:42px;background:#f5f8fb;
 border-color:#d8e3ec;padding:10px 13px}
.eis223-workspace .eis223-employee-links a:first-child{
 background:#175d8d;border-color:#175d8d;color:#fff!important}
.eis223-workspace .eis223-employee-links a:first-child:hover{background:#124e78}
.eis223-workspace :is(input,select,textarea){min-width:0;max-width:100%;line-height:1.45}
.eis223-workspace :is(button,a,input,select,textarea,summary):focus-visible{
 outline:3px solid #247ab0!important;outline-offset:3px}
.eis223-workspace .eis223-menu :is(button,a):focus-visible{
 outline-color:#c5e7ff!important}
.eis223-workspace button:disabled{cursor:wait}
.eis223-workspace .eis223-profile-prompt form{border-radius:13px}
.eis223-workspace .eis223-profile-prompt p{color:var(--ui-muted)}
@media(max-width:1100px){.eis223-workspace .eis223-auth-user{max-width:45vw}}
@media(max-width:660px){
 .eis223-workspace .eis223-menu{padding:12px;gap:8px}
 .eis223-workspace .eis223-auth-user{max-width:none;width:100%}
 .eis223-workspace .eis223-personal{padding:20px 12px}
 .eis223-workspace :is(.eis223-tile,.eis223-feature){padding:16px}
 .eis223-workspace .eis223-overview{gap:8px}
 .eis223-workspace .eis223-overview>span{padding:13px 10px}
 .eis223-workspace .eis223-overview b{font-size:24px}
 .eis223-workspace .eis223-overview small{font-size:11px}
 .eis223-workspace .eis223-employee-links a{flex:1 1 190px;justify-content:center}
 .eis223-workspace .eis223-tile form button{flex:1 1 150px}
}
@media(pointer:coarse){.eis223-workspace :is(button,.eis223-employee-links a){min-height:44px}}
@media(prefers-reduced-motion:reduce){.eis223-workspace *{transition:none!important}}
</style>"""


def home_style():
    """Polish only the authenticated shared registry, including old table markup."""
    return r"""<style id="eis223-home-polish">
body:has(> .eis223-workspace){--eis-bg:#f3f6f9;--eis-text:#263b4d;
 --eis-muted:#536b7d;--eis-line:#dce5ed;--eis-shadow:0 2px 6px #163a5805;
 -webkit-font-smoothing:antialiased}
body:has(> .eis223-workspace)>main{max-width:1570px!important}
body:has(> .eis223-workspace)>header{padding-block:12px!important}
body:has(> .eis223-workspace) .eis361-heading{background:transparent;border:0;
 box-shadow:none;padding:8px 0 19px!important;margin-bottom:7px!important;gap:18px}
body:has(> .eis223-workspace) .eis361-heading h2{color:#233d50!important;
 font-size:clamp(23px,2.2vw,30px)!important;letter-spacing:-.025em!important}
body:has(> .eis223-workspace) .eis361-heading p{color:#536b7d;font-size:13px;margin-top:6px}
body:has(> .eis223-workspace) .eis361-heading-icon{background:#e5eef6;
 color:#245f88;border:1px solid #d3e2ed;box-shadow:none;width:46px;height:46px}
body:has(> .eis223-workspace) .eis361-source{background:#fff;color:#3c6684;
 border-color:#d8e3ec;font-size:12px;font-weight:650}
body:has(> .eis223-workspace) .eis361-not-official{color:#536b7d;font-size:12px}
body:has(> .eis223-workspace) .stats{gap:12px!important;margin-block:14px 20px!important}
body:has(> .eis223-workspace) .stat{padding:18px 20px!important;min-width:0}
body:has(> .eis223-workspace) .stat::before{background:#87adc7;width:2px}
body:has(> .eis223-workspace) .stat small{color:#536b7d!important;letter-spacing:0!important}
body:has(> .eis223-workspace) .stat b{color:#274b65!important}
body:has(> .eis223-workspace) .filters{gap:10px!important;padding:18px!important;align-items:end}
body:has(> .eis223-workspace) :is(input,select,textarea){max-width:100%;min-width:0}
body:has(> .eis223-workspace) input:is([type=checkbox],[type=radio]){
 min-height:0!important;width:17px!important;height:17px!important;padding:0!important;flex:none}
body:has(> .eis223-workspace) .choices{max-width:calc(100vw - 28px)}
body:has(> .eis223-workspace) summary{line-height:1.45}
body:has(> .eis223-workspace) summary:focus-visible{outline:3px solid #247ab0!important;outline-offset:3px}
body:has(> .eis223-workspace) .hero h1{font-family:inherit;font-size:clamp(22px,2.5vw,30px);
 line-height:1.25;letter-spacing:-.025em;color:#233d50}
body:has(> .eis223-workspace) table th{background:#edf3f7!important;
 color:#455f74!important;font-size:12px!important;padding:13px 15px!important}
body:has(> .eis223-workspace) table td{padding:14px 15px!important;line-height:1.6}
body:has(> .eis223-workspace) table td small{color:#536b7d}
body:has(> .eis223-workspace) table td:is(.bad,.violation,.risk-red,.status-violation){
 color:#a34043!important}
body:has(> .eis223-workspace) #v257-tabs{gap:8px!important;padding-bottom:13px;
 border-bottom:1px solid #dce5ed}
body:has(> .eis223-workspace) #v257-tabs a{background:#fff!important;
 padding:10px 14px!important;min-height:42px;line-height:1.5}
body:has(> .eis223-workspace) #v257-tabs a[aria-current=page]{
 background:#e6f0f7!important;border-color:#a8c8df!important;color:#174e75!important;
 box-shadow:inset 0 -2px #347ca9}
body:has(> .eis223-workspace) .v302-header{background:#f5f9fc!important;padding:20px 23px}
body:has(> .eis223-workspace) .v302-title strong{color:#263e50!important;font-size:18px}
body:has(> .eis223-workspace) .v302-title small{color:#536b7d}
body:has(> .eis223-workspace) .v302-box{background:#fff!important;padding:18px}
body:has(> .eis223-workspace) .v302-box p{color:#536b7d}
body:has(> .eis223-workspace) .v302-links{padding:14px 23px}
@media(max-width:620px){
 body:has(> .eis223-workspace) .eis361-heading{padding:7px 2px 14px!important}
 body:has(> .eis223-workspace) .eis361-heading p{font-size:12px}
 body:has(> .eis223-workspace) .eis361-right{padding-left:0;gap:8px}
 body:has(> .eis223-workspace) .stat{padding:14px!important}
 body:has(> .eis223-workspace) .filters{padding:14px!important}
 body:has(> .eis223-workspace) .filters>:is(input,select,details,button){flex:1 1 100%;width:100%}
 body:has(> .eis223-workspace) #v257-tabs a{flex:1 1 150px;text-align:center}
 body:has(> .eis223-workspace) table th,body:has(> .eis223-workspace) table td{padding:11px 12px!important}
}
</style>"""


def account_page_style():
    """Common standalone account, employee and manager presentation (CSS only)."""
    return r"""<style id="eis223-account-polish">
:root{--ui-ink:#263b4d;--ui-muted:#536b7d;--ui-edge:#dce5ed;
 --ui-accent:#175d8d;--muted:#536b7d;--line:#dce5ed;font-synthesis:none}
body{background:#f3f6f9;color:var(--ui-ink);-webkit-font-smoothing:antialiased}
body>header,.mgr-top{background:#18384f;border-bottom:3px solid #4d91bd;padding-block:18px}
body>header a,.mgr-top a{min-height:42px;display:inline-flex;align-items:center;line-height:1.4}
body>header a:hover,.mgr-top a:hover{background:#ffffff12}
body>header .action-row a{background:#ffffff08;border-color:#8aaabd;color:#fff}
body>header .action-row a:hover{background:#ffffff18}
main{min-width:0}
body h1{color:#263b4d;letter-spacing:-.025em;line-height:1.25}
body h2{color:#2c465a;letter-spacing:-.015em;line-height:1.4}
body p,.mgr-card p,.metric small,.mgr-kpi small,.mgr-kpi span,.mgr-muted,
.entry small,.staff-rowbody small,.mgr-footnote{color:var(--ui-muted)}
body :is(.panel,.mgr-card,.metric,.mgr-kpi){border-color:var(--ui-edge);
 box-shadow:0 2px 6px #163a5805;border-radius:12px}
body>.panel,body>main>.panel{padding:clamp(20px,3vw,34px)}
body>main>.panel>nav{gap:8px;padding:13px 0 18px;margin-bottom:22px}
body>main>.panel>nav a{background:#fff;border-color:#d8e4ed;padding:9px 12px;min-height:42px}
body>main>.panel>nav a:hover{background:#eef5fa}
body :is(button,input,select,textarea){line-height:1.45}
body :is(input,select,textarea){min-width:0;max-width:100%}
body input:is([type=checkbox],[type=radio]){width:17px;height:17px;min-height:0;padding:0;flex:none}
body :is(button,a,input,select,textarea,summary):focus-visible{
 outline:3px solid #247ab0;outline-offset:3px}
body>header a:focus-visible,.mgr-top a:focus-visible{outline-color:#c5e7ff}
body :is(button,a){touch-action:manipulation}
body :is(.manual-note,.notice,.mgr-banner){line-height:1.65;background:#edf4f9;color:#42627b}
body :is(.table-scroll,.mgr-table-wrap){overscroll-behavior-x:contain;scrollbar-gutter:stable}
body :is(table,.mgr-table){font-variant-numeric:tabular-nums}
body :is(th,.mgr-table th){background:#edf3f7;color:#455f74;line-height:1.5}
body :is(td,.mgr-table td){line-height:1.65}
body :is(.metric,.mgr-kpi){padding:19px}
body :is(.metric strong,.mgr-kpi strong){color:#274b65;letter-spacing:-.03em}
.staff-layout{gap:22px;margin-top:25px}
.staff-layout>.tabs{padding:9px;border-radius:12px;box-shadow:0 2px 6px #163a5805;gap:4px}
.staff-layout>.tabs button{min-height:44px;line-height:1.45;padding:12px;color:#47647a}
.staff-layout>.tabs button:hover{background:#f2f6f9}
.staff-layout>.tabs button[aria-selected=true]{background:#e7f0f7;color:#174f78;
 box-shadow:inset 3px 0 #347ca9}
.staff-subpanel{background:#fafcfe;border-color:#e0e7ee;padding:18px}
.staff-tools{gap:8px}.staff-tools :is(button,a){min-height:36px;line-height:1.4}
.staff-rowbody strong{overflow-wrap:anywhere}
.mgr-side{padding:22px 13px;gap:6px}
.mgr-tab{min-height:44px;line-height:1.45;color:#47647a}
.mgr-tab[aria-selected=true]{background:#e7f0f7;border-color:#cfdfeb;
 box-shadow:inset 3px 0 #347ca9;color:#174f78}
.mgr-card{padding:22px}.mgr-card h2{color:#2c465a;letter-spacing:-.015em}
.mgr-entry{padding:17px;border-color:#e0e7ee}.mgr-entry .mgr-evidence{line-height:1.7}
.mgr-side-heading{color:#536b7d;font-size:11px;letter-spacing:.08em}
@media(min-width:761px){.staff-hero+.grid{grid-template-columns:repeat(3,minmax(0,1fr));margin-top:18px}}
@media(min-width:981px){.staff-layout>.tabs{max-height:calc(100dvh - 28px);overflow-y:auto}}
@media(min-width:901px){.mgr-side{align-self:start;position:sticky;top:0;
 max-height:100dvh;overflow-y:auto}}
@media(max-width:720px){
 body>main>.panel{padding:18px 14px}
 body>main>.panel>nav{margin-bottom:18px;padding-bottom:14px}
 body>main>.panel>nav a{flex:1 1 150px;text-align:center;line-height:1.4}
 body :is(.metric,.mgr-kpi){padding:14px}
 .mgr-card{padding:16px}.staff-subpanel{padding:15px}
}
@media(pointer:coarse){body :is(button,.mgr-actions a,.staff-tools a){min-height:44px}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}body *{transition:none!important}}
@media(forced-colors:active){.mgr-tab[aria-selected=true],.staff-layout>.tabs button[aria-selected=true]{
 outline:2px solid Highlight;outline-offset:-3px}}
</style>"""
