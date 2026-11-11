"""Local, CSS-only presentation for the existing procurement audit screens.

No network assets, data requests, hidden features or changes to authentication.
The final theme is applied after each page's structural/compact layout styles.
"""

_TOKENS = r"""
:root{color-scheme:light;--ui-bg:#f5f5f7;--ui-surface:#fff;--ui-ink:#1d1d1f;
 --ui-muted:#636366;--ui-line:#dedee3;--ui-accent:#0066cc;--ui-hover:#0055ad;
 --ui-selected:#eaf2ff;--ui-danger:#b42332;--ui-warning:#865b00;--ui-success:#216e45;
 --ui-font:-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
 --ui-shadow:0 1px 3px #1d1d1f05;--ui-radius:12px;font-synthesis:none}
"""


def _style(identifier, rules):
    return '<style id="' + identifier + '">' + _TOKENS + rules + '</style>'


_INPUTS = r"""
:is(input,select,textarea){min-width:0;max-width:100%;color:var(--ui-ink);
 background:var(--ui-surface);border-color:#c8c8cf;border-radius:8px;line-height:1.45;
 font-family:var(--ui-font);accent-color:var(--ui-accent)}
:is(input,select){min-height:36px}
input:is([type=checkbox],[type=radio]){width:17px;height:17px;min-height:0;padding:0;flex:none}
:is(button,input,select,textarea,a,summary):focus-visible{
 outline:3px solid #0066cc;outline-offset:3px}
:is(button,a){touch-action:manipulation}
button:disabled{cursor:wait;opacity:.55}
/* Touch targets intentionally override the older compact desktop rules. */
@media(pointer:coarse){body :is(button,a.action,nav a,header a,.staff-tools a,
 input:not([type=checkbox]):not([type=radio]),select,summary){min-height:44px!important}}
@media(prefers-reduced-motion:reduce){html{scroll-behavior:auto}body *{transition:none!important}}
@media(forced-colors:active){:is([aria-selected=true],[aria-current=page]){outline:2px solid Highlight;outline-offset:-3px}}
"""


def workspace_style():
    """Global account toolbar and its existing expandable personal cabinet."""
    return _style('eis223-workspace-polish', r"""
.eis223-workspace{color:var(--ui-ink);font-family:var(--ui-font);-webkit-font-smoothing:antialiased;
 --ui-edge:var(--ui-line);border-color:var(--ui-line);box-shadow:none}
.eis223-workspace .eis223-menu{background:#fff;min-height:60px;padding-block:9px;gap:8px;
 border-bottom:1px solid var(--ui-line)}
.eis223-workspace .eis223-brand{color:var(--ui-ink)!important;gap:9px}
.eis223-workspace .eis223-brand-icon{background:var(--ui-selected);color:var(--ui-accent);
 border-color:#d4e3f7;border-radius:9px;width:34px;height:34px;font-size:12px;letter-spacing:-.04em}
.eis223-workspace .eis223-brand-name{font-size:17px;letter-spacing:-.035em;font-weight:650}
.eis223-workspace .eis223-auth-user{background:#f5f5f7;border-color:var(--ui-line);
 color:var(--ui-ink)!important;padding:5px 10px;max-width:min(33vw,360px)}
.eis223-workspace .eis223-auth-user strong{color:var(--ui-ink);font-weight:600}
.eis223-workspace .eis223-auth-user small{color:var(--ui-muted);font-size:11px}
.eis223-workspace .eis223-menu :is(button,a){min-height:36px;font:500 13px var(--ui-font);
 border-radius:8px!important;padding:7px 11px!important}
.eis223-workspace .eis223-menu a{color:var(--ui-ink)!important;border-color:var(--ui-line);background:#fff}
.eis223-workspace .eis223-menu a:hover{background:#f5f5f7}
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet{
 background:#f5f5f7!important;border-color:var(--ui-line)!important;color:var(--ui-ink)!important}
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet[aria-expanded=true],
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet[aria-expanded=true]:hover{
 background:var(--ui-selected)!important;border-color:#b9d3f4!important;color:var(--ui-hover)!important}
.eis223-workspace .eis223-menu button#eis223-toggle-cabinet:hover{background:#ebebef!important}
.eis223-workspace .eis223-menu button#eis223-logout{background:#fff!important;
 border-color:var(--ui-line)!important;color:var(--ui-muted)!important;box-shadow:none!important}
.eis223-workspace .eis223-menu button#eis223-logout:hover{background:#f5f5f7!important}
.eis223-workspace #eis223-logout-status:not(:empty){color:var(--ui-danger)}
.eis223-workspace .eis223-personal{background:var(--ui-bg);padding-block:18px 22px;
 padding-inline:max(clamp(12px,3vw,44px),calc((100% - 1482px)/2));border-color:var(--ui-line);box-shadow:none}
.eis223-workspace .eis223-personal h2{color:var(--ui-ink)!important;font-size:23px!important;
 letter-spacing:-.035em;font-weight:650}
.eis223-workspace .eis223-kicker{color:var(--ui-muted);letter-spacing:.07em;font-size:10px}
.eis223-workspace :is(.eis223-subtle,.eis223-tile-sub,.eis223-tile li small,
 .eis223-check-desc small,.eis223-feature p,.eis223-feature li small){color:var(--ui-muted);line-height:1.5}
.eis223-workspace :is(.eis223-tile,.eis223-feature){background:#fff;border-color:var(--ui-line);
 border-radius:12px;box-shadow:var(--ui-shadow);padding:15px}
.eis223-workspace :is(.eis223-tile h3,.eis223-feature h3){color:var(--ui-ink)!important;
 font-size:15px!important;font-weight:600;letter-spacing:-.015em}
.eis223-workspace .eis223-tile:hover{border-color:#c8c8cf;box-shadow:var(--ui-shadow)}
.eis223-workspace .eis223-tile-head{border-color:#ebebef}
.eis223-workspace .eis223-tile-number{background:#f5f5f7;border-color:var(--ui-line);color:var(--ui-muted)}
.eis223-workspace .eis223-tile li{padding-block:9px;border-color:#ebebef}
.eis223-workspace .eis223-tile li a{white-space:normal!important;overflow-wrap:anywhere}
.eis223-workspace :is(.eis223-tile ul,.eis223-feature ul){scrollbar-gutter:stable;overscroll-behavior:contain}
.eis223-workspace .eis223-overview>span{background:#fff;border-color:var(--ui-line);
 border-radius:10px;box-shadow:none;padding:11px 14px}
.eis223-workspace .eis223-overview b{color:var(--ui-ink);font-size:23px;font-weight:600;letter-spacing:-.04em}
.eis223-workspace .eis223-overview small{color:var(--ui-muted);font-size:12px}
.eis223-workspace :is(.eis223-all-checks,.eis223-tile a,.eis223-feature a){color:var(--ui-accent);font-weight:500}
.eis223-workspace .eis223-employee-links a{background:#fff;border-color:var(--ui-line);
 color:var(--ui-ink)!important;font-weight:500;min-height:36px;padding:8px 11px}
.eis223-workspace .eis223-employee-links a:first-child{background:var(--ui-accent);
 border-color:var(--ui-accent);color:#fff!important}
.eis223-workspace .eis223-employee-links a:first-child:hover{background:var(--ui-hover)}
.eis223-workspace :is(.eis223-cabinet-extra,.eis223-panel-mark){background:#fff;border-color:var(--ui-line);color:var(--ui-muted);box-shadow:none}
.eis223-workspace .eis223-cabinet-shortcuts{padding:0;border:0;background:transparent;box-shadow:none;margin:0 0 12px}
.eis223-workspace .eis223-cabinet-shortcuts .eis223-note{padding:0;border:0;background:transparent}
.eis223-workspace .eis223-cabinet-extra{padding:0 13px}
.eis223-workspace .eis223-cabinet-extra>summary{color:var(--ui-ink);font-weight:600}
.eis223-workspace .eis223-cabinet-extra[open]>summary{border-color:var(--ui-line)}
.eis223-workspace .eis223-note{background:#f5f5f7;color:var(--ui-muted);border-color:var(--ui-line)}
.eis223-workspace :is(input,select,textarea){max-width:100%;min-width:0;color:var(--ui-ink);
 border-color:#c8c8cf;background:#fff;font-family:var(--ui-font);accent-color:var(--ui-accent)}
.eis223-workspace :is(.eis223-tile,.eis223-feature,.eis223-profile-prompt) button{
 background:var(--ui-accent);border-color:var(--ui-accent);font-family:var(--ui-font);font-weight:500}
.eis223-workspace .eis223-profile-prompt form{border-color:var(--ui-line);color:var(--ui-ink);border-radius:16px}
.eis223-workspace .eis223-profile-prompt h2{color:var(--ui-ink)!important}
.eis223-workspace .eis223-profile-prompt :is(p,label){color:var(--ui-muted)}
.eis223-workspace :is(button,a,input,select,textarea,summary):focus-visible{
 outline:3px solid #0066cc!important;outline-offset:3px}
.eis223-workspace button:disabled{cursor:wait}
@media(max-width:1100px){.eis223-workspace .eis223-auth-user{max-width:45vw}}
@media(max-width:660px){.eis223-workspace .eis223-menu{padding:10px 12px}
 .eis223-workspace .eis223-auth-user{max-width:none;width:100%}
 .eis223-workspace .eis223-personal{padding:16px 12px}
 .eis223-workspace .eis223-employee-links a{flex:1 1 42%;justify-content:center}}
@media(pointer:coarse){.eis223-workspace :is(button,.eis223-menu a,.eis223-employee-links a,
 .eis223-all-checks,input:not([type=checkbox]):not([type=radio]),select,summary){min-height:44px!important}}
@media(prefers-reduced-motion:reduce){.eis223-workspace *{transition:none!important}}
""")


def cabinet_clarity_style():
    """Task-focused account view; duplicate navigation stays as the no-JS fallback."""
    return _style('eis223-cabinet-clarity', r"""
body.eis-shell-ready .eis223-menu :is(#eis223-toggle-cabinet,#eis223-admin-nav,.eis223-manager-link,a[href="/account/admin"]),
body.eis-shell-ready .eis223-cabinet-shortcuts,
body.eis-shell-ready .eis223-cabinet-profile{display:none!important}
.eis223-workspace .eis223-identity-link{display:grid;gap:2px;text-decoration:none;
 min-width:0;max-width:100%;text-align:left;line-height:1.35}
.eis223-workspace .eis223-menu .eis223-identity-link{border:0!important;padding:0!important;
 min-height:0;background:transparent!important}
body.eis-shell-ready .eis223-workspace .eis223-auth-user{margin-left:auto;background:transparent;
 border-color:transparent;max-width:min(44vw,360px);padding:2px 8px}
body.eis-shell-ready .eis223-workspace .eis223-menu button#eis223-logout{border-color:transparent!important;
 background:transparent!important;padding-inline:9px!important}
body.eis-shell-ready .eis223-workspace .eis223-personal{padding-block:28px 38px}
body.eis-shell-ready .eis223-workspace .eis223-cabinet-heading{align-items:flex-start;margin-bottom:0;gap:20px}
body.eis-shell-ready .eis223-workspace .eis223-personal h2{font-size:28px!important;letter-spacing:-.04em;font-weight:650}
.eis223-cabinet-note{font-size:13px;line-height:1.6;color:var(--ui-muted);margin:8px 0 3px}
body.eis-shell-ready .eis223-workspace #eis223-owner-fullname{font-size:11px;color:var(--ui-muted);margin-top:3px}
.eis223-cabinet-actions{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
body.eis-shell-ready .eis223-workspace button#eis223-start-check{display:inline-flex;align-items:center;justify-content:center;
 min-height:38px;background:var(--ui-accent)!important;border:1px solid var(--ui-accent)!important;
 color:#fff!important;padding:9px 15px!important;border-radius:9px;font:500 13px var(--ui-font);cursor:pointer;white-space:nowrap}
body.eis-shell-ready .eis223-workspace button#eis223-start-check:hover{background:var(--ui-hover)!important}
body.eis-shell-ready .eis223-workspace .eis223-overview{display:flex;flex-wrap:wrap;gap:0;
 padding:18px 0 17px;margin:0 0 22px;border-bottom:1px solid var(--ui-line)}
body.eis-shell-ready .eis223-workspace .eis223-overview>span{display:inline-flex;align-items:baseline;gap:7px;
 background:transparent;border:0;border-radius:0;box-shadow:none;padding:0 22px 0 0;margin-right:22px;
 border-right:1px solid var(--ui-line)}
body.eis-shell-ready .eis223-workspace .eis223-overview>span:last-child{border-right:0;margin-right:0;padding-right:0}
body.eis-shell-ready .eis223-workspace .eis223-overview b{font-size:20px;line-height:1.2;font-weight:600}
body.eis-shell-ready .eis223-workspace .eis223-overview small{font-size:12px;margin:0}
body.eis-shell-ready .eis223-workspace .eis223-columns{grid-template-columns:minmax(0,1.6fr) minmax(280px,1fr);gap:16px;align-items:stretch}
body.eis-shell-ready .eis223-workspace .eis223-tile{padding:19px;border-radius:12px;box-shadow:none}
body.eis-shell-ready .eis223-workspace .eis223-tile-main{grid-row:span 2;display:flex;flex-direction:column}
body.eis-shell-ready .eis223-workspace .eis223-tile h3{font-size:16px!important;font-weight:600}
body.eis-shell-ready .eis223-workspace .eis223-tile-sub{font-size:12px;line-height:1.5;margin-top:4px}
body.eis-shell-ready .eis223-workspace .eis223-tile-head{margin-bottom:12px;padding-bottom:0}
body.eis-shell-ready .eis223-workspace .eis223-tile-main ul{max-height:360px;min-height:60px;margin:0 0 8px}
body.eis-shell-ready .eis223-workspace .eis223-tile-main li{display:grid;grid-template-columns:minmax(0,1fr) auto;
 gap:12px;padding:14px 0;align-items:center}
body.eis-shell-ready .eis223-workspace .eis223-check-desc{gap:5px}
body.eis-shell-ready .eis223-workspace .eis223-check-desc strong{font-size:13px;color:var(--ui-ink);font-weight:600}
body.eis-shell-ready .eis223-workspace .eis223-check-desc small{font-size:11px;color:var(--ui-muted);line-height:1.5}
.eis223-row-actions{display:flex;gap:7px;flex-wrap:wrap;align-items:center;justify-content:flex-end;max-width:220px}
body.eis-shell-ready .eis223-workspace .eis223-row-actions a{font-weight:500!important;font-size:12px}
body.eis-shell-ready .eis223-workspace .eis223-row-actions .eis223-open-card{padding:7px 9px;border:1px solid var(--ui-line);
 border-radius:7px;background:#fff;line-height:1.4;min-height:32px;display:inline-flex;align-items:center}
body.eis-shell-ready .eis223-workspace .eis223-tile li .eis223-row-actions button{margin:0;padding:5px 7px!important;
 border-color:transparent!important;min-height:30px!important;background:transparent!important;color:var(--ui-muted)!important;font-size:11px!important}
.eis223-legal-state::before{content:"";display:inline-block;width:5px;height:5px;margin:0 6px 1px 0;border-radius:50%;background:#7b7b80}
.eis223-legal-state[data-tone=complete]::before{background:#216e45}
.eis223-legal-state[data-tone=attention]::before{background:#b42332}
body.eis-shell-ready .eis223-workspace #eis223-add-procurement{border-top:1px solid var(--ui-line);padding-top:16px;margin-top:auto;gap:8px}
body.eis-shell-ready .eis223-workspace #eis223-add-procurement button{background:#fff!important;
 color:var(--ui-accent)!important;border-color:#bdcfe4!important;font-weight:500!important}
body.eis-shell-ready .eis223-workspace #eis223-add-procurement button:hover{background:var(--ui-selected)!important}
body.eis-shell-ready .eis223-workspace .eis223-more{display:block;margin-top:18px;background:#fff;
 border:1px solid var(--ui-line);border-radius:12px;padding:0 18px}
body.eis-shell-ready #eis223-cabinet .eis223-more>.eis223-cabinet-extra{border:0!important;border-bottom:1px solid var(--ui-line)!important;
 border-radius:0!important;padding:0!important;box-shadow:none!important;background:transparent!important;margin:0}
body.eis-shell-ready #eis223-cabinet .eis223-more>.eis223-cabinet-extra:last-child{border-bottom:0!important}
body.eis-shell-ready .eis223-workspace .eis223-more summary{min-height:46px;padding:13px 0;font-size:13px;font-weight:500}
body.eis-shell-ready #eis223-cabinet .eis223-more>.eis223-cabinet-extra[open]{padding-bottom:15px!important}
@media(max-width:900px){body.eis-shell-ready .eis223-workspace .eis223-columns{grid-template-columns:minmax(0,1fr)}
 body.eis-shell-ready .eis223-workspace .eis223-tile-main{grid-row:auto}}
@media(max-width:660px){body.eis-shell-ready .eis223-workspace>.eis223-menu{display:grid;
 grid-template-columns:44px minmax(0,1fr) auto;gap:6px 8px!important;align-items:center}
 body.eis-shell-ready .eis223-workspace .eis223-menu #eis-shell-nav-toggle{grid-column:1;grid-row:1}
 body.eis-shell-ready .eis223-workspace .eis223-menu .eis223-brand{grid-column:2;grid-row:1;min-width:0;margin-right:0}
 body.eis-shell-ready .eis223-workspace .eis223-menu #eis223-logout{grid-column:3;grid-row:1}
 body.eis-shell-ready .eis223-workspace .eis223-auth-user{grid-column:2/-1;grid-row:2;max-width:100%;width:auto;min-width:0;padding:0;margin-left:0}
 body.eis-shell-ready .eis223-workspace .eis223-auth-user strong{font-size:12px}
 body.eis-shell-ready .eis223-workspace .eis223-personal{padding:22px 14px 30px}
 body.eis-shell-ready .eis223-workspace .eis223-personal h2{font-size:25px!important}
 body.eis-shell-ready .eis223-workspace .eis223-cabinet-heading{gap:14px}
 body.eis-shell-ready .eis223-workspace .eis223-overview{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:0;padding-block:17px}
 body.eis-shell-ready .eis223-workspace .eis223-overview>span{display:block;padding-right:12px;margin-right:12px}
 body.eis-shell-ready .eis223-workspace .eis223-overview b{display:block}
 body.eis-shell-ready .eis223-workspace .eis223-overview small{display:block;font-size:11px;line-height:1.4;margin-top:3px}
 body.eis-shell-ready .eis223-workspace .eis223-tile{padding:16px}
 body.eis-shell-ready .eis223-workspace .eis223-tile-main li{grid-template-columns:minmax(0,1fr);gap:10px}
 body.eis-shell-ready .eis223-workspace .eis223-tile-main ul{max-height:none;overflow:visible}
 .eis223-row-actions{max-width:none;justify-content:flex-start}
 body.eis-shell-ready .eis223-workspace .eis223-more{padding-inline:15px}}
@media(pointer:coarse){body.eis-shell-ready .eis223-workspace :is(button#eis223-start-check,.eis223-row-actions a,.eis223-row-actions button){min-height:44px!important}}
""")


def home_style():
    """Final visual layer over the existing authenticated registry layout."""
    return _style('eis223-home-polish', r"""
body:has(> .eis223-workspace){--eis-bg:var(--ui-bg);--eis-text:var(--ui-ink);
 --eis-muted:var(--ui-muted);--eis-line:var(--ui-line);--eis-blue:var(--ui-accent);
 --eis-success:var(--ui-success);--eis-danger:var(--ui-danger);--eis-shadow:var(--ui-shadow);
 font-family:var(--ui-font)!important;-webkit-font-smoothing:antialiased}
body:has(> .eis223-workspace)>header{padding-block:10px!important}
body:has(> .eis223-workspace)>header h1{color:var(--ui-ink)!important;font-weight:600!important}
body:has(> .eis223-workspace) .eis361-heading{background:transparent;border:0;box-shadow:none;
 padding:4px 0 14px!important;margin-bottom:0!important;gap:12px}
body:has(> .eis223-workspace) .eis361-heading h2{color:var(--ui-ink)!important;
 font-size:clamp(22px,2.2vw,28px)!important;letter-spacing:-.035em!important;font-weight:650!important}
body:has(> .eis223-workspace) .eis361-heading p{color:var(--ui-muted);font-size:13px}
body:has(> .eis223-workspace) .eis361-heading-icon{background:#fff;color:var(--ui-accent);
 border:1px solid var(--ui-line);box-shadow:none;width:40px;height:40px}
body:has(> .eis223-workspace) :is(h2,h3,h4){color:var(--ui-ink)!important;font-weight:600}
body:has(> .eis223-workspace) .stats{gap:10px!important;margin:8px 0 16px!important}
body:has(> .eis223-workspace) .stat{padding:13px 16px!important;min-height:80px!important}
body:has(> .eis223-workspace) .stat::before{background:transparent}
body:has(> .eis223-workspace) .stat small{color:var(--ui-muted)!important;font-weight:500!important}
body:has(> .eis223-workspace) .stat b{color:var(--ui-ink)!important;font-size:27px!important;font-weight:600!important}
body:has(> .eis223-workspace) :is(.card,.filters,.detail,details,#v302-search-panel,#v300-eis-status){
 border-color:var(--ui-line)!important;box-shadow:var(--ui-shadow)!important;border-radius:12px!important}
body:has(> .eis223-workspace) .filters{gap:10px!important;padding:14px!important;align-items:end}
body:has(> .eis223-workspace) .filters label{color:var(--ui-muted)!important;font-weight:500}
body:has(> .eis223-workspace) :is(input,select,textarea){color:var(--ui-ink)!important;
 border-color:#c8c8cf!important;min-height:36px!important;font-family:var(--ui-font)!important}
body:has(> .eis223-workspace) input:is([type=checkbox],[type=radio]){
 min-height:0!important;width:17px!important;height:17px!important;padding:0!important;flex:none}
body:has(> .eis223-workspace) .choices{max-width:calc(100vw - 28px)}
body:has(> .eis223-workspace) :is(button,input[type=submit],.btn){min-height:36px!important;
 font-family:var(--ui-font)!important;font-weight:500!important}
body:has(> .eis223-workspace) button:hover:not(:disabled){background:var(--ui-hover)!important;border-color:var(--ui-hover)!important}
body:has(> .eis223-workspace) table th{background:#f5f5f7!important;color:var(--ui-muted)!important;
 font-size:12px!important;font-weight:600!important;padding:10px 12px!important;border-color:var(--ui-line)!important}
body:has(> .eis223-workspace) table td{color:var(--ui-ink)!important;padding:10px 12px!important;line-height:1.5;border-color:#ebebef!important}
body:has(> .eis223-workspace) table td small{color:var(--ui-muted)}
body:has(> .eis223-workspace) table tbody tr:nth-child(even) td{background:#fafafa!important}
body:has(> .eis223-workspace) table tbody tr:hover td{background:#f0f5ff!important}
body:has(> .eis223-workspace) table td:is(.bad,.violation,.risk-red,.status-violation){color:var(--ui-danger)!important}
body:has(> .eis223-workspace) #v257-tabs{gap:6px!important;padding-bottom:12px;border-bottom:1px solid var(--ui-line)}
body:has(> .eis223-workspace) #v257-tabs a{background:#fff!important;border-color:var(--ui-line)!important;
 color:var(--ui-muted)!important;padding:7px 12px!important;min-height:36px;line-height:1.45}
body:has(> .eis223-workspace) #v257-tabs a[aria-current=page]{background:var(--ui-selected)!important;
 border-color:#b9d3f4!important;color:var(--ui-hover)!important;box-shadow:none}
body:has(> .eis223-workspace) .v302-header{background:#fafafa!important;padding:14px 18px;border-color:var(--ui-line)!important}
body:has(> .eis223-workspace) .v302-title strong{color:var(--ui-ink)!important;font-size:17px}
body:has(> .eis223-workspace) .v302-title small{color:var(--ui-muted)}
body:has(> .eis223-workspace) .v302-box{background:#fff!important;border-color:var(--ui-line)!important;padding:14px}
body:has(> .eis223-workspace) .v302-links{background:#fafafa!important;border-color:var(--ui-line)!important;padding:12px 18px}
body:has(> .eis223-workspace) :is(button,a,input,select,textarea,summary):focus-visible{
 outline:3px solid #0066cc!important;outline-offset:3px}
body:has(> .eis223-workspace) summary:focus-visible{outline:3px solid #0066cc!important;outline-offset:3px}
@media(max-width:620px){body:has(> .eis223-workspace) .filters>:is(input,select,details,button){flex:1 1 100%;width:100%}
 body:has(> .eis223-workspace) .eis361-right{padding-left:0}
 body:has(> .eis223-workspace) #v257-tabs a{flex:1 1 140px;text-align:center}}
@media(pointer:coarse){body:has(> .eis223-workspace) :is(button,.btn,input:not([type=checkbox]):not([type=radio]),select){min-height:44px!important}}
""")


def account_page_style():
    """Personal, employee and manager pages; never alter their layout or roles."""
    return _style('eis223-account-polish', r"""
:root{--ink:var(--ui-ink);--muted:var(--ui-muted);--line:var(--ui-line);--edge:var(--ui-line);
 --blue:var(--ui-accent);--accent:var(--ui-accent);--accent-dark:var(--ui-hover);--soft:var(--ui-bg)}
body{background:var(--ui-bg);color:var(--ui-ink);font-family:var(--ui-font);-webkit-font-smoothing:antialiased}
body>header,.mgr-top{background:#fff;color:var(--ui-ink);border-bottom:1px solid var(--ui-line);padding-block:12px}
body>header a,.mgr-top a{color:var(--ui-ink);background:#fff;border-color:var(--ui-line);
 min-height:36px;display:inline-flex;align-items:center;line-height:1.4;font-weight:500}
body>header a:hover,.mgr-top a:hover{background:#f5f5f7;text-decoration:none}
body>header .action-row a{background:#fff;border-color:var(--ui-line);color:var(--ui-ink)}
body>header .action-row a:hover{background:#f5f5f7}
body>header #staff-user-name{color:var(--ui-muted)!important}
.mgr-brand small{color:var(--ui-muted)}
.mgr-logo{color:var(--ui-accent);background:var(--ui-selected);border-color:#d4e3f7;font-weight:650}
main{min-width:0}
body :is(h1,h2,h3){color:var(--ui-ink);letter-spacing:-.025em;line-height:1.3;font-weight:650}
body .mgr-head h1,body .mgr-card h2,body .mgr-card h3{color:var(--ui-ink);font-weight:650}
body :is(p,label,legend,.mgr-card p,.metric small,.mgr-kpi small,.mgr-kpi span,.mgr-muted,
 .entry small,.staff-rowbody small,.mgr-footnote){color:var(--ui-muted)}
body a{color:var(--ui-accent);font-weight:500}
body :is(.panel,.mgr-card,.metric,.mgr-kpi){border-color:var(--ui-line);box-shadow:var(--ui-shadow);border-radius:12px}
body>main>.panel{padding:22px}
body>main>.panel>nav{gap:6px;padding:10px 0 14px;margin-bottom:18px}
body>main>.panel>nav a{background:#fff;border-color:var(--ui-line);padding:7px 11px;min-height:36px}
body>main>.panel>nav a:hover{background:#f5f5f7}
body :is(button,input,select,textarea){font-family:var(--ui-font);line-height:1.45}
body :is(button,.mgr-primary,.mgr-outline,.mgr-danger){font-weight:500}
body button{background:var(--ui-accent);border-color:var(--ui-accent)}
body :is(button.primary,.mgr-primary){background:var(--ui-accent);border-color:var(--ui-accent);color:#fff}
body :is(button.primary,.mgr-primary):hover:not(:disabled){background:var(--ui-hover)}
body :is(button.outline,.mgr-outline){color:var(--ui-ink);background:#fff;border-color:var(--ui-line)}
body :is(button.outline,.mgr-outline):hover{background:#f5f5f7}
body :is(button.danger,.mgr-danger){color:var(--ui-danger);background:#fff;border-color:#e7c6ca}
body :is(button.danger,.mgr-danger):hover{background:#fff1f2}
body :is(.manual-note,.notice,.mgr-banner){line-height:1.55;background:#f0f5ff;color:#425a7a;border-color:#b9d3f4}
body :is(.table-scroll,.mgr-table-wrap){overscroll-behavior-x:contain;scrollbar-gutter:stable}
body :is(table,.mgr-table){font-variant-numeric:tabular-nums;border-color:var(--ui-line)}
body :is(th,.mgr-table th){background:#f5f5f7;color:var(--ui-muted);line-height:1.45;font-weight:600}
body :is(td,.mgr-table td){line-height:1.5;border-color:#ebebef}
body :is(.metric strong,.mgr-kpi strong){color:var(--ui-ink);font-weight:600;letter-spacing:-.04em}
body :is(.mgr-role,.staff-statline){background:#f5f5f7;color:var(--ui-muted);border-color:var(--ui-line);font-weight:500}
body .staff-layout>.tabs{background:#fff;border-color:var(--ui-line);box-shadow:var(--ui-shadow)}
body .staff-layout>.tabs button{background:transparent;color:var(--ui-muted);font-weight:500}
body .staff-layout>.tabs button:hover{background:#f5f5f7}
body .staff-layout>.tabs button[aria-selected=true]{background:var(--ui-selected);color:var(--ui-hover);box-shadow:none;border-color:transparent}
body .staff-subpanel{background:#fff;border-color:var(--ui-line)}
.staff-compact .staff-columns .staff-subpanel{border:0;border-bottom:1px solid var(--ui-line);
 border-radius:0;padding:4px 0 15px;box-shadow:none;background:transparent;margin-bottom:15px}
.staff-compact .staff-columns .staff-subpanel:last-child{border-bottom:0;margin-bottom:0}
body .staff-tools :is(button,a){line-height:1.4}
body .staff-rowbody strong{overflow-wrap:anywhere}
.staff-compact .staff-smallrow .staff-rowbody strong{color:var(--ui-ink);font-weight:600}
.staff-compact .staff-smallrow .staff-rowbody small{color:var(--ui-muted)}
body .mgr-side{background:#f9f9fb;border-color:var(--ui-line)}
body .mgr-tab{color:var(--ui-muted);background:transparent;font-weight:500}
body .mgr-tab:hover{background:#ededf0}
body .mgr-tab[aria-selected=true]{background:var(--ui-selected);color:var(--ui-hover);border-color:transparent;box-shadow:none}
body .mgr-entry{border-color:var(--ui-line)}
body .mgr-side-heading{color:var(--ui-muted);letter-spacing:.06em;font-weight:600}
body .eis-my-check{border-color:var(--ui-line)!important;box-shadow:var(--ui-shadow)!important}
body .eis-my-check h2{color:var(--ui-ink)!important}
body .eis-my-actions a{background:var(--ui-accent)!important;border-color:var(--ui-accent)!important}
body .eis-pill{background:var(--ui-selected)!important;color:var(--ui-hover)!important}
@media(min-width:981px){body .staff-layout>.tabs{max-height:calc(100dvh - 28px);overflow-y:auto}}
@media(min-width:901px){body .mgr-side{align-self:start;position:sticky;top:0;max-height:100dvh;overflow-y:auto}}
@media(max-width:720px){body>main>.panel{padding:17px 14px}
 body>main>.panel>nav a{flex:1 1 150px;text-align:center;justify-content:center}}
""" + _INPUTS)


def signin_style():
    """The existing login/registration portal, without changing form behavior."""
    return _style('eis223-signin-style', r"""
*{box-sizing:border-box}
html,body{margin:0;min-height:100%;font-family:var(--ui-font)}
body{background:var(--ui-bg);color:var(--ui-ink);-webkit-font-smoothing:antialiased}
.layout{min-height:100vh;min-height:100dvh;display:flex;align-items:center;justify-content:center;padding:24px 16px}
.auth{width:min(440px,100%);padding:30px;background:#fff;border:1px solid var(--ui-line);
 border-radius:18px;box-shadow:0 8px 32px #1d1d1f07}
h1{font-size:26px;line-height:1.2;font-weight:650;letter-spacing:-.045em;margin:0 0 12px;color:var(--ui-ink)}
p{font-size:13px;line-height:1.55;color:var(--ui-muted)}
.tabs{display:flex;gap:3px;padding:3px;margin:22px 0 20px;background:#ededf0;border:1px solid #e6e6eb;border-radius:9px}
.tabs button{flex:1;min-height:34px;padding:7px;border:0;border-radius:6px;
 background:transparent;color:var(--ui-muted);font:500 13px var(--ui-font);cursor:pointer}
.tabs button[aria-selected=true]{background:#fff;color:var(--ui-ink);box-shadow:0 1px 4px #1d1d1f12}
form[hidden]{display:none!important}
label{display:block;font-size:12px;font-weight:500;color:var(--ui-ink);margin:15px 0 6px}
input{width:100%;display:block;min-height:40px;padding:10px 12px;border:1px solid #c8c8cf;
 border-radius:8px;background:#fff;color:var(--ui-ink);font:14px var(--ui-font)}
input:focus{outline:3px solid #0066cc28;outline-offset:1px;border-color:var(--ui-accent)}
.submit{width:100%;min-height:40px;padding:10px 12px;margin-top:22px;background:var(--ui-accent);
 color:#fff;border:1px solid var(--ui-accent);border-radius:8px;font:500 14px var(--ui-font);cursor:pointer}
.submit:hover:not(:disabled){background:var(--ui-hover)}
.submit:disabled{opacity:.55}
#auth-status{font-size:12px;line-height:1.5;min-height:18px;margin:12px 0;color:var(--ui-muted)}
.small{font-size:12px;text-align:center;line-height:1.5;color:var(--ui-muted);margin:16px 0 0}
@media(max-width:500px){.layout{align-items:flex-start;padding:6vh 14px 24px}.auth{padding:24px 20px;border-radius:16px}h1{font-size:25px}}
""" + _INPUTS)


def card_style():
    """Independent read-only audit card and its preserved evidence sections."""
    return _style('eis223-card-polish', r"""
body{font:14px/1.55 var(--ui-font);background:var(--ui-bg);color:var(--ui-ink);-webkit-font-smoothing:antialiased}
:root{--edge:var(--ui-line);--green:var(--ui-accent)}
header{background:#fff;color:var(--ui-ink);border-color:var(--ui-line);padding-block:16px}
header h1{font-size:clamp(20px,2.3vw,25px);font-weight:650;letter-spacing:-.035em;overflow-wrap:anywhere}
main{margin:18px auto;max-width:1250px}
a{color:var(--ui-accent);font-weight:500}
nav a,.action{border-color:var(--ui-line);border-radius:8px;min-height:36px;padding:8px 12px;font-weight:500}
.action{background:var(--ui-accent);border-color:var(--ui-accent);color:#fff}
.action:hover{background:var(--ui-hover);color:#fff}
.panel{border-color:var(--ui-line);border-radius:12px;padding:18px 20px;box-shadow:var(--ui-shadow);margin:12px 0}
.panel h2{color:var(--ui-ink);font-size:19px;font-weight:600;letter-spacing:-.025em;margin-bottom:12px}
.result-group{padding:0}
.result-group>summary{min-height:48px;padding:14px 20px;color:var(--ui-ink)}
.result-group>summary h2{font-size:19px}
.result-group>summary::marker{color:var(--ui-muted)}
.result-count{color:var(--ui-muted);font-weight:500}
.result-body{padding:0 20px 18px}
.legal{border-color:#ebcdd1;border-left-color:var(--ui-danger);background:#fffbfb;border-radius:10px}
.review{border-color:#eadcb9;border-left-color:var(--ui-warning);background:#fffdf7;border-radius:10px}
.legal h3,.review h3{color:var(--ui-ink);font-weight:600}
.meta{color:var(--ui-muted)}
.chip{background:var(--ui-selected);color:var(--ui-hover);border-color:#d4e3f7;font-weight:500}
.source-fragment{border-color:#c8c8cf}
table{border-color:var(--ui-line);border-radius:10px;font-variant-numeric:tabular-nums;font-size:13px}
th{background:#f5f5f7;color:var(--ui-muted);font-weight:600}
th,td{padding:10px 12px;border-color:#ebebef}
body .eis-finding-review{background:#f5f5f7;color:var(--ui-ink);border-color:var(--ui-line);font-family:var(--ui-font)}
body .eis-finding-review button{background:var(--ui-accent);border-color:var(--ui-accent);font-weight:500}
body .eis-finding-review button.secondary{background:#fff;color:var(--ui-ink);border-color:var(--ui-line)}
@media(max-width:660px){header{padding:14px}.panel{padding:15px}.result-group{padding:0}
 .result-group>summary{padding:14px 15px}.result-body{padding:0 15px 15px}
 th,td{min-width:105px}th:first-child,td:first-child{min-width:210px}}
""" + _INPUTS)


def profile_style():
    """Same visual language for the existing edit-profile form."""
    return _style('eis223-profile-polish', r"""
body{font-family:var(--ui-font);background:var(--ui-bg);color:var(--ui-ink);-webkit-font-smoothing:antialiased}
header{background:#fff;color:var(--ui-ink);border-bottom:1px solid var(--ui-line);padding-block:13px}
header strong{font-size:17px;font-weight:600}
header a{color:var(--ui-ink);background:#fff;border-color:var(--ui-line);font-size:13px}
main{margin:28px auto;max-width:720px}
article{border-color:var(--ui-line);border-radius:14px;box-shadow:var(--ui-shadow);padding:26px}
h1{color:var(--ui-ink);font-weight:650;font-size:25px;letter-spacing:-.035em}
p{color:var(--ui-muted);font-size:13px;line-height:1.55}
label{color:var(--ui-ink);font-weight:500}
input{min-height:40px}
button{background:var(--ui-accent);border-color:var(--ui-accent);font:500 13px var(--ui-font);border-radius:8px;min-height:38px}
button:hover:not(:disabled){background:var(--ui-hover)}
.meta{background:#f5f5f7;color:var(--ui-ink);border:1px solid var(--ui-line);border-radius:10px}
#profile-status{color:var(--ui-muted)}
@media(max-width:500px){main{margin:18px auto}article{padding:20px}}
""" + _INPUTS)
