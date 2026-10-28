import CoreLocation
import SwiftUI

struct HomeView: View {
    let start: () -> Void

    @EnvironmentObject private var recorder: Recorder
    @EnvironmentObject private var rides: RideLog

    private var status: String {
        guard let last = rides.rides.first(where: { $0.state != .recording }) else { return "not recording · no rides yet" }
        return "not recording · last ride \(Fmt.relative(last.startedAt))"
    }

    private var gps: String {
        switch recorder.authorization {
        case .denied, .restricted: return "not allowed"
        case .notDetermined: return "not asked yet"
        default: break
        }
        if !recorder.precise { return "approximate only" }
        guard let loc = recorder.lastLocation, -loc.timestamp.timeIntervalSinceNow < 10 else { return "searching…" }
        return "fix · ±\(Int(loc.horizontalAccuracy.rounded())) m"
    }

    private var toSend: String {
        switch rides.toSend.count {
        case 0: return "nothing"
        case 1: return "1 ride"
        case let n: return "\(n) rides"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            TopBar(title: "kiel.earth — surface") { Text(status) }
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("ready").font(.mono(12)).foregroundStyle(Palette.dim)
                    Text("phone on the bar,\nthen ride.").font(.mono(26))
                }
                Spacer(minLength: 12)
                Plate().opacity(0.55).frame(maxWidth: .infinity)
                Spacer(minLength: 12)
                VStack(alignment: .leading, spacing: 2) {
                    InfoRow("gps", gps)
                    InfoRow("motion", recorder.motionAvailable ? "\(Int(Recorder.sampleHz)) hz" : "no accelerometer")
                    InfoRow("to send", toSend)
                    if RideLog.uploadToken == nil {
                        InfoRow("upload", "no key in this build")
                    }
                }
                Spacer(minLength: 12)
                VStack(spacing: 10) {
                    if recorder.mayRecord && recorder.precise {
                        Button("start ride", action: start)
                            .buttonStyle(OutlineButtonStyle())
                            .disabled(!recorder.motionAvailable)
                    } else {
                        Button("allow precise location") {
                            if recorder.authorization == .notDetermined {
                                recorder.requestPermission()
                            } else if let url = URL(string: UIApplication.openSettingsURLString) {
                                UIApplication.shared.open(url)
                            }
                        }
                        .buttonStyle(OutlineButtonStyle())
                    }
                    Text("stops by itself after 3 min standing still")
                        .font(.mono(12))
                        .foregroundStyle(Palette.dim)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 24)
            .padding(.bottom, 20)
        }
    }
}
