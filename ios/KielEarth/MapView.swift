import SwiftUI
import WebKit

// The map tab: kiel.earth/surface itself, in its app mode (?app), which drops
// the site's top bar, hint and legal links. The app draws nothing of the map
// on its own, so it always shows what the site shows.
struct MapView: View {
    var body: some View {
        VStack(spacing: 0) {
            TopBar()
            SurfacePage()
                .onAppear { SurfaceWeb.shared.loadIfStale() }
        }
    }
}

private struct SurfacePage: UIViewRepresentable {
    func makeUIView(context: Context) -> WKWebView { SurfaceWeb.shared.view }
    func updateUIView(_ view: WKWebView, context: Context) {}
}

// One web view for the app's life, so switching tabs keeps the map where it
// was. It loads again when the tab comes back after five minutes, the time
// kiel.earth caches the line for.
@MainActor
final class SurfaceWeb: NSObject, WKNavigationDelegate {
    static let shared = SurfaceWeb()
    static let url = URL(string: "https://kiel.earth/surface?app")!

    let view: WKWebView
    private var loadedAt: Date?

    override init() {
        view = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        super.init()
        view.navigationDelegate = self
        // The page's own black, not a white flash, while it loads.
        view.isOpaque = false
        view.backgroundColor = UIColor(Palette.ground)
        view.scrollView.backgroundColor = UIColor(Palette.ground)
        // The map pans and zooms itself; the page around it must not move.
        view.scrollView.isScrollEnabled = false
        view.scrollView.bounces = false
    }

    func loadIfStale() {
        if let t = loadedAt, Date().timeIntervalSince(t) < 300 { return }
        loadedAt = Date()
        view.load(URLRequest(url: Self.url))
    }

    // Anything that would leave the map opens in Safari instead, so the tab
    // can never end up on a page with no way back.
    func webView(_ webView: WKWebView,
                 decidePolicyFor action: WKNavigationAction) async -> WKNavigationActionPolicy {
        guard action.navigationType == .linkActivated, let url = action.request.url else { return .allow }
        UIApplication.shared.open(url, options: [:], completionHandler: nil)
        return .cancel
    }

    // Offline, or the site down: try again the next time the tab opens.
    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!,
                 withError error: Error) {
        loadedAt = nil
    }
}
