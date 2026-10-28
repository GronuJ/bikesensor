import CoreLocation
import SwiftUI

struct HomeView: View {
    let start: () -> Void

    @EnvironmentObject private var recorder: Recorder

    // Only what stops a ride from recording or being sent.
    private var problem: String? {
        if !recorder.motionAvailable { return "no accelerometer" }
        if RideLog.uploadToken == nil { return "no upload key in this build" }
        return nil
    }

    var body: some View {
        VStack(spacing: 0) {
            TopBar()
            Spacer(minLength: 12)
            Plate().opacity(0.55).frame(maxWidth: .infinity)
            Spacer(minLength: 12)
            VStack(spacing: 10) {
                if let problem {
                    Text(problem).font(.mono(12)).foregroundStyle(Palette.dim)
                }
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
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 20)
        }
    }
}
