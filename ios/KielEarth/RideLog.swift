import Foundation
import UIKit

struct Ride: Codable, Identifiable, Equatable {
    enum State: String, Codable {
        case recording   // only seen if the app was stopped mid-ride; finished on next launch
        case ready       // trimmed, waiting to be sent
        case empty       // nothing left outside the 300 m zones
        case sent
    }

    let id: String
    let startedAt: Date
    var state: State
    var durationS = 0.0
    var distanceM = 0.0
    var keptDistanceM = 0.0
    var samples = 0
    var fixes = 0
    var sendBytes = 0
    var autoStopped = false
    var sentAt: Date?
    var lastError: String?
}

// The rides on this phone, their files, and sending them to kiel.earth.
//
// Each ride is two files in Documents/rides, which the Files app shows under
// "On My iPhone": <id>.csv as recorded, home included, which never leaves the
// phone, and <id>.send.csv, the trimmed copy that is uploaded.
@MainActor
final class RideLog: ObservableObject {
    struct Summary: Identifiable {
        let id: String
        let track: [TrackPoint]
    }

    @Published private(set) var rides: [Ride] = []   // newest first
    @Published private(set) var sending: Set<String> = []
    @Published private(set) var finishing = false
    @Published var summary: Summary?

    static let ingestURL = URL(string: "https://kiel.earth/api/bike/ingest")!
    static let keepSentDays = 7.0

    // The bearer token comes from ios/Local.xcconfig through Info.plist.
    static let uploadToken: String? = {
        let t = (Bundle.main.object(forInfoDictionaryKey: "KEUploadToken") as? String ?? "")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty || t.hasPrefix("$(") ? nil : t
    }()

    var toSend: [Ride] { rides.filter { $0.state == .ready } }

    private let folder: URL
    private let indexURL: URL

    init() {
        let fm = FileManager.default
        folder = fm.urls(for: .documentDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("rides", isDirectory: true)
        let support = fm.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        indexURL = support.appendingPathComponent("rides.json")
        try? fm.createDirectory(at: folder, withIntermediateDirectories: true)
        try? fm.createDirectory(at: support, withIntermediateDirectories: true)
        if let data = try? Data(contentsOf: indexURL),
           let list = try? Self.decoder.decode([Ride].self, from: data) {
            rides = list
        }
    }

    func ride(_ id: String) -> Ride? { rides.first { $0.id == id } }
    func rawURL(_ id: String) -> URL { folder.appendingPathComponent("\(id).csv") }
    func sendURL(_ id: String) -> URL { folder.appendingPathComponent("\(id).send.csv") }

    // A new ride file holding just the header. The id is "ip-" and the start
    // time in UTC, the firmware's r<yymmdd>_<hhmmss> with the phone's prefix.
    func begin(at date: Date = Date()) throws -> (id: String, file: URL) {
        var id = "ip-" + Self.idFormatter.string(from: date)
        while ride(id) != nil { id += "b" }
        let url = rawURL(id)
        try (RideCSV.header + "\n").write(to: url, atomically: true, encoding: .utf8)
        rides.insert(Ride(id: id, startedAt: date, state: .recording), at: 0)
        save()
        return (id, url)
    }

    // Trims the ride and records what is left. Shows the summary when the ride
    // was just stopped; a ride recovered after a crash goes straight to the list.
    func finish(_ id: String, autoStopped: Bool, showSummary: Bool) async {
        if showSummary { finishing = true }
        let raw = rawURL(id), out = sendURL(id)
        let result = await Task.detached(priority: .userInitiated) {
            try? PrivacyTrim.run(raw: raw, out: out)
        }.value
        update(id) { r in
            r.autoStopped = autoStopped
            guard let t = result else {
                r.state = .empty
                r.lastError = "the ride file could not be read"
                return
            }
            r.durationS = t.durationS
            r.distanceM = t.distanceM
            r.keptDistanceM = t.keptDistanceM
            r.samples = t.samples
            r.fixes = t.fixes
            r.state = t.sendable ? .ready : .empty
            r.sendBytes = t.sendable ? Self.size(of: out) : 0
        }
        if ride(id)?.state == .empty { try? FileManager.default.removeItem(at: out) }
        finishing = false
        if showSummary { summary = Summary(id: id, track: result?.track ?? []) }
    }

    func finishInterrupted() async {
        for r in rides where r.state == .recording {
            await finish(r.id, autoStopped: false, showSummary: false)
        }
    }

    func send(_ id: String) async {
        guard let r = ride(id), r.state == .ready, !sending.contains(id) else { return }
        guard let token = Self.uploadToken else {
            update(id) { $0.lastError = "no upload key in this build" }
            return
        }
        sending.insert(id)
        let task = BackgroundTask("upload \(id)")
        defer {
            sending.remove(id)
            task.end()
        }

        var req = URLRequest(url: Self.ingestURL, timeoutInterval: 120)
        req.httpMethod = "POST"
        req.setValue("text/csv", forHTTPHeaderField: "Content-Type")
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        req.setValue(id, forHTTPHeaderField: "X-Ride-Id")
        do {
            let (_, response) = try await URLSession.shared.upload(for: req, fromFile: sendURL(id))
            let code = (response as? HTTPURLResponse)?.statusCode ?? 0
            // kiel.earth stores the file before it answers 2xx, and answers a
            // repeated ride id with 2xx too, so a retry after a lost answer is safe.
            switch code {
            case 200..<300:
                update(id) { r in
                    r.state = .sent
                    r.sentAt = Date()
                    r.lastError = nil
                }
            case 401:
                update(id) { $0.lastError = "kiel.earth refused the upload key" }
            default:
                update(id) { $0.lastError = "kiel.earth answered \(code)" }
            }
        } catch {
            update(id) { $0.lastError = "kiel.earth could not be reached" }
        }
    }

    func sendAll() async {
        for r in toSend { await send(r.id) }
    }

    func remove(_ id: String) {
        guard !sending.contains(id) else { return }
        try? FileManager.default.removeItem(at: rawURL(id))
        try? FileManager.default.removeItem(at: sendURL(id))
        rides.removeAll { $0.id == id }
        save()
    }

    // A ride stays on the phone for 7 days after kiel.earth has it.
    func prune(now: Date = Date()) {
        let cutoff = now.addingTimeInterval(-Self.keepSentDays * 86_400)
        for r in rides where r.state == .sent && (r.sentAt ?? now) < cutoff {
            remove(r.id)
        }
    }

    private func update(_ id: String, _ change: (inout Ride) -> Void) {
        guard let i = rides.firstIndex(where: { $0.id == id }) else { return }
        change(&rides[i])
        save()
    }

    private func save() {
        guard let data = try? Self.encoder.encode(rides) else { return }
        try? data.write(to: indexURL, options: .atomic)
    }

    private static func size(of url: URL) -> Int {
        ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.intValue ?? 0
    }

    private static let idFormatter: DateFormatter = {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyMMdd_HHmmss"
        return f
    }()

    private static let encoder: JSONEncoder = {
        let e = JSONEncoder()
        e.dateEncodingStrategy = .iso8601
        return e
    }()

    private static let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.dateDecodingStrategy = .iso8601
        return d
    }()
}

// Asks iOS for time to finish an upload if the phone is locked meanwhile.
@MainActor
final class BackgroundTask {
    private var id: UIBackgroundTaskIdentifier = .invalid

    init(_ name: String) {
        id = UIApplication.shared.beginBackgroundTask(withName: name) { [weak self] in
            self?.end()
        }
    }

    func end() {
        guard id != .invalid else { return }
        UIApplication.shared.endBackgroundTask(id)
        id = .invalid
    }
}
