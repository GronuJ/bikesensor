import SwiftUI

// While a ride records. The design's "road now" class strip and its black
// screen are left out: the site does the classifying, and the phone can simply
// be locked.
struct RidingView: View {
    @EnvironmentObject private var recorder: Recorder

    private var t: RideWriter.Tick { recorder.tick }

    private var gps: String {
        guard let loc = recorder.lastLocation, -loc.timestamp.timeIntervalSinceNow < 5 else { return "no fix" }
        return "±\(Int(loc.horizontalAccuracy.rounded())) m"
    }

    private var speed: String {
        guard let loc = recorder.lastLocation, loc.speed >= 0, -loc.timestamp.timeIntervalSinceNow < 5 else { return "–" }
        return String(format: "%.1f", loc.speed * 3.6)
    }

    private var rate: String { t.rateHz > 0 ? String(Int(t.rateHz.rounded())) : "–" }

    private var privateZone: String {
        guard let s = recorder.leftStartZoneAtS else { return "inside the start zone" }
        return "start zone left at \(Fmt.duration(s))"
    }

    var body: some View {
        VStack(spacing: 0) {
            TopBar(title: "kiel.earth — surface", showsMap: false) {
                Text("\(Text("●").foregroundStyle(Palette.red)) recording · \(rate) hz · gps \(gps)")
            }
            VStack(alignment: .leading, spacing: 0) {
                VStack(alignment: .leading, spacing: 2) {
                    Text(Fmt.clock(Double(t.millis) / 1000)).font(.mono(54)).lineLimit(1).minimumScaleFactor(0.5)
                    HStack(spacing: 24) {
                        Text("\(Fmt.km(recorder.distanceM)) km")
                        Text("\(speed) km/h")
                    }
                    .font(.mono(18))
                }
                Spacer(minLength: 12)
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text("road now").foregroundStyle(Palette.dim)
                        Spacer()
                        Text(String(format: "%.3f g rms", t.rmsG))
                    }
                    .font(.mono(13))
                    Trace(values: t.trace).frame(height: 110)
                    Text("vertical acceleration, last 2 s").font(.mono(12)).foregroundStyle(Palette.dim)
                }
                Spacer(minLength: 12)
                VStack(alignment: .leading, spacing: 2) {
                    InfoRow("private", privateZone)
                    InfoRow("samples", Fmt.count(t.samples))
                    InfoRow("gps fixes", Fmt.count(t.fixes))
                }
                Spacer(minLength: 12)
                VStack(spacing: 4) {
                    HoldToStop { recorder.stop() }
                    Text("lock the phone, it keeps recording")
                        .font(.mono(13))
                        .foregroundStyle(Palette.dim)
                        .frame(minHeight: 44)
                }
            }
            .padding(.horizontal, 20)
            .padding(.top, 22)
            .padding(.bottom, 20)
        }
    }
}

// Vertical acceleration over the last two seconds; ±0.25 g fills the height.
struct Trace: View {
    let values: [Float]

    var body: some View {
        Canvas { ctx, size in
            let mid = size.height / 2
            var axis = Path()
            axis.move(to: CGPoint(x: 0, y: mid))
            axis.addLine(to: CGPoint(x: size.width, y: mid))
            ctx.stroke(axis, with: .color(Palette.rule), lineWidth: 1)
            guard values.count > 1 else { return }
            let step = size.width / CGFloat(values.count - 1)
            let scale = size.height / 0.5
            var line = Path()
            for (i, v) in values.enumerated() {
                let y = min(max(mid - CGFloat(v) * scale, 2), size.height - 2)
                let p = CGPoint(x: CGFloat(i) * step, y: y)
                if i == 0 { line.move(to: p) } else { line.addLine(to: p) }
            }
            ctx.stroke(line, with: .color(Palette.ink), style: StrokeStyle(lineWidth: 1.3, lineJoin: .round))
        }
        .overlay(alignment: .top) { Rule() }
        .overlay(alignment: .bottom) { Rule() }
        .accessibilityElement()
        .accessibilityLabel("vertical acceleration, last two seconds")
    }
}

// Stopping takes a one-second press, so a bump on the bar cannot end a ride.
struct HoldToStop: View {
    let action: () -> Void
    @State private var holding = false

    var body: some View {
        ZStack(alignment: .leading) {
            GeometryReader { geo in
                Rectangle()
                    .fill(Palette.ink.opacity(0.22))
                    .frame(width: holding ? geo.size.width : 0)
            }
            Text("hold to stop")
                .font(.mono(17))
                .tracking(0.7)
                .frame(maxWidth: .infinity)
        }
        .frame(height: 64)
        .overlay(Rectangle().stroke(Palette.ink, lineWidth: 1))
        .contentShape(Rectangle())
        .onLongPressGesture(minimumDuration: 1) {
            action()
        } onPressingChanged: { pressing in
            withAnimation(pressing ? .linear(duration: 1) : .easeOut(duration: 0.2)) { holding = pressing }
        }
        .accessibilityElement()
        .accessibilityLabel("stop ride")
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { action() }
    }
}
