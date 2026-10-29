import Foundation

struct TrackPoint: Codable, Sendable, Equatable {
    var lat: Double
    var lon: Double
    var kept: Bool
}

struct TrimResult: Sendable {
    var samples = 0
    var fixes = 0
    var durationS = 0.0
    var distanceM = 0.0
    var keptDistanceM = 0.0
    var keptRows = 0
    var track: [TrackPoint] = []

    var sendable: Bool { keptRows >= PrivacyTrim.minKeptRows }
}

// Cuts every part of a ride within 100 m of where it started or ended, on the
// phone, before anything is uploaded, so the stretch around home never leaves it.
//
//   - Start and end are the ride's first and last fix sharper than 50 m. A
//     cold-start fix can be hundreds of metres off, and a circle around it
//     could miss home.
//   - Everything before the start and after the end is cut as well, so rows
//     recorded before the first sharp fix cannot place anything near home.
//   - A radius, not a distance along the track (like nearRideEnd in dsp.js),
//     so a ride that loops back past home is hidden there too.
//   - Rows are kept or cut a whole fix-to-fix interval at a time: an interval
//     stays only when the fixes at both its ends are outside. Every kept stretch
//     then begins and ends on a fix, so dsp.js never interpolates a position
//     across a cut, and the gap a cut leaves in millis splits the stretches
//     into separate segments, as a firmware stall does.
//
// millis is left untouched, gaps included.
enum PrivacyTrim {
    static let radiusM = 100.0
    static let anchorMaxAccuracyM = 50.0
    // Under about 10 s of data at 100 Hz the site cannot make a cell of it.
    static let minKeptRows = 1000
    // The summary drawing does not need every fix.
    static let trackStepM = 10.0

    static func run(raw: URL, out: URL) throws -> TrimResult {
        let text = try String(contentsOf: raw, encoding: .utf8)
        let (kept, result) = trim(text)
        try kept.write(to: out, atomically: true, encoding: .utf8)
        return result
    }

    private struct Fix {
        let row: Int
        let lat: Double
        let lon: Double
        let speedKmh: Double?
        let hAcc: Double?   // nil in box files, which have no h_acc_m column

        var sharp: Bool { (hAcc ?? 0) <= PrivacyTrim.anchorMaxAccuracyM }
    }

    static func trim(_ text: String) -> (String, TrimResult) {
        var result = TrimResult()
        let lines = text.split(separator: "\n", omittingEmptySubsequences: true)
        guard let headerLine = lines.first else { return (RideCSV.header + "\n", result) }
        let names = headerLine.split(separator: ",", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
        let col = { (name: String) in names.firstIndex(of: name) }
        var out = String(headerLine) + "\n"
        guard let cMillis = col("millis"), let cAx = col("ax"),
              let cLat = col("lat"), let cLon = col("lon"), lines.count > 1 else { return (out, result) }
        let cSpeed = col("speed_kmh"), cAcc = col("h_acc_m")

        // Pass 1: the fixes. Most rows carry none, so only the columns up to lat
        // are looked at before a row is split in full.
        var fixes: [Fix] = []
        for i in 1..<lines.count {
            let line = lines[i]
            if !field(line, cAx).isEmpty { result.samples += 1 }
            guard !field(line, cLat).isEmpty else { continue }
            let p = line.split(separator: ",", omittingEmptySubsequences: false)
            let num = { (c: Int?) -> Double? in
                guard let c, c < p.count, let v = Double(p[c]), v.isFinite else { return nil }
                return v
            }
            guard let lat = num(cLat), let lon = num(cLon) else { continue }
            fixes.append(Fix(row: i, lat: lat, lon: lon, speedKmh: num(cSpeed), hAcc: num(cAcc)))
        }
        result.fixes = fixes.count
        if let a = Double(field(lines[1], cMillis)), let b = Double(field(lines[lines.count - 1], cMillis)) {
            result.durationS = max(0, (b - a) / 1000)
        }

        // Which fixes lie outside both circles and between the anchors.
        var outside = [Bool](repeating: false, count: fixes.count)
        if let s = fixes.firstIndex(where: { $0.sharp }), let e = fixes.lastIndex(where: { $0.sharp }) {
            let start = fixes[s], end = fixes[e]
            for k in s...e {
                let f = fixes[k]
                outside[k] = Geo.metres(start.lat, start.lon, f.lat, f.lon) >= radiusM
                    && Geo.metres(end.lat, end.lon, f.lat, f.lon) >= radiusM
            }
        }

        // Pass 2: keep a row when the fixes on both sides of it are outside.
        var next = 0   // the first fix at or after row i
        for i in 1..<lines.count {
            while next < fixes.count && fixes[next].row < i { next += 1 }
            guard next < fixes.count else { break }
            let prev = fixes[next].row == i ? next : next - 1
            guard prev >= 0, outside[prev], outside[next] else { continue }
            out += lines[i]
            out += "\n"
            result.keptRows += 1
        }

        // Distance, whole and kept, between consecutive usable fixes.
        var last: Int?
        for k in fixes.indices {
            let f = fixes[k]
            guard Geo.counts(hAcc: f.hAcc ?? 0, speedKmh: f.speedKmh ?? Geo.distanceMinSpeedKmh) else { continue }
            if let l = last {
                let d = Geo.metres(fixes[l].lat, fixes[l].lon, f.lat, f.lon)
                result.distanceM += d
                if outside[l] && outside[k] { result.keptDistanceM += d }
            }
            last = k
        }

        // A thinned copy of the track for the summary screen.
        for k in fixes.indices where fixes[k].sharp {
            let f = fixes[k]
            let point = TrackPoint(lat: f.lat, lon: f.lon, kept: outside[k])
            if let p = result.track.last, p.kept == point.kept, k != fixes.count - 1,
               Geo.metres(p.lat, p.lon, f.lat, f.lon) < trackStepM { continue }
            result.track.append(point)
        }

        return (out, result)
    }

    // The n-th comma-separated field of a row, without splitting all of it.
    static func field(_ line: Substring, _ n: Int) -> Substring {
        let bytes = line.utf8
        var start = bytes.startIndex
        var k = 0
        var i = bytes.startIndex
        while i < bytes.endIndex {
            if bytes[i] == UInt8(ascii: ",") {
                if k == n { return line[start..<i] }
                k += 1
                start = bytes.index(after: i)
            }
            i = bytes.index(after: i)
        }
        return k == n ? line[start..<line.endIndex] : ""
    }
}
