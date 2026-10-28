import CoreMotion
import Foundation

// Everything that happens at 100 Hz: one accelerometer sample becomes one CSV
// row, and each GPS fix is put on the row nearest to it in time. Every method
// runs on the recorder's serial queue; nothing here touches the main actor.
final class RideWriter: @unchecked Sendable {
    // What the riding screen shows, sent ten times a second.
    struct Tick: Sendable {
        var samples = 0
        var fixes = 0
        var millis: Int64 = 0
        var rateHz = 0.0
        var rmsG = 0.0
        var trace: [Float] = []   // vertical acceleration in g, oldest first
    }

    var onTick: (@Sendable (Tick) -> Void)?

    private struct Row {
        var millis: Int64
        var ax, ay, az: Int32
        var fix: RideCSV.Fix?
    }

    private let handle: FileHandle
    private var closed = false

    // Rows stay here for 2 s before they are written, because a fix reaches the
    // app a little after the moment it describes. Placing it on the row nearest
    // its own timestamp, not on the next row written, keeps that delivery delay
    // (up to a second, 5 m at 18 km/h) out of the positions.
    private var pending: [Row] = []
    private var early: (millis: Int64, fix: RideCSV.Fix)?
    private var out = ""
    private var outRows = 0
    private static let holdMillis: Int64 = 2000
    // The firmware writes a fix only while it is under 1.5 s old.
    private static let maxFixAgeMillis: Int64 = 1500

    // Sample timestamps count seconds since boot; fixes carry wall-clock dates.
    // The offset between the two is the smallest (delivery time - sample time)
    // seen over the first two seconds, when delivery latency is a few ms.
    private var t0: TimeInterval?
    private var wallOffset = TimeInterval.infinity
    private var offsetSamples = 0

    private var tick = Tick()
    private var gravity: (x: Double, y: Double, z: Double)?
    private var trace = [Float](repeating: 0, count: 200)
    private var traceAt = 0
    private var recentMillis = [Int64](repeating: 0, count: 101)

    init(appendingTo file: URL) throws {
        handle = try FileHandle(forWritingTo: file)
        try handle.seekToEnd()
    }

    func add(_ data: CMAccelerometerData) {
        guard !closed else { return }
        let now = Date().timeIntervalSince1970
        if t0 == nil { t0 = data.timestamp }
        if offsetSamples < 200 {
            wallOffset = min(wallOffset, now - data.timestamp)
            offsetSamples += 1
        }
        let millis = Int64(((data.timestamp - t0!) * 1000).rounded())

        // iOS reports gravity as -1 g on the axis pointing up; the MPU-6050
        // reads +1 g there. Flip, so a phone row reads like a box row. dsp.js
        // projects onto its own gravity estimate and would not mind either way.
        let x = -data.acceleration.x, y = -data.acceleration.y, z = -data.acceleration.z
        var row = Row(millis: millis, ax: RideCSV.counts(x), ay: RideCSV.counts(y), az: RideCSV.counts(z), fix: nil)
        if let e = early, millis >= e.millis {
            row.fix = e.fix
            early = nil
            tick.fixes += 1
        }
        pending.append(row)
        tick.samples += 1
        tick.millis = millis

        if let i = pending.firstIndex(where: { millis - $0.millis <= Self.holdMillis }), i > 0 {
            for r in pending[..<i] {
                out += RideCSV.row(millis: r.millis, ax: r.ax, ay: r.ay, az: r.az, fix: r.fix)
            }
            outRows += i
            pending.removeFirst(i)
            if outRows >= 100 { flush() }
        }

        traceVertical(x, y, z)
        recentMillis[tick.samples % recentMillis.count] = millis
        if tick.samples % 10 == 0 { sendTick() }
    }

    func add(_ fix: RideCSV.Fix) {
        guard !closed, let t0, wallOffset.isFinite else { return }
        let millis = Int64(((fix.time.timeIntervalSince1970 - wallOffset - t0) * 1000).rounded())
        guard let last = pending.last, millis <= last.millis else {
            // Newer than the newest sample: it goes on the next row at or after it.
            early = (millis, fix)
            return
        }
        guard let first = pending.first, millis >= first.millis - Self.maxFixAgeMillis else { return }
        var best = 0
        for i in pending.indices where abs(pending[i].millis - millis) < abs(pending[best].millis - millis) {
            best = i
        }
        if pending[best].fix == nil { tick.fixes += 1 }
        pending[best].fix = fix
    }

    // Writes what is left and closes the file. Safe to call twice.
    func finish() {
        guard !closed else { return }
        for r in pending {
            out += RideCSV.row(millis: r.millis, ax: r.ax, ay: r.ay, az: r.az, fix: r.fix)
        }
        pending.removeAll()
        flush()
        try? handle.close()
        closed = true
    }

    private func flush() {
        guard !out.isEmpty else { return }
        try? handle.write(contentsOf: Data(out.utf8))
        out = ""
        outRows = 0
    }

    // The riding screen's trace: acceleration along gravity, gravity estimated
    // with the same 0.5 Hz low-pass dsp.js uses (verticalAccel).
    private func traceVertical(_ x: Double, _ y: Double, _ z: Double) {
        let alpha = exp(-2 * Double.pi * 0.5 / 100)
        var g = gravity ?? (x, y, z)
        g = (alpha * g.x + (1 - alpha) * x, alpha * g.y + (1 - alpha) * y, alpha * g.z + (1 - alpha) * z)
        gravity = g
        let mag = max(sqrt(g.x * g.x + g.y * g.y + g.z * g.z), 1e-6)
        let v = (x * g.x + y * g.y + z * g.z) / mag - mag
        trace[traceAt] = Float(v)
        traceAt = (traceAt + 1) % trace.count
    }

    private func sendTick() {
        var t = tick
        t.trace = Array(trace[traceAt...] + trace[..<traceAt])
        let mean = t.trace.reduce(0, +) / Float(t.trace.count)
        t.rmsG = Double(sqrt(t.trace.reduce(0) { $0 + ($1 - mean) * ($1 - mean) } / Float(t.trace.count)))
        if t.samples > recentMillis.count {
            // Rate over the last 100 samples.
            let then = recentMillis[(t.samples + 1) % recentMillis.count]
            if t.millis > then { t.rateHz = 100_000 / Double(t.millis - then) }
        }
        onTick?(t)
    }
}
