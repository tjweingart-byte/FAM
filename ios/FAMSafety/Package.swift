// swift-tools-version:5.9
// FAM's App Store safety screens (APP_STORE.md Part B, PROBLEMS.md §223):
// asking before a listener's words go to the AI (5.1.2(i)), and reporting and
// blocking (1.2). A Swift package so the app target adds it as a local
// dependency; every word the screens show comes from the server.
import PackageDescription

let package = Package(
    name: "FAMSafety",
    platforms: [.iOS(.v16), .macOS(.v13)],
    products: [
        .library(name: "FAMSafety", targets: ["FAMSafety"]),
    ],
    targets: [
        .target(name: "FAMSafety"),
        .testTarget(name: "FAMSafetyTests", dependencies: ["FAMSafety"]),
    ]
)
