import SwiftUI
import WebKit

/// The mini app full screen. The page draws its own safe areas (viewport-fit=cover), so the web view runs edge to
/// edge. Long-press with two fingers for the menu (reload, change link).
struct AppScreen: View {
    let url: URL
    let onReset: () -> Void
    @StateObject private var web = WebState()
    @State private var menu = false

    var body: some View {
        ZStack {
            Color(.systemBackground).ignoresSafeArea()
            WebView(url: url, state: web) { menu = true }
                .ignoresSafeArea()
                .opacity(web.failed == nil ? 1 : 0)
            if let error = web.failed {
                VStack(spacing: 12) {
                    Text("에이전트에 연결할 수 없습니다").font(.headline)
                    Text(error)
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                    Text("컴퓨터에서 kit가 돌고 있는지, 링크가 지금 터미널에 나온 것과 같은지 확인하세요.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                        .multilineTextAlignment(.center)
                    HStack(spacing: 10) {
                        Button("링크 바꾸기", action: onReset).buttonStyle(.bordered)
                        Button("다시 시도") { web.reload() }.buttonStyle(.borderedProminent)
                    }
                    .tint(.primary)
                    .padding(.top, 6)
                }
                .padding(24)
            } else if web.loading && !web.loadedOnce {
                ProgressView()
            }
        }
        .confirmationDialog("미니앱", isPresented: $menu) {
            Button("새로고침") { web.reload() }
            Button("링크 바꾸기", role: .destructive, action: onReset)
        } message: {
            Text(url.host ?? url.absoluteString)
        }
    }
}

final class WebState: NSObject, ObservableObject, WKNavigationDelegate, WKUIDelegate {
    @Published var loading = true
    @Published var loadedOnce = false
    @Published var failed: String?
    weak var view: WKWebView?

    func reload() {
        failed = nil
        loading = true
        if let v = view {
            if v.url == nil, let u = v.backForwardList.currentItem?.initialURL { v.load(URLRequest(url: u)) } else { v.reload() }
        }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        loading = false
        loadedOnce = true
        failed = nil
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) { fail(error) }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        fail(error)
    }

    private func fail(_ error: Error) {
        let e = error as NSError
        if e.domain == NSURLErrorDomain && e.code == NSURLErrorCancelled { return }
        loading = false
        failed = e.localizedDescription
    }

    /// The app stays on its own host; any other link opens in Safari.
    func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let target = action.request.url else { return decisionHandler(.cancel) }
        let home = webView.url?.host ?? action.request.mainDocumentURL?.host
        if action.targetFrame?.isMainFrame == false || target.host == home || home == nil || target.scheme == "about" {
            return decisionHandler(.allow)
        }
        if action.navigationType == .linkActivated {
            UIApplication.shared.open(target)
            return decisionHandler(.cancel)
        }
        decisionHandler(.allow)
    }

    /// target=_blank and window.open: open in Safari.
    func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                 for action: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
        if let u = action.request.url { UIApplication.shared.open(u) }
        return nil
    }

    func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }
}

struct WebView: UIViewRepresentable {
    let url: URL
    let state: WebState
    let onMenu: () -> Void

    func makeCoordinator() -> Coordinator { Coordinator(onMenu: onMenu) }

    func makeUIView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .default()               // the access cookie survives restarts
        config.allowsInlineMediaPlayback = true
        config.applicationNameForUserAgent = "MiniAppShell/1"
        let view = WKWebView(frame: .zero, configuration: config)
        view.navigationDelegate = state
        view.uiDelegate = state
        view.isOpaque = false
        view.backgroundColor = .systemBackground
        view.scrollView.backgroundColor = .systemBackground
        view.scrollView.contentInsetAdjustmentBehavior = .never   // the page handles env(safe-area-inset-*)
        view.scrollView.bounces = false
        view.allowsBackForwardNavigationGestures = false
        #if DEBUG
        if #available(iOS 16.4, *) { view.isInspectable = true }  // Safari, Develop menu, to debug the page
        #endif
        let press = UILongPressGestureRecognizer(target: context.coordinator, action: #selector(Coordinator.press(_:)))
        press.numberOfTouchesRequired = 2
        press.minimumPressDuration = 0.6
        view.addGestureRecognizer(press)
        state.view = view
        view.load(URLRequest(url: url))
        return view
    }

    func updateUIView(_ view: WKWebView, context: Context) {}

    final class Coordinator: NSObject {
        let onMenu: () -> Void
        init(onMenu: @escaping () -> Void) { self.onMenu = onMenu }
        @objc func press(_ g: UILongPressGestureRecognizer) { if g.state == .began { onMenu() } }
    }
}
