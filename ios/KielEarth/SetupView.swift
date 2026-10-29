import SwiftUI

// First start: consent to share, then location. The design's third step, the
// knock test for the mount, is left out for now.
struct SetupView: View {
    let done: () -> Void

    @EnvironmentObject private var recorder: Recorder
    @AppStorage("consented") private var consented = false

    private var locationState: String {
        switch recorder.authorization {
        case .authorizedWhenInUse, .authorizedAlways: return recorder.precise ? "allowed" : "approximate only"
        case .denied, .restricted: return "not allowed"
        default: return "not yet"
        }
    }

    private var ready: Bool { consented && recorder.mayRecord && recorder.precise }

    var body: some View {
        VStack(spacing: 0) {
            TopBar(showsMap: false)
            VStack(alignment: .leading, spacing: 0) {
                Spacer(minLength: 20)
                VStack(alignment: .leading, spacing: 0) {
                    step(1, "share", state: consented ? "sharing" : "not yet", on: consented,
                         text: "rides go to kiel.earth/surface, without the 100 m around start and end.") {
                        HStack(spacing: 0) {
                            Toggle("share my rides", isOn: $consented).toggleStyle(CheckboxStyle())
                            Text(" · ").foregroundStyle(Palette.dim)
                            Link(destination: URL(string: "https://kiel.earth/datenschutz")!) {
                                Text("datenschutz").underline().foregroundStyle(Palette.dim)
                            }
                        }
                        .font(.mono(13))
                    }
                    step(2, "location", state: locationState, on: recorder.mayRecord && recorder.precise,
                         text: "precise, so rides land on the right street.") {
                        switch recorder.authorization {
                        case .notDetermined:
                            Button("allow") { recorder.requestPermission() }
                                .buttonStyle(OutlineButtonStyle(height: 44, size: 13))
                                .fixedSize()
                        case .denied, .restricted:
                            settingsButton
                        default:
                            if !recorder.precise { settingsButton }
                        }
                    }
                    Rule()
                }
                Spacer(minLength: 20)
                Button("done", action: done)
                    .buttonStyle(FilledButtonStyle())
                    .disabled(!ready)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 24)
        }
    }

    private var settingsButton: some View {
        Button("open settings") {
            if let url = URL(string: UIApplication.openSettingsURLString) { UIApplication.shared.open(url) }
        }
        .buttonStyle(OutlineButtonStyle(height: 44, size: 13))
        .fixedSize()
    }

    private func step<Action: View>(_ n: Int, _ title: String, state: String, on: Bool, text: String,
                                    @ViewBuilder action: () -> Action) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("\(n)  \(title)")
                Spacer()
                Text(state).font(.mono(13)).foregroundStyle(on ? Palette.ink : Palette.dim)
            }
            Text(text).font(.mono(12)).foregroundStyle(Palette.dim).fixedSize(horizontal: false, vertical: true)
            action()
        }
        .padding(.vertical, 14)
        .overlay(alignment: .top) { Rule() }
    }
}

struct CheckboxStyle: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        Button { configuration.isOn.toggle() } label: {
            HStack(spacing: 10) {
                ZStack {
                    Rectangle().stroke(Palette.ink, lineWidth: 1)
                    if configuration.isOn {
                        Rectangle().fill(Palette.ink).padding(4)
                    }
                }
                .frame(width: 20, height: 20)
                configuration.label
            }
            .frame(minHeight: 44)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .accessibilityValue(configuration.isOn ? "on" : "off")
    }
}
