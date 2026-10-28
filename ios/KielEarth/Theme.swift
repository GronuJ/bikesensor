import SwiftUI

// kiel.earth's palette (style.css :root): dark only, lowercase, monospace.
enum Palette {
    static let ground = Color(hex: 0x0a0a0a)
    static let ink = Color(hex: 0xe8e8e8)
    static let dim = Color(hex: 0x888888)
    static let rule = Color(hex: 0x262626)
    static let cut = Color(hex: 0x5c5c5c)
    static let red = Color(hex: 0xff3b3b)
}

extension Color {
    init(hex: UInt32) {
        self.init(red: Double((hex >> 16) & 0xff) / 255,
                  green: Double((hex >> 8) & 0xff) / 255,
                  blue: Double(hex & 0xff) / 255)
    }
}

extension Font {
    static func mono(_ size: CGFloat) -> Font { .system(size: size, design: .monospaced) }
}

struct Rule: View {
    var body: some View { Rectangle().fill(Palette.rule).frame(height: 1) }
}

// The site's top bar: page title, the map link, one status line.
struct TopBar<Status: View>: View {
    let title: String
    var showsMap = true
    @ViewBuilder let status: Status

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Text(title)
                Spacer()
                if showsMap {
                    Link(destination: URL(string: "https://kiel.earth/surface")!) {
                        Text("map ↗").foregroundStyle(Palette.dim)
                    }
                }
            }
            .font(.mono(13))
            .frame(minHeight: 44)
            status
                .font(.mono(12))
                .foregroundStyle(Palette.dim)
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.bottom, 10)
        }
        .padding(.horizontal, 20)
        .overlay(alignment: .bottom) { Rule() }
    }
}

// One <dt>/<dd> line of the design's info lists.
struct InfoRow: View {
    let term: String
    let value: String

    init(_ term: String, _ value: String) {
        self.term = term
        self.value = value
    }

    var body: some View {
        HStack(alignment: .firstTextBaseline, spacing: 12) {
            Text(term).foregroundStyle(Palette.dim).frame(width: 88, alignment: .leading)
            Text(value)
            Spacer(minLength: 0)
        }
        .font(.mono(13))
    }
}

struct OutlineButtonStyle: ButtonStyle {
    var height: CGFloat = 64
    var size: CGFloat = 17

    func makeBody(configuration: Configuration) -> some View {
        Body(configuration: configuration, height: height, size: size, filled: false)
    }

    fileprivate struct Body: View {
        let configuration: Configuration
        let height: CGFloat
        let size: CGFloat
        let filled: Bool
        @Environment(\.isEnabled) private var enabled

        var body: some View {
            let line = enabled ? Palette.ink : Palette.dim
            configuration.label
                .font(.mono(size))
                .tracking(size * 0.04)
                .foregroundStyle(filled && enabled ? Palette.ground : line)
                .frame(maxWidth: .infinity, minHeight: height)
                .background(filled && enabled ? Palette.ink : Color.clear)
                .overlay(Rectangle().stroke(line, lineWidth: 1))
                .contentShape(Rectangle())
                .opacity(configuration.isPressed ? 0.6 : 1)
        }
    }
}

struct FilledButtonStyle: ButtonStyle {
    var height: CGFloat = 56
    var size: CGFloat = 16

    func makeBody(configuration: Configuration) -> some View {
        OutlineButtonStyle.Body(configuration: configuration, height: height, size: size, filled: true)
    }
}

// Text-only, for "discard" and the small row actions.
struct QuietButtonStyle: ButtonStyle {
    var size: CGFloat = 13

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.mono(size))
            .foregroundStyle(Palette.dim)
            .frame(minHeight: 44)
            .contentShape(Rectangle())
            .opacity(configuration.isPressed ? 0.6 : 1)
    }
}

enum Fmt {
    static func km(_ metres: Double) -> String { String(format: "%.2f", metres / 1000) }

    // 00:23:41
    static func clock(_ seconds: Double) -> String {
        let s = max(0, Int(seconds))
        return String(format: "%02d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
    }

    // 24:12, or 1:04:12 past an hour
    static func duration(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s / 60 % 60, s % 60)
                         : String(format: "%d:%02d", s / 60, s % 60)
    }

    static func minutes(_ seconds: Double) -> String { "\(max(1, Int((seconds / 60).rounded()))) min" }

    static func mb(_ bytes: Int) -> String { String(format: "%.1f mb", Double(bytes) / 1_000_000) }

    static func count(_ n: Int) -> String {
        n.formatted(.number.locale(Locale(identifier: "en_US")))
    }

    // "28 oct, 08:51"
    static func when(_ date: Date) -> String { whenFormatter.string(from: date).lowercased() }

    // "today 08:51", "yesterday 17:40", "26 oct 18:12"
    static func relative(_ date: Date) -> String {
        let cal = Calendar.current
        let time = timeFormatter.string(from: date)
        if cal.isDateInToday(date) { return "today \(time)" }
        if cal.isDateInYesterday(date) { return "yesterday \(time)" }
        return dayFormatter.string(from: date).lowercased() + " " + time
    }

    private static func formatter(_ format: String) -> DateFormatter {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.dateFormat = format
        return f
    }

    private static let whenFormatter = formatter("d MMM, HH:mm")
    private static let timeFormatter = formatter("HH:mm")
    private static let dayFormatter = formatter("d MMM")
}
