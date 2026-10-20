package ru.eis223.auditor;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.graphics.Color;
import android.net.Uri;
import android.net.http.SslError;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.Message;
import android.provider.DocumentsContract;
import android.util.Base64;
import android.view.Gravity;
import android.view.View;
import android.view.WindowInsets;
import android.view.WindowInsetsController;
import android.webkit.CookieManager;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.SslErrorHandler;
import android.webkit.URLUtil;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.Button;
import android.widget.FrameLayout;
import android.widget.LinearLayout;
import android.widget.PopupMenu;
import android.widget.ProgressBar;
import android.widget.TextView;
import android.widget.Toast;
import org.json.JSONObject;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MainActivity extends Activity {
    private static final int PICK_FILE = 51, SAVE_FILE = 52;
    private static final int BLUE = Color.rgb(23, 95, 146);
    private static final int INK = Color.rgb(33, 63, 87);
    private static final int MAX_BLOB = 12 * 1024 * 1024;
    private final Handler main = new Handler(Looper.getMainLooper());
    private final ExecutorService downloads = Executors.newSingleThreadExecutor();
    private WebView web;
    private ProgressBar progress;
    private LinearLayout errorPanel;
    private TextView back;
    private boolean failed, busySaving, cabinetRequested;
    private String failedUrl = UrlPolicy.BASE, blobNonce;
    private ValueCallback<Uri[]> filePicker;
    private PendingDownload pending;

    private static final class PendingDownload {
        String url, mime, name, cookie, userAgent;
        byte[] data;
    }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setBackgroundColor(Color.WHITE);
        if (Build.VERSION.SDK_INT >= 30) {
            getWindow().setDecorFitsSystemWindows(false);
            root.setOnApplyWindowInsetsListener((v, insets) -> {
                android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars() | WindowInsets.Type.displayCutout());
                int ime = insets.getInsets(WindowInsets.Type.ime()).bottom;
                v.setPadding(bars.left, bars.top, bars.right, Math.max(bars.bottom, ime));
                return insets;
            });
            WindowInsetsController c = getWindow().getInsetsController();
            if (c != null) c.setSystemBarsAppearance(
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS,
                WindowInsetsController.APPEARANCE_LIGHT_STATUS_BARS | WindowInsetsController.APPEARANCE_LIGHT_NAVIGATION_BARS);
        }

        LinearLayout toolbar = new LinearLayout(this);
        toolbar.setGravity(Gravity.CENTER_VERTICAL);
        toolbar.setPadding(dp(4), 0, dp(4), 0);
        back = toolbarButton("‹", "Назад");
        back.setOnClickListener(v -> goBack());
        toolbar.addView(back, new LinearLayout.LayoutParams(dp(48), dp(54)));
        LinearLayout heading = new LinearLayout(this);
        heading.setOrientation(LinearLayout.VERTICAL);
        TextView title = new TextView(this);
        title.setText("ЕИС Аудитор"); title.setTextSize(18); title.setTextColor(INK);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        TextView subtitle = new TextView(this);
        subtitle.setText("223-ФЗ · Проверки закупок"); subtitle.setTextSize(11);
        subtitle.setTextColor(Color.rgb(104, 126, 142));
        heading.addView(title); heading.addView(subtitle);
        toolbar.addView(heading, new LinearLayout.LayoutParams(0, -2, 1));
        TextView more = toolbarButton("⋮", "Меню приложения");
        more.setOnClickListener(this::showMenu);
        toolbar.addView(more, new LinearLayout.LayoutParams(dp(48), dp(54)));
        root.addView(toolbar, new LinearLayout.LayoutParams(-1, dp(54)));
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setProgressTintList(android.content.res.ColorStateList.valueOf(BLUE));
        root.addView(progress, new LinearLayout.LayoutParams(-1, dp(2)));
        FrameLayout content = new FrameLayout(this);
        web = new WebView(this);
        web.setId(101);
        web.setBackgroundColor(Color.rgb(244, 247, 250));
        content.addView(web, new FrameLayout.LayoutParams(-1, -1));
        errorPanel = makeErrorPanel();
        content.addView(errorPanel, new FrameLayout.LayoutParams(-1, -1));
        errorPanel.setVisibility(View.GONE);
        root.addView(content, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        root.requestApplyInsets();
        configureWeb();
        if (Build.VERSION.SDK_INT >= 33) getOnBackInvokedDispatcher().registerOnBackInvokedCallback(0, this::goBack);
        if (state == null || web.restoreState(state) == null || !UrlPolicy.trusted(web.getUrl())) load(UrlPolicy.BASE);
    }

    private int dp(int value) { return (int)(getResources().getDisplayMetrics().density * value + .5f); }
    private TextView toolbarButton(String text, String label) {
        TextView t = new TextView(this);
        t.setText(text); t.setTextColor(BLUE); t.setTextSize(30);
        t.setGravity(Gravity.CENTER); t.setContentDescription(label);
        t.setFocusable(true); t.setClickable(true);
        android.util.TypedValue value = new android.util.TypedValue();
        getTheme().resolveAttribute(android.R.attr.selectableItemBackgroundBorderless, value, true);
        t.setBackgroundResource(value.resourceId);
        return t;
    }

    private LinearLayout makeErrorPanel() {
        LinearLayout panel = new LinearLayout(this);
        panel.setOrientation(LinearLayout.VERTICAL); panel.setGravity(Gravity.CENTER);
        panel.setPadding(dp(28), dp(24), dp(28), dp(24)); panel.setBackgroundColor(Color.WHITE);
        TextView title = new TextView(this);
        title.setText("Не удалось открыть программу"); title.setTextColor(INK); title.setTextSize(22);
        title.setGravity(Gravity.CENTER); panel.addView(title);
        TextView note = new TextView(this);
        note.setText("Проверьте подключение к интернету и повторите попытку. Если сервер временно недоступен, ваши данные сохраняются на нём.");
        note.setTextSize(15); note.setTextColor(INK); note.setGravity(Gravity.CENTER);
        note.setPadding(0, dp(18), 0, dp(24)); panel.addView(note);
        Button retry = new Button(this); retry.setText("Повторить");
        retry.setOnClickListener(v -> load(failedUrl)); panel.addView(retry);
        Button browser = new Button(this); browser.setText("Открыть в браузере");
        browser.setOnClickListener(v -> openExternal(failedUrl)); panel.addView(browser);
        return panel;
    }

    private void configureWeb() {
        WebView.setWebContentsDebuggingEnabled(false);
        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true); s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false); s.setAllowContentAccess(true);
        s.setAllowFileAccessFromFileURLs(false); s.setAllowUniversalAccessFromFileURLs(false);
        s.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        s.setSafeBrowsingEnabled(true);
        s.setSupportMultipleWindows(true); s.setJavaScriptCanOpenWindowsAutomatically(false);
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setUserAgentString(s.getUserAgentString() + " EIS223Mobile/1.0.0");
        CookieManager.getInstance().setAcceptCookie(true);
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false);
        web.addJavascriptInterface(new BlobReceiver(), "EisDocumentExport");
        web.setWebViewClient(new WebViewClient() {
            @Override public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                if (!request.isForMainFrame()) return !UrlPolicy.trusted(request.getUrl().toString());
                return route(request.getUrl().toString());
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view, String url) { return route(url); }
            @Override public void onPageStarted(WebView view, String url, android.graphics.Bitmap icon) {
                failed = false; blobNonce = null;
                if (UrlPolicy.trusted(url)) failedUrl = url;
                errorPanel.setVisibility(View.GONE); progress.setVisibility(View.VISIBLE);
            }
            @Override public void onPageFinished(WebView view, String url) {
                CookieManager.getInstance().flush(); progress.setVisibility(View.INVISIBLE);
                back.setAlpha(web.canGoBack() ? 1f : .45f);
                if (!failed) errorPanel.setVisibility(View.GONE);
                if (cabinetRequested && UrlPolicy.trusted(url)) {
                    cabinetRequested=false;
                    web.evaluateJavascript("(function(){var b=document.getElementById('eis223-toggle-cabinet');if(b&&b.getAttribute('aria-expanded')!=='true')b.click();var p=document.getElementById('eis223-cabinet');if(p)p.scrollIntoView({block:'start'});})()",null);
                }
            }
            @Override public void onReceivedError(WebView v, WebResourceRequest r, WebResourceError e) {
                if (r.isForMainFrame()) showError(r.getUrl().toString());
            }
            @Override public void onReceivedHttpError(WebView v, WebResourceRequest r, WebResourceResponse e) {
                if (r.isForMainFrame() && e.getStatusCode() >= 500) showError(r.getUrl().toString());
            }
            @Override public void onReceivedSslError(WebView v, SslErrorHandler h, SslError e) {
                h.cancel(); showError(v.getUrl());
            }
            @Override public boolean onRenderProcessGone(WebView v, android.webkit.RenderProcessGoneDetail detail) {
                // A killed WebView must be recreated, not reused.
                recreate(); return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient() {
            @Override public void onProgressChanged(WebView v, int value) { progress.setProgress(value); }
            @Override public boolean onJsAlert(WebView v, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message).setPositiveButton("ОК", (d, w) -> result.confirm())
                    .setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onJsConfirm(WebView v, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this).setMessage(message).setPositiveButton("Подтвердить", (d,w) -> result.confirm())
                    .setNegativeButton("Отмена", (d,w) -> result.cancel()).setOnCancelListener(d -> result.cancel()).show(); return true;
            }
            @Override public boolean onShowFileChooser(WebView v, ValueCallback<Uri[]> callback, FileChooserParams p) {
                if (filePicker != null) filePicker.onReceiveValue(null);
                filePicker = callback;
                Intent i = p.createIntent(); i.addCategory(Intent.CATEGORY_OPENABLE);
                try { startActivityForResult(i, PICK_FILE); }
                catch (ActivityNotFoundException e) { filePicker.onReceiveValue(null); filePicker = null; toast("Не найдено приложение для выбора файлов"); }
                return true;
            }
            @Override public boolean onCreateWindow(WebView v, boolean dialog, boolean userGesture, Message message) {
                if (!userGesture) return false;
                WebView popup = new WebView(MainActivity.this);
                popup.setWebViewClient(new WebViewClient() {
                    private boolean navigate(String url) {
                        if ("about:blank".equals(url)) return false;
                        if (UrlPolicy.trusted(url)) load(url); else openExternal(url);
                        main.post(popup::destroy); return true;
                    }
                    @Override public boolean shouldOverrideUrlLoading(WebView w, WebResourceRequest r) { return navigate(r.getUrl().toString()); }
                    @Override public boolean shouldOverrideUrlLoading(WebView w, String u) { return navigate(u); }
                });
                ((WebView.WebViewTransport) message.obj).setWebView(popup); message.sendToTarget(); return true;
            }
        });
        web.setDownloadListener(this::download);
    }

    private boolean route(String url) {
        if (UrlPolicy.trusted(url)) return false;
        openExternal(url); return true;
    }
    private void load(String url) {
        if (!UrlPolicy.trusted(url)) url = UrlPolicy.BASE;
        failed = false; errorPanel.setVisibility(View.GONE); web.loadUrl(url);
    }
    private void showError(String url) {
        failed = true;
        if (UrlPolicy.trusted(url)) failedUrl = url;
        progress.setVisibility(View.INVISIBLE); errorPanel.setVisibility(View.VISIBLE);
    }
    private void openExternal(String url) {
        if (!UrlPolicy.external(url)) { toast("Эта ссылка не поддерживается"); return; }
        try { startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse(url)).addCategory(Intent.CATEGORY_BROWSABLE)); }
        catch (ActivityNotFoundException e) { toast("Не найдено приложение для открытия ссылки"); }
    }
    private String currentUrl() { return UrlPolicy.trusted(web.getUrl()) ? web.getUrl() : UrlPolicy.BASE; }
    private void showMenu(View anchor) {
        PopupMenu menu = new PopupMenu(this, anchor);
        String[] titles = {"Главная страница", "Личный кабинет", "Обновить", "Открыть в браузере", "Поделиться ссылкой", "О приложении"};
        for (int i=0; i<titles.length; i++) menu.getMenu().add(0,i,i,titles[i]);
        menu.setOnMenuItemClickListener(item -> {
            switch (item.getItemId()) {
                case 0: load(UrlPolicy.BASE); break;
                case 1: cabinetRequested=true; load(UrlPolicy.BASE); break;
                case 2: if (failed) load(failedUrl); else web.reload(); break;
                case 3: openExternal(currentUrl()); break;
                case 4:
                    Intent share = new Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, currentUrl());
                    startActivity(Intent.createChooser(share,"Поделиться ссылкой")); break;
                default: new AlertDialog.Builder(this).setTitle("ЕИС Аудитор 1.0.0")
                    .setMessage("Мобильное приложение вашей программы проверки закупок по 223-ФЗ.\n\nВходите с той же учётной записью, что и на сайте. Для работы нужен интернет.\n\nДокументы сохраняются в выбранную вами папку через системное окно Android.")
                    .setPositiveButton("Понятно",null).show();
            }
            return true;
        }); menu.show();
    }
    @Override public void onBackPressed() { goBack(); }
    private void goBack() {
        if (web.canGoBack()) { failed=false; errorPanel.setVisibility(View.GONE); web.goBack(); }
        else finish();
    }

    private void download(String url, String agent, String disposition, String mime, long length) {
        if (pending != null || busySaving || blobNonce != null) { toast("Сначала завершите сохранение документа"); return; }
        if (!UrlPolicy.trusted(web.getUrl())) return;
        if (url.startsWith("blob:")) { exportBlob(url); return; }
        if (!UrlPolicy.trusted(url)) { openExternal(url); return; }
        PendingDownload p = new PendingDownload(); p.url=url; p.userAgent=agent;
        p.cookie=CookieManager.getInstance().getCookie(url);
        p.mime=mime == null || mime.isEmpty() ? "application/octet-stream" : mime;
        p.name=UrlPolicy.safeName(URLUtil.guessFileName(url,disposition,p.mime));
        chooseDestination(p);
    }

    private void chooseDestination(PendingDownload p) {
        pending=p;
        Intent i=new Intent(Intent.ACTION_CREATE_DOCUMENT).setType(p.mime)
            .addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_TITLE,p.name);
        try { startActivityForResult(i,SAVE_FILE); }
        catch (ActivityNotFoundException e) { pending=null; toast("Не найдено приложение для сохранения файлов"); }
    }

    private void exportBlob(String url) {
        final String nonce=UUID.randomUUID().toString(); blobNonce=nonce;
        String js="(function(){fetch("+JSONObject.quote(url)+").then(r=>r.blob()).then(b=>{if(b.size>"+MAX_BLOB+")throw Error('large');"
            +"var name='Документ';for(var a of document.querySelectorAll('a[download]'))if(a.href==="+JSONObject.quote(url)+")name=a.download||name;"
            +"var f=new FileReader();f.onload=()=>EisDocumentExport.receive("+JSONObject.quote(nonce)+",name,b.type,f.result);f.onerror=()=>EisDocumentExport.failed("+JSONObject.quote(nonce)+");f.readAsDataURL(b);"
            +"}).catch(()=>EisDocumentExport.failed("+JSONObject.quote(nonce)+"));})();";
        web.evaluateJavascript(js,null);
        main.postDelayed(() -> { if(nonce.equals(blobNonce)){blobNonce=null;toast("Не удалось подготовить документ. Попробуйте скачать его в браузере.");}},30000);
    }
    private final class BlobReceiver {
        @JavascriptInterface public void receive(String nonce, String name, String mime, String data) {
            main.post(() -> {
                if (blobNonce == null || !blobNonce.equals(nonce) || !UrlPolicy.trusted(web.getUrl())) return;
                blobNonce=null;
                if(data == null || data.length() > MAX_BLOB*4/3+2048) { toast("Документ слишком большой. Откройте его в браузере."); return; }
                try {
                    int comma=data.indexOf(','); if(comma<0)throw new IllegalArgumentException();
                    PendingDownload p=new PendingDownload(); p.data=Base64.decode(data.substring(comma+1),Base64.DEFAULT);
                    p.mime=mime == null || mime.isEmpty() ? "application/octet-stream" : mime;
                    String chosenName = name == null || name.equals("Документ") || name.isEmpty()
                        ? URLUtil.guessFileName("https://document/Документ",null,p.mime) : name;
                    p.name=UrlPolicy.safeName(chosenName);
                    chooseDestination(p);
                } catch(Exception e) { toast("Не удалось подготовить документ"); }
            });
        }
        @JavascriptInterface public void failed(String nonce) {
            main.post(() -> { if(blobNonce != null && blobNonce.equals(nonce)){blobNonce=null;toast("Не удалось подготовить документ. Попробуйте скачать его в браузере.");} });
        }
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request,result,data);
        if(request==PICK_FILE && filePicker!=null) {
            Uri[] uris=WebChromeClient.FileChooserParams.parseResult(result,data);
            if(uris!=null) for(Uri u:uris) if(!"content".equals(u.getScheme())) { uris=null; break; }
            filePicker.onReceiveValue(uris); filePicker=null;
        } else if(request==SAVE_FILE) {
            PendingDownload p=pending; pending=null;
            if(result==RESULT_OK && data!=null && data.getData()!=null && p!=null && "content".equals(data.getData().getScheme())) {
                Uri target=data.getData(); busySaving=true; toast("Сохраняем документ…");
                downloads.execute(() -> saveDownload(p,target));
            }
        }
    }

    private void saveDownload(PendingDownload p, Uri target) {
        String result="Документ сохранён";
        HttpURLConnection connection=null;
        try {
            InputStream input;
            if(p.data!=null) input=new java.io.ByteArrayInputStream(p.data);
            else {
                String next=p.url;
                for(int hops=0; ;hops++) {
                    URL u=new URL(next);
                    if(!"https".equals(u.getProtocol()) || u.getUserInfo()!=null || hops>5) throw new java.io.IOException();
                    connection=(HttpURLConnection)u.openConnection();
                    connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(20000); connection.setReadTimeout(60000);
                    if(p.userAgent!=null)connection.setRequestProperty("User-Agent",p.userAgent);
                    if(UrlPolicy.trusted(next) && p.cookie!=null)connection.setRequestProperty("Cookie",p.cookie);
                    int code=connection.getResponseCode();
                    if(code==301||code==302||code==303||code==307||code==308){
                        String location=connection.getHeaderField("Location");
                        if(location==null)throw new java.io.IOException();
                        next=new URL(u,location).toString(); connection.disconnect(); continue;
                    }
                    if(code<200||code>=300)throw new java.io.IOException();
                    String type=connection.getContentType();
                    if(type!=null && type.toLowerCase(java.util.Locale.ROOT).contains("text/html"))throw new java.io.IOException();
                    input=connection.getInputStream(); break;
                }
            }
            try(InputStream in=input; OutputStream out=getContentResolver().openOutputStream(target,"wt")) {
                if(out==null)throw new java.io.IOException();
                byte[] buffer=new byte[32768]; int n; long total=0;
                while((n=in.read(buffer))!=-1){total+=n;if(total>100L*1024*1024)throw new java.io.IOException();out.write(buffer,0,n);}
            }
        } catch(Exception e) {
            result="Не удалось сохранить документ. Проверьте соединение и повторите скачивание.";
            try{DocumentsContract.deleteDocument(getContentResolver(),target);}catch(Exception ignored){}
        } finally {
            if(connection!=null)connection.disconnect();
            final String message=result;
            main.post(() -> {busySaving=false;if(!isDestroyed())toast(message);});
        }
    }

    private void toast(String value) { Toast.makeText(this,value,Toast.LENGTH_LONG).show(); }
    @Override protected void onSaveInstanceState(Bundle out) { web.saveState(out); super.onSaveInstanceState(out); }
    @Override protected void onPause() { CookieManager.getInstance().flush(); web.onPause(); super.onPause(); }
    @Override protected void onResume() { super.onResume(); if(web!=null)web.onResume(); }
    @Override protected void onDestroy() {
        if(filePicker!=null){filePicker.onReceiveValue(null);filePicker=null;}
        main.removeCallbacksAndMessages(null);
        web.removeJavascriptInterface("EisDocumentExport"); web.stopLoading();
        ((android.view.ViewGroup)web.getParent()).removeView(web); web.destroy();
        downloads.shutdown(); super.onDestroy();
    }
}
