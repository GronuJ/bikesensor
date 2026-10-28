import SwiftUI

// Right after a ride: the track with the cut parts dashed, the numbers, and
// send / later / discard. The design's on-phone surface preview is left out;
// the site does the processing.
struct SummaryView: View {
    let summary: RideLog.Summary
    let close: (_ showRides: Bool) -> Void

    @EnvironmentObject private var rides: RideLog
    @State private var confirmDiscard = false

    private var ride: Ride? { rides.ride(summary.id) }
    private var sending: Bool { rides.sending.contains(summary.id) }

    private var stateLine: String {
        guard let r = ride else { return "" }
        switch r.state {
        case .sent: return "sent"
        case .empty: return "nothing to send"
        default: return sending ? "sending…" : "not sent yet"
        }
    }

    var body: some View {
        VStack(spacing: 0) {
            TopBar(title: "kiel.earth — surface") { Text("ride \(summary.id) · \(stateLine)") }
            if let r = ride {
                content(r)
            } else {
                Spacer()
            }
        }
        .confirmationDialog("discard this ride?", isPresented: $confirmDiscard, titleVisibility: .visible) {
            Button("discard", role: .destructive) {
                rides.remove(summary.id)
                close(false)
            }
        } message: {
            Text("it is deleted from this phone and never sent.")
        }
    }

    private func content(_ r: Ride) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            VStack(alignment: .leading, spacing: 6) {
                TrackView(points: summary.track)
                    .frame(height: 230)
                    .overlay(Rectangle().stroke(Palette.rule, lineWidth: 1))
                Text("dashed: within 300 m of start and end, cut before sending")
                    .font(.mono(12))
                    .foregroundStyle(Palette.dim)
            }
            Spacer(minLength: 12)
            Grid(alignment: .leading, horizontalSpacing: 12, verticalSpacing: 12) {
                GridRow {
                    stat("distance", "\(Fmt.km(r.distanceM)) km")
                    stat("time", Fmt.duration(r.durationS))
                    stat("avg", r.durationS > 0 ? String(format: "%.1f km/h", r.distanceM / r.durationS * 3.6) : "–")
                }
                GridRow {
                    stat("samples", Fmt.count(r.samples))
                    stat("gps fixes", Fmt.count(r.fixes))
                    stat("to send", r.state == .empty ? "–" : Fmt.mb(r.sendBytes))
                }
            }
            Spacer(minLength: 12)
            VStack(alignment: .leading, spacing: 4) {
                if r.autoStopped {
                    Text("stopped by itself after 3 min standing still.")
                }
                if r.state == .empty {
                    Text("nothing is left outside the 300 m around start and end, so there is nothing to send.")
                } else {
                    Text("\(Fmt.km(r.keptDistanceM)) km of it goes to kiel.earth.")
                }
                if let e = r.lastError, r.state != .sent {
                    Text(e).foregroundStyle(Palette.ink)
                }
            }
            .font(.mono(12))
            .foregroundStyle(Palette.dim)
            .fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 12)
            buttons(r)
        }
        .padding(.horizontal, 20)
        .padding(.top, 20)
        .padding(.bottom, 24)
    }

    @ViewBuilder private func buttons(_ r: Ride) -> some View {
        VStack(spacing: 8) {
            if r.state == .ready {
                Button(sending ? "sending…" : "send to kiel.earth") {
                    Task {
                        await rides.send(summary.id)
                        if rides.ride(summary.id)?.state == .sent { close(true) }
                    }
                }
                .buttonStyle(FilledButtonStyle())
                .disabled(sending)
                HStack(spacing: 8) {
                    Button("later") { close(false) }
                        .buttonStyle(OutlineButtonStyle(height: 44, size: 13))
                        .disabled(sending)
                    Button("discard") { confirmDiscard = true }
                        .buttonStyle(QuietButtonStyle())
                        .frame(maxWidth: .infinity)
                        .disabled(sending)
                }
            } else {
                Button("done") { close(r.state == .sent) }
                    .buttonStyle(OutlineButtonStyle(height: 56, size: 16))
                if r.state == .empty {
                    Button("discard") { confirmDiscard = true }
                        .buttonStyle(QuietButtonStyle())
                        .frame(maxWidth: .infinity)
                }
            }
        }
    }

    private func stat(_ term: String, _ value: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(term).font(.mono(12)).foregroundStyle(Palette.dim)
            Text(value).font(.mono(15)).lineLimit(1).minimumScaleFactor(0.7)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

// The ride's shape, no basemap: solid where it is sent, dashed where it is cut.
struct TrackView: View {
    let points: [TrackPoint]

    var body: some View {
        Canvas { ctx, size in
            guard points.count > 1 else { return }
            let lat0 = points.map(\.lat).reduce(0, +) / Double(points.count)
            let kx = cos(lat0 * .pi / 180)
            let xs = points.map { $0.lon * kx }, ys = points.map { -$0.lat }
            guard let x0 = xs.min(), let x1 = xs.max(), let y0 = ys.min(), let y1 = ys.max() else { return }
            let w = Double(size.width), h = Double(size.height), pad = 14.0
            let scale: Double = min((w - 2 * pad) / max(x1 - x0, 1e-9), (h - 2 * pad) / max(y1 - y0, 1e-9))
            let dx: Double = (w - (x1 - x0) * scale) / 2
            let dy: Double = (h - (y1 - y0) * scale) / 2
            let at = { (i: Int) -> CGPoint in
                CGPoint(x: dx + (xs[i] - x0) * scale, y: dy + (ys[i] - y0) * scale)
            }

            var kept = Path(), cut = Path()
            for i in 1..<points.count {
                if points[i - 1].kept && points[i].kept {
                    kept.move(to: at(i - 1))
                    kept.addLine(to: at(i))
                } else {
                    cut.move(to: at(i - 1))
                    cut.addLine(to: at(i))
                }
            }
            ctx.stroke(cut, with: .color(Palette.cut), style: StrokeStyle(lineWidth: 2, lineCap: .round, dash: [3, 4]))
            ctx.stroke(kept, with: .color(Palette.ink), style: StrokeStyle(lineWidth: 2.5, lineCap: .round, lineJoin: .round))
            for p in [at(0), at(points.count - 1)] {
                ctx.fill(Path(ellipseIn: CGRect(x: p.x - 3, y: p.y - 3, width: 6, height: 6)), with: .color(Palette.dim))
            }
        }
        .accessibilityElement()
        .accessibilityLabel("the ride's track; the parts within 300 m of start and end are dashed")
    }
}
