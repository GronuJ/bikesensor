import Foundation

// The bikesensor CSV contract (see CLAUDE.md), written by the phone so that
// kiel.earth cannot tell a phone ride from a box ride except by its id prefix.
//
// One column is added at the end, h_acc_m: the fix's horizontal accuracy in
// metres. dsp.js reads columns by name, so it is ignored until something uses it.
//
// This file and PrivacyTrim.swift use Foundation only, so they also build with
// a plain swiftc on Linux to be checked against dsp.js.
enum RideCSV {
    static let header = "millis,ax,ay,az,lat,lon,ele,speed_kmh,battery_pct,gps_time,h_acc_m"

    // MPU-6050 counts per g at the ±4 g range the firmware sets. dsp.js divides
    // by the same number (CAL.ACC_SCALE), so a phone row reads like a box row.
    static let countsPerG = 8192.0

    struct Fix {
        var lat: Double
        var lon: Double
        var ele: Double?
        var speedKmh: Double?
        var batteryPct: Int?
        var time: Date
        var hAcc: Double
    }

    static func counts(_ g: Double) -> Int32 {
        Int32(max(min((g * countsPerG).rounded(), Double(Int32.max)), Double(Int32.min)))
    }

    // An accel-only row leaves the seven GPS columns empty, as the firmware's
    // 200 Hz rows do.
    static func row(millis: Int64, ax: Int32, ay: Int32, az: Int32, fix: Fix?) -> String {
        let head = "\(millis),\(ax),\(ay),\(az)"
        guard let f = fix else { return head + ",,,,,,,\n" }
        var s = head
        s += "," + fixed(f.lat, 6) + "," + fixed(f.lon, 6)
        s += "," + (f.ele.map { fixed($0, 1) } ?? "")
        s += "," + (f.speedKmh.map { fixed($0, 2) } ?? "")
        s += "," + (f.batteryPct.map { String($0) } ?? "")
        s += "," + isoTime(f.time)
        s += "," + fixed(f.hAcc, 1)
        return s + "\n"
    }

    static func fixed(_ v: Double, _ places: Int) -> String {
        String(format: "%.\(places)f", v)
    }

    // ISO 8601 in UTC like the firmware's gps_time, but with milliseconds: a
    // phone fix is not stamped on the whole second the way a GPS module's is.
    static func isoTime(_ d: Date) -> String {
        let ms = Int64((d.timeIntervalSince1970 * 1000).rounded())
        let whole = Date(timeIntervalSince1970: Double(ms / 1000))
        // "2026-10-28T08:15:00Z" → "2026-10-28T08:15:00.123Z"
        return String(isoFormatter.string(from: whole).dropLast()) + String(format: ".%03dZ", Int(ms % 1000))
    }

    private static let isoFormatter: ISO8601DateFormatter = {
        let f = ISO8601DateFormatter()
        f.formatOptions = [.withInternetDateTime]
        f.timeZone = TimeZone(identifier: "UTC")
        return f
    }()
}

enum Geo {
    // Straight-line metres, the same flat-earth approximation dsp.js uses for
    // its 300 m privacy radius, so the phone and the site agree on the zone.
    static func metres(_ aLat: Double, _ aLon: Double, _ bLat: Double, _ bLon: Double) -> Double {
        let kx = cos(aLat * .pi / 180) * 111_320
        return hypot((bLon - aLon) * kx, (bLat - aLat) * 111_320)
    }

    // Distance counts between two fixes only when both are reasonably sharp and
    // the bike is rolling, so GPS wander at a red light adds nothing.
    static let distanceMaxAccuracyM = 30.0
    static let distanceMinSpeedKmh = 3.0

    static func counts(hAcc: Double, speedKmh: Double?) -> Bool {
        hAcc >= 0 && hAcc <= distanceMaxAccuracyM && (speedKmh ?? 0) >= distanceMinSpeedKmh
    }
}
