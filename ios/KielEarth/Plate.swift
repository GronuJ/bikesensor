import SwiftUI

// The landing page's Chladni plate, settled: sand on the nodal lines of
// f = sin(nπx)sin(mπy) − sin(mπx)sin(nπy), a few grains still loose.
// Same seed and generator as the design, so it is the same plate.
struct Plate: View {
    private static let side: CGFloat = 240
    private static let sand = makeSand(n: 3, m: 5, size: side, count: 2600)

    var body: some View {
        Canvas { ctx, _ in ctx.fill(Self.sand, with: .color(Palette.dim)) }
            .frame(width: Self.side, height: Self.side)
            .accessibilityHidden(true)
    }

    private static func makeSand(n: Double, m: Double, size: CGFloat, count: Int) -> Path {
        var seed: UInt32 = 7
        // mulberry32
        func rnd() -> Double {
            seed = seed &+ 0x6d2b79f5
            var t = (seed ^ (seed >> 15)) &* (1 | seed)
            t = (t &+ ((t ^ (t >> 7)) &* (61 | t))) ^ t
            return Double(t ^ (t >> 14)) / 4_294_967_296
        }
        func f(_ x: Double, _ y: Double) -> Double {
            sin(n * .pi * x) * sin(m * .pi * y) - sin(m * .pi * x) * sin(n * .pi * y)
        }
        var path = Path()
        var got = 0, tries = 0
        while got < count && tries < 400_000 {
            tries += 1
            let x = rnd(), y = rnd()
            if abs(f(x, y)) < 0.035 || rnd() < 0.003 {
                path.addRect(CGRect(x: x * size, y: y * size, width: 1.1, height: 1.1))
                got += 1
            }
        }
        return path
    }
}
