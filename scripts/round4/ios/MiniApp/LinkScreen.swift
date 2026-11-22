import SwiftUI
import UIKit

/// First run: paste the link the kit printed. It carries the app's access code; the page keeps it in a cookie.
struct LinkScreen: View {
    let onOpen: (String) -> Void
    @State private var text = ""
    @State private var invalid = false

    var body: some View {
        VStack(spacing: 0) {
            Spacer(minLength: 24)
            Image("Mark")
                .resizable()
                .frame(width: 72, height: 72)
                .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
            Text("에이전트 앱 연결")
                .font(.title2.weight(.semibold))
                .padding(.top, 18)
            Text("에이전트를 실행한 터미널에 나온 링크를 붙여 넣으세요.\n[app] open 으로 시작하는 줄을 통째로 넣어도 됩니다.")
                .font(.subheadline)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .padding(.top, 6)
                .padding(.horizontal, 8)

            TextField("https://...trycloudflare.com/?k=...", text: $text, axis: .vertical)
                .font(.callout.monospaced())
                .lineLimit(1...4)
                .textInputAutocapitalization(.never)
                .autocorrectionDisabled()
                .keyboardType(.URL)
                .padding(14)
                .background(Color(.secondarySystemBackground), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
                .padding(.top, 24)

            if invalid {
                Text("링크를 찾지 못했습니다. http:// 또는 https:// 로 시작하는 주소가 필요합니다.")
                    .font(.footnote)
                    .foregroundStyle(.secondary)
                    .padding(.top, 8)
            }

            HStack(spacing: 10) {
                Button {
                    text = UIPasteboard.general.string ?? text
                    invalid = false
                } label: {
                    Text("붙여넣기").frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.bordered)

                Button {
                    if AppLink.url(from: text) != nil { onOpen(text) } else { invalid = true }
                } label: {
                    Text("열기").fontWeight(.semibold).frame(maxWidth: .infinity, minHeight: 48)
                }
                .buttonStyle(.borderedProminent)
            }
            .tint(.primary)
            .padding(.top, 14)
            Spacer(minLength: 24)
        }
        .padding(.horizontal, 20)
        .frame(maxWidth: 480)
    }
}
