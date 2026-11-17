import SwiftUI

/// The iOS shell of the mini app. The agent and its app run on your computer (agent_kit.py run --app ...);
/// this app opens the link the kit prints and shows the page full screen, the way an installed app looks.
@main
struct MiniAppApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}

struct RootView: View {
    @AppStorage("miniapp.link") private var link = ""

    var body: some View {
        if let url = AppLink.url(from: link) {
            AppScreen(url: url) { link = "" }
        } else {
            LinkScreen { link = $0 }
        }
    }
}

enum AppLink {
    /// Accepts the bare link or the whole line the kit prints ("[app] open https://...?k=...").
    static func url(from text: String) -> URL? {
        guard let range = text.range(of: #"https?://[^\s"'<>]+"#, options: .regularExpression),
              let url = URL(string: String(text[range])),
              let scheme = url.scheme, ["http", "https"].contains(scheme),
              url.host != nil else { return nil }
        return url
    }
}
