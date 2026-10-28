import SwiftUI

@main
struct KielEarthApp: App {
    @StateObject private var recorder = Recorder()
    @StateObject private var rides = RideLog()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(recorder)
                .environmentObject(rides)
                .preferredColorScheme(.dark)
                .tint(Palette.ink)
        }
    }
}

struct RootView: View {
    enum Tab { case ride, rides }

    @EnvironmentObject private var recorder: Recorder
    @EnvironmentObject private var rides: RideLog
    @Environment(\.scenePhase) private var scenePhase
    @AppStorage("setupDone") private var setupDone = false
    @State private var tab = Tab.ride

    var body: some View {
        ZStack {
            Palette.ground.ignoresSafeArea()
            content
        }
        .font(.mono(14))
        .foregroundStyle(Palette.ink)
        .task {
            let log = rides
            recorder.onFinished = { id, auto in
                Task { await log.finish(id, autoStopped: auto, showSummary: true) }
            }
            recorder.setIdle(scenePhase == .active && setupDone)
            await rides.finishInterrupted()
            rides.prune()
        }
        .onChange(of: scenePhase) { _, phase in
            recorder.setIdle(phase == .active && setupDone)
        }
    }

    @ViewBuilder private var content: some View {
        if !setupDone {
            SetupView {
                setupDone = true
                recorder.setIdle(true)
            }
        } else if recorder.isRecording {
            RidingView()
        } else if rides.finishing {
            VStack(spacing: 0) {
                TopBar(title: "kiel.earth — surface", showsMap: false) { Text("finishing the ride") }
                Spacer()
                ProgressView().tint(Palette.dim)
                Spacer()
            }
        } else if let summary = rides.summary {
            SummaryView(summary: summary) { showRides in
                rides.summary = nil
                tab = showRides ? .rides : .ride
            }
        } else {
            VStack(spacing: 0) {
                switch tab {
                case .ride: HomeView(start: startRide)
                case .rides: RidesView()
                }
                footer
            }
        }
    }

    private var footer: some View {
        let n = rides.toSend.count
        return HStack(spacing: 20) {
            tabButton("ride", .ride)
            tabButton(n > 0 ? "rides · \(n)" : "rides", .rides)
            Spacer()
        }
        .font(.mono(13))
        .padding(.horizontal, 20)
        .overlay(alignment: .top) { Rule() }
    }

    private func tabButton(_ label: String, _ target: Tab) -> some View {
        Button(label) { tab = target }
            .buttonStyle(.plain)
            .foregroundStyle(tab == target ? Palette.ink : Palette.dim)
            .frame(minHeight: 44)
            .accessibilityAddTraits(tab == target ? .isSelected : [])
    }

    private func startRide() {
        guard let ride = try? rides.begin() else { return }
        do {
            try recorder.start(rideID: ride.id, file: ride.file)
        } catch {
            rides.remove(ride.id)
        }
    }
}
