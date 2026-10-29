import SwiftUI

// Every ride on the phone and whether kiel.earth has it. Swipe left to delete;
// a sent ride goes by itself after 7 days.
struct RidesView: View {
    @EnvironmentObject private var rides: RideLog

    var body: some View {
        VStack(spacing: 0) {
            TopBar()
            if rides.rides.isEmpty {
                Spacer()
            } else {
                List {
                    ForEach(rides.rides) { ride in
                        RideRow(ride: ride)
                            .listRowBackground(Palette.ground)
                            .listRowSeparatorTint(Palette.rule)
                            .listRowInsets(EdgeInsets(top: 12, leading: 20, bottom: 12, trailing: 20))
                            .deleteDisabled(ride.state == .recording || rides.sending.contains(ride.id))
                    }
                    .onDelete { offsets in
                        for id in offsets.map({ rides.rides[$0].id }) { rides.remove(id) }
                    }
                }
                .listStyle(.plain)
                .scrollContentBackground(.hidden)
            }
            if rides.toSend.count > 1 {
                Button("send all") { Task { await rides.sendAll() } }
                    .buttonStyle(OutlineButtonStyle(height: 52, size: 15))
                    .disabled(!rides.sending.isEmpty)
                    .padding(.horizontal, 20)
                    .padding(.vertical, 16)
            }
        }
    }
}

private struct RideRow: View {
    let ride: Ride
    @EnvironmentObject private var rides: RideLog

    private var sending: Bool { rides.sending.contains(ride.id) }

    // What the row says when there is no send button.
    private var state: String {
        switch ride.state {
        case .recording: return "…"
        case .empty: return "–"
        case .sent: return "sent"
        case .ready: return "sending…"
        }
    }

    private var meta: String { "\(Fmt.km(ride.distanceM)) km · \(Fmt.minutes(ride.durationS))" }

    var body: some View {
        HStack(spacing: 12) {
            VStack(alignment: .leading, spacing: 2) {
                Text(Fmt.when(ride.startedAt))
                Text(meta).font(.mono(12)).foregroundStyle(Palette.dim)
                if let e = ride.lastError, ride.state == .ready, !sending {
                    Text(e).font(.mono(12)).foregroundStyle(Palette.dim)
                }
            }
            Spacer()
            if ride.state == .ready && !sending {
                Button(ride.lastError == nil ? "send" : "retry") { Task { await rides.send(ride.id) } }
                    .buttonStyle(OutlineButtonStyle(height: 44, size: 13))
                    .frame(width: 96)
            } else {
                Text(state).font(.mono(13)).foregroundStyle(Palette.dim)
            }
        }
    }
}
