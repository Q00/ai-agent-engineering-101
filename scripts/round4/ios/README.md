# Mini app for iOS

A small SwiftUI app that shows your agent's mini app full screen. The agent and its app keep running on your computer
(`agent_kit.py run ... --app 8800`); this app only opens the link the kit prints.

1. Run the kit with `--app-tunnel` (a phone anywhere) or `--app-host 0.0.0.0` (the same Wi-Fi). In the simulator the
   plain `http://127.0.0.1:8800/?k=...` link works too.
2. Open `MiniApp.xcodeproj` in Xcode and Run (a simulator, or your iPhone after choosing your Team and your own bundle
   identifier in Signing & Capabilities).
3. Paste the link (or the whole `[app] open ...` line) and tap 열기. The app remembers it and keeps the access cookie.

Long-press with two fingers for the menu: reload, or change the link. Links to other sites open in Safari. A Debug build
can be inspected from Safari's Develop menu.

Requires iOS 16 or later. `Info.plist` allows plain `http://` in the web view (`NSAllowsArbitraryLoadsInWebContent`)
for the Wi-Fi link and asks for local network access the first time it opens one.
