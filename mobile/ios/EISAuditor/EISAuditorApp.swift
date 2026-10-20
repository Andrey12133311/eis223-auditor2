import SwiftUI
import WebKit
import SafariServices
import UniformTypeIdentifiers

enum AppSite {
    static let home = URL(string: "https://web-production-1c2ea.up.railway.app/")!
    static func trusted(_ url: URL?) -> Bool {
        guard let url else { return false }
        return url.scheme?.lowercased() == "https"
            && url.host?.lowercased() == home.host
            && url.user == nil && url.password == nil
            && (url.port == nil || url.port == 443)
    }
    static func external(_ url: URL) -> Bool {
        if url.scheme == "mailto" || url.scheme == "tel" { return true }
        return url.scheme?.lowercased() == "https" && url.host != nil && url.user == nil && url.password == nil
    }
}

@main
struct EISAuditorApp: App {
    var body: some Scene {
        WindowGroup { AuditorView().preferredColorScheme(.light) }
    }
}

struct AuditorView: View {
    @StateObject private var browser = AuditorBrowser()
    var body: some View {
        NavigationStack {
            ZStack {
                WebSurface(browser: browser)
                if browser.loading {
                    VStack { ProgressView().progressViewStyle(.linear).tint(Color(red: 0.09, green: 0.37, blue: 0.57)); Spacer() }
                }
                if browser.loadFailed {
                    VStack(spacing: 20) {
                        Image(systemName: "wifi.exclamationmark").font(.system(size: 38)).foregroundStyle(.secondary)
                        Text("Не удалось открыть программу").font(.title2).multilineTextAlignment(.center)
                        Text("Проверьте интернет и повторите попытку. Ваши данные сохраняются на сервере.")
                            .font(.body).foregroundStyle(.secondary).multilineTextAlignment(.center)
                        Button("Повторить") { browser.retry() }.buttonStyle(.borderedProminent)
                        Button("Открыть в Safari") { browser.openInSafari() }
                    }.padding(28).frame(maxWidth: .infinity, maxHeight: .infinity).background(Color.white)
                }
            }
            .navigationTitle("ЕИС Аудитор")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .navigationBarLeading) {
                    Button { browser.goBack() } label: { Image(systemName: "chevron.left") }
                        .disabled(!browser.canGoBack).accessibilityLabel("Назад")
                }
                ToolbarItem(placement: .navigationBarTrailing) {
                    Menu {
                        Button("Главная страница", systemImage: "house") { browser.home() }
                        Button("Личный кабинет", systemImage: "person.crop.circle") { browser.cabinet() }
                        Button("Обновить", systemImage: "arrow.clockwise") { browser.retry() }
                        Button("Открыть в Safari", systemImage: "safari") { browser.openInSafari() }
                        Button("Поделиться ссылкой", systemImage: "square.and.arrow.up") { browser.shareLink() }
                    } label: { Image(systemName: "ellipsis.circle") }.accessibilityLabel("Меню приложения")
                }
            }
            .alert("ЕИС Аудитор", isPresented: Binding(get: { browser.message != nil }, set: { if !$0 { browser.message = nil } })) {
                Button("Понятно", role: .cancel) { browser.message = nil }
            } message: { Text(browser.message ?? "") }
        }
        .tint(Color(red: 0.09, green: 0.37, blue: 0.57))
    }
}

struct WebSurface: UIViewControllerRepresentable {
    let browser: AuditorBrowser
    func makeUIViewController(context: Context) -> BrowserController {
        let vc = BrowserController(browser: browser)
        browser.presenter = vc
        return vc
    }
    func updateUIViewController(_ uiViewController: BrowserController, context: Context) { }
}

final class BrowserController: UIViewController {
    let browser: AuditorBrowser
    init(browser: AuditorBrowser) { self.browser = browser; super.init(nibName: nil, bundle: nil) }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func viewDidLoad() {
        super.viewDidLoad()
        let web = browser.webView
        web.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(web)
        NSLayoutConstraint.activate([
            web.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            web.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            web.topAnchor.constraint(equalTo: view.topAnchor),
            web.bottomAnchor.constraint(equalTo: view.bottomAnchor)
        ])
        if web.url == nil { browser.home() }
    }
}

final class AuditorBrowser: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate, WKDownloadDelegate {
    @Published var loading = true
    @Published var canGoBack = false
    @Published var loadFailed = false
    @Published var message: String?
    weak var presenter: UIViewController?
    let webView: WKWebView
    private var wantsCabinet = false
    private var lastURL = AppSite.home
    private var urls: [ObjectIdentifier: URL] = [:]
    private var observations: [NSKeyValueObservation] = []

    override init() {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = .default()
        configuration.defaultWebpagePreferences.allowsContentJavaScript = true
        configuration.applicationNameForUserAgent = "EIS223Mobile/1.0.0"
        webView = WKWebView(frame: .zero, configuration: configuration)
        super.init()
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.allowsBackForwardNavigationGestures = true
        webView.scrollView.keyboardDismissMode = .interactive
        observations.append(webView.observe(\.canGoBack, options: [.new]) { [weak self] _, change in
            DispatchQueue.main.async { self?.canGoBack = change.newValue ?? false }
        })
    }

    func home() { load(AppSite.home) }
    func cabinet() { wantsCabinet = true; home() }
    func retry() { load(lastURL) }
    func goBack() { if webView.canGoBack { loadFailed = false; webView.goBack() } }
    private func load(_ url: URL) {
        guard AppSite.trusted(url) else { return }
        loadFailed = false; loading = true; lastURL = url
        webView.load(URLRequest(url: url))
    }
    func openInSafari() { UIApplication.shared.open(lastURL) }
    func shareLink() { share([AppSite.trusted(webView.url) ? (webView.url ?? AppSite.home) : AppSite.home]) }
    private func share(_ items: [Any]) {
        guard let presenter else { return }
        let sheet = UIActivityViewController(activityItems: items, applicationActivities: nil)
        sheet.popoverPresentationController?.sourceView = presenter.view
        sheet.popoverPresentationController?.sourceRect = CGRect(x: presenter.view.bounds.midX, y: presenter.view.bounds.midY, width: 1, height: 1)
        presenter.present(sheet, animated: true)
    }
    private func openExternal(_ url: URL) {
        guard AppSite.external(url) else { message = "Эта ссылка не поддерживается"; return }
        if url.scheme == "https", let presenter {
            presenter.present(SFSafariViewController(url: url), animated: true)
        } else { UIApplication.shared.open(url) }
    }

    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = action.request.url else { decisionHandler(.cancel); return }
        if url.scheme == "blob" && AppSite.trusted(webView.url) {
            decisionHandler(action.shouldPerformDownload ? .download : .allow); return
        }
        if AppSite.trusted(url) {
            decisionHandler(action.shouldPerformDownload ? .download : .allow)
        } else {
            decisionHandler(.cancel)
            if action.targetFrame?.isMainFrame != false { openExternal(url) }
        }
    }
    func webView(_ webView: WKWebView, decidePolicyFor response: WKNavigationResponse, decisionHandler: @escaping (WKNavigationResponsePolicy) -> Void) {
        let type = response.response.mimeType ?? ""
        let isDocument = ["application/pdf", "application/vnd.oasis.opendocument.text", "application/octet-stream", "application/zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"].contains(type)
        let attachment = (response.response as? HTTPURLResponse)?.value(forHTTPHeaderField: "Content-Disposition")?.lowercased().contains("attachment") ?? false
        if response.isForMainFrame && (!response.canShowMIMEType || isDocument || attachment) {
            decisionHandler(.download)
        } else if response.isForMainFrame && ((response.response as? HTTPURLResponse)?.statusCode ?? 200) >= 500 {
            decisionHandler(.cancel); loading = false; loadFailed = true
        } else { decisionHandler(.allow) }
    }
    func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
        loading = true; loadFailed = false
    }
    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading = false; loadFailed = false
        if let url = webView.url, AppSite.trusted(url) { lastURL = url }
        if wantsCabinet {
            wantsCabinet = false
            webView.evaluateJavaScript("(function(){var b=document.getElementById('eis223-toggle-cabinet');if(b&&b.getAttribute('aria-expanded')!=='true')b.click();var p=document.getElementById('eis223-cabinet');if(p)p.scrollIntoView({block:'start'});})()")
        }
    }
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) { handle(error) }
    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { handle(error) }
    private func handle(_ error: Error) {
        // A cancelled navigation also occurs when WebKit turns a response into a download.
        if (error as NSError).code == NSURLErrorCancelled { loading = false; return }
        loading = false; loadFailed = true
    }
    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { retry() }
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration, for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let url = action.request.url {
            if AppSite.trusted(url) { webView.load(action.request) }
            else if url.scheme == "blob" && AppSite.trusted(webView.url) { webView.load(action.request) }
            else { openExternal(url) }
        }
        return nil
    }
    func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage text: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
        guard let presenter else { completionHandler(); return }
        let alert = UIAlertController(title: "ЕИС Аудитор", message: text, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "ОК", style: .default) { _ in completionHandler() })
        presenter.present(alert, animated: true)
    }
    func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage text: String, initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
        guard let presenter else { completionHandler(false); return }
        let alert = UIAlertController(title: "ЕИС Аудитор", message: text, preferredStyle: .alert)
        alert.addAction(UIAlertAction(title: "Подтвердить", style: .default) { _ in completionHandler(true) })
        alert.addAction(UIAlertAction(title: "Отмена", style: .cancel) { _ in completionHandler(false) })
        presenter.present(alert, animated: true)
    }
    func webView(_ webView: WKWebView, navigationAction: WKNavigationAction, didBecome download: WKDownload) { download.delegate = self }
    func webView(_ webView: WKWebView, navigationResponse: WKNavigationResponse, didBecome download: WKDownload) { download.delegate = self }
    func download(_ download: WKDownload, decideDestinationUsing response: URLResponse, suggestedFilename: String, completionHandler: @escaping (URL?) -> Void) {
        let cleanName = (suggestedFilename as NSString).lastPathComponent
            .components(separatedBy: .controlCharacters).joined()
        let directory = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            let destination = directory.appendingPathComponent(cleanName.isEmpty ? "Документ" : cleanName)
            urls[ObjectIdentifier(download)] = destination
            completionHandler(destination)
        } catch { completionHandler(nil); message = "Не удалось подготовить сохранение документа" }
    }
    func downloadDidFinish(_ download: WKDownload) {
        loading = false
        if let url = urls.removeValue(forKey: ObjectIdentifier(download)) { share([url]) }
    }
    func download(_ download: WKDownload, didFailWithError error: Error, resumeData: Data?) {
        loading = false
        if let url = urls.removeValue(forKey: ObjectIdentifier(download)) { try? FileManager.default.removeItem(at: url.deletingLastPathComponent()) }
        message = "Не удалось скачать документ. Проверьте соединение и повторите попытку."
    }
}
