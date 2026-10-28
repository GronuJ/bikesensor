import SwiftUI

// Every ride on the phone and whether kiel.earth has it. Swipe left to delete.
struct RidesView: View {
    @EnvironmentObject private var rides: RideLog

    private var status: String {
        if RideLog.uploadToken == nil { return "no upload key in this build" }
        let n = rides.toSend.count
        if n > 0 { return "\(n) to send" }
        return rides.rides.isEmpty ? "no rides yet" : "all sent"
    }

    var body: some View {
        VStack(spacing: 0) {
            TopBar(title: "kiel.earth — rides") { Text(status) }
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
            VStack(spacing: 6) {
                if rides.toSend.count > 1 {
                    Button("send all now") { Task { await rides.sendAll() } }
                        .buttonStyle(OutlineButtonStyle(height: 52, size: 15))
                        .disabled(!rides.sending.isEmpty)
                }
                Text("a ride is deleted here 7 days after kiel.earth confirms it")
                    .font(.mono(12))
                    .foregroundStyle(Palette.dim)
                    .multilineTextAlignment(.center)
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 16)
        }
    }
}

private struct RideRow: View {
    let ride: Ride
    @EnvironmentObject private var rides: RideLog

    private var sending: Bool { rides.sending.contains(ride.id) }

    private var state: (String, Color) {
        switch ride.state {
        case .recording: return ("finishing…", Palette.dim)
        case .empty: return ("nothing to send", Palette.dim)
        case .sent: return ("sent", Palette.dim)
        case .ready: return (sending ? "sending…" : "not sent", Palette.ink)
        }
    }

    private var meta: String {
        var parts = ["\(Fmt.km(ride.distanceM)) km", Fmt.minutes(ride.durationS)]
        if ride.state != .empty { parts.append(Fmt.mb(ride.sendBytes)) }
        return parts.joined(separator: " · ")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(alignment: .firstTextBaseline, spacing: 12) {
                Text(Fmt.when(ride.startedAt))
                Spacer()
                Text(state.0).font(.mono(13)).foregroundStyle(state.1)
            }
            Text(meta).font(.mono(12)).foregroundStyle(Palette.dim)
            if ride.state == .ready && !sending {
                HStack(spacing: 12) {
                    Text(ride.lastError ?? "").font(.mono(12)).foregroundStyle(Palette.dim)
                    Spacer()
                    Button(ride.lastError == nil ? "send" : "retry") { Task { await rides.send(ride.id) } }
                        .buttonStyle(OutlineButtonStyle(height: 44, size: 13))
                        .frame(width: 96)
                }
                .padding(.top, 4)
            }
        }
    }
}
