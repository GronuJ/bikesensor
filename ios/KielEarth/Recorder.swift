import CoreLocation
import CoreMotion
import UIKit

// Runs a ride: GPS and the accelerometer in, a RideWriter out.
//
// Recording with the screen locked rests on location: with the location
// background mode, allowsBackgroundLocationUpdates and updates started while
// the app is open, iOS keeps the app running, and Core Motion keeps delivering
// samples with it. When-in-use permission is enough for that; the blue
// location pill shows while a ride runs in the background.
@MainActor
final class Recorder: NSObject, ObservableObject {
    @Published private(set) var rideID: String?
    // True from the stop until the file is closed and handed on, so the screen
    // goes straight from riding to the summary.
    @Published private(set) var finishing = false
    @Published private(set) var tick = RideWriter.Tick()
    @Published private(set) var lastLocation: CLLocation?
    @Published private(set) var authorization = CLAuthorizationStatus.notDetermined
    @Published private(set) var precise = true
    @Published private(set) var distanceM = 0.0

    var isRecording: Bool { rideID != nil }
    var mayRecord: Bool { authorization == .authorizedWhenInUse || authorization == .authorizedAlways }
    var motionAvailable: Bool { motion.isAccelerometerAvailable }

    // Called with the ride id once the file is closed, and whether the ride
    // stopped by itself.
    var onFinished: ((String, Bool) -> Void)?

    // A test since 2026-10-29: Apple promises 100 Hz on an iPhone, and the app
    // asks for 200 to see what the phone delivers. Each row's millis comes from
    // the sensor's own timestamp, so a ride's file shows the rate it got, and
    // dsp.js reads the rate from the file. Nothing else assumes a rate.
    static let sampleHz = 200.0
    // Like the firmware: a ride ends after three still minutes.
    static let stillStopS = 180.0
    static let movingKmh = 6.0

    private let location = CLLocationManager()
    private let motion = CMMotionManager()
    private let queue = DispatchQueue(label: "earth.kiel.recorder", qos: .userInitiated)
    private let motionQueue = OperationQueue()
    private var writer: RideWriter?
    private var session: CLBackgroundActivitySession?
    private var startedAt = Date()
    private var lastMovedAt = Date()
    private var lastCounted: CLLocation?
    private var idle = false

    override init() {
        super.init()
        authorization = location.authorizationStatus
        motionQueue.underlyingQueue = queue
        motionQueue.maxConcurrentOperationCount = 1
        location.delegate = self
        location.desiredAccuracy = kCLLocationAccuracyBestForNavigation
        location.distanceFilter = kCLDistanceFilterNone
        location.activityType = .fitness
        location.pausesLocationUpdatesAutomatically = false
        precise = location.accuracyAuthorization == .fullAccuracy
    }

    func requestPermission() {
        location.requestWhenInUseAuthorization()
    }

    // GPS while the home screen is open, so the fix is ready when the ride starts.
    func setIdle(_ on: Bool) {
        idle = on
        guard !isRecording else { return }
        if on && mayRecord {
            location.startUpdatingLocation()
        } else {
            location.stopUpdatingLocation()
        }
    }

    func start(rideID id: String, file: URL) throws {
        guard !isRecording, motionAvailable, mayRecord else { throw CocoaError(.featureUnsupported) }
        let w = try RideWriter(appendingTo: file)
        w.onTick = { [weak self] t in
            guard let self else { return }
            Task { @MainActor in self.apply(t) }
        }
        writer = w
        tick = RideWriter.Tick()
        distanceM = 0
        lastCounted = nil
        startedAt = Date()
        lastMovedAt = startedAt
        UIDevice.current.isBatteryMonitoringEnabled = true

        location.allowsBackgroundLocationUpdates = true
        location.showsBackgroundLocationIndicator = true
        session = CLBackgroundActivitySession()
        location.startUpdatingLocation()

        motion.accelerometerUpdateInterval = 1 / Self.sampleHz
        motion.startAccelerometerUpdates(to: motionQueue, withHandler: Self.handler(for: w))
        rideID = id
    }

    func stop(automatically: Bool = false) {
        guard let id = rideID, let w = writer else { return }
        motion.stopAccelerometerUpdates()
        session?.invalidate()
        session = nil
        location.allowsBackgroundLocationUpdates = false
        if !idle { location.stopUpdatingLocation() }
        rideID = nil
        writer = nil
        finishing = true
        queue.async {
            w.finish()
            Task { @MainActor [weak self] in
                self?.onFinished?(id, automatically)
                self?.finishing = false
            }
        }
    }

    // Built outside the main actor: Core Motion calls it on the recorder queue.
    nonisolated private static func handler(for w: RideWriter) -> CMAccelerometerHandler {
        return { data, _ in
            if let data { w.add(data) }
        }
    }

    private func apply(_ t: RideWriter.Tick) {
        guard isRecording else { return }
        tick = t
        if Date().timeIntervalSince(lastMovedAt) > Self.stillStopS {
            stop(automatically: true)
        }
    }

    private func handle(_ locations: [CLLocation]) {
        for loc in locations where loc.horizontalAccuracy >= 0 {
            lastLocation = loc
            guard let w = writer, loc.timestamp >= startedAt else { continue }
            let speedKmh: Double? = loc.speed >= 0 ? loc.speed * 3.6 : nil
            let level = UIDevice.current.batteryLevel
            let fix = RideCSV.Fix(
                lat: loc.coordinate.latitude,
                lon: loc.coordinate.longitude,
                ele: loc.verticalAccuracy >= 0 ? loc.altitude : nil,
                speedKmh: speedKmh,
                batteryPct: level >= 0 ? Int((level * 100).rounded()) : nil,
                time: loc.timestamp,
                hAcc: loc.horizontalAccuracy)
            queue.async { w.add(fix) }

            if (speedKmh ?? 0) >= Self.movingKmh { lastMovedAt = Date() }
            if Geo.counts(hAcc: loc.horizontalAccuracy, speedKmh: speedKmh ?? Geo.distanceMinSpeedKmh) {
                if let p = lastCounted {
                    distanceM += Geo.metres(p.coordinate.latitude, p.coordinate.longitude,
                                            loc.coordinate.latitude, loc.coordinate.longitude)
                }
                lastCounted = loc
            }
        }
    }
}

extension Recorder: CLLocationManagerDelegate {
    // The manager was made on the main thread, so it calls back there.
    nonisolated func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        MainActor.assumeIsolated { handle(locations) }
    }

    nonisolated func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        MainActor.assumeIsolated {
            authorization = manager.authorizationStatus
            precise = manager.accuracyAuthorization == .fullAccuracy
            if idle && !isRecording { setIdle(true) }
        }
    }

    nonisolated func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {}
}
