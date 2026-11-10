import AuthenticationServices
import CryptoKit
import GoogleSignIn
import SwiftUI

/// Continue with Apple and Continue with Google (App Store 4.8): the
/// system's own sheets, then the identity token to `/api/auth/provider`,
/// which verifies it against the provider's keys. The web has neither -
/// they only complete on the phone. On sign-up both wait for the terms box,
/// like the email form (§228).
struct ProviderButtons: View {
    @EnvironmentObject private var model: AppModel
    /// Sign-up needs the terms ticked first; log-in does not.
    let making: Bool
    let agreed: Bool
    @Binding var problem: String
    @Binding var working: Bool
    /// Set to link to the account signed in now instead of signing in.
    var linking = false
    /// Which buttons to draw (the link list draws one at a time).
    var showApple = true
    var showGoogle = true

    @State private var appleNonce = ""
    /// Dark mode (Settings): the web has neither button, so each follows its
    /// maker's own rule - Apple's white button and Google's dark one on a
    /// dark page.
    @Environment(\.colorScheme) private var scheme

    private var enabled: Bool { (!making || agreed) && !working }

    /// The Google button is drawn only once the app has its client id.
    static var googleConfigured: Bool {
        let id = Bundle.main.object(forInfoDictionaryKey: "GIDClientID") as? String ?? ""
        return id.hasSuffix(".apps.googleusercontent.com")
    }

    var body: some View {
        VStack(spacing: 10) {
            if showApple {
            SignInWithAppleButton(linking ? .continue : making ? .signUp : .signIn) { request in
                appleNonce = Self.randomNonce()
                request.requestedScopes = [.fullName, .email]
                request.nonce = Self.sha256(appleNonce)
            } onCompletion: { result in
                Task { await finishApple(result) }
            }
            .signInWithAppleButtonStyle(scheme == .dark ? .white : .black)
            .frame(height: 46)
            .clipShape(Capsule())
            .disabled(!enabled)
            .opacity(enabled ? 1 : 0.45)
            // Apple's button sets its words once; a new id makes it redraw
            // when Log in and Sign up switch.
            .id("apple-\(making)-\(linking)-\(scheme == .dark)")
            }

            if showGoogle && Self.googleConfigured {
                Button { Task { await google() } } label: {
                    HStack(spacing: 10) {
                        GoogleMark().frame(width: 18, height: 18)
                        Text(linking ? "Continue with Google" : making ? "Sign up with Google" : "Sign in with Google")
                            .font(.system(size: 17, weight: .medium))
                            .foregroundStyle(scheme == .dark ? Color(hex: 0xE3E3E3) : Color.black.opacity(0.85))
                    }
                    .frame(maxWidth: .infinity, minHeight: 46)
                    .background(scheme == .dark ? Color(hex: 0x131314) : Color.white, in: Capsule())
                    .overlay(Capsule().stroke(Color(hex: scheme == .dark ? 0x8E918F : 0x747775)))
                }
                .buttonStyle(.press)
                .disabled(!enabled)
                .opacity(enabled ? 1 : 0.45)
            }

            if making && !agreed {
                Text("Tick the box below to agree to the terms first.")
                    .font(FAMFont.body(11.5))
                    .foregroundStyle(FAMColor.textFaint)
            }
        }
    }

    // MARK: Apple

    private func finishApple(_ result: Result<ASAuthorization, Error>) async {
        switch result {
        case .failure(let error):
            // Cancelling the sheet is not a problem worth a sentence.
            if (error as? ASAuthorizationError)?.code != .canceled {
                problem = "Sign in with Apple did not finish. Try again."
            }
        case .success(let auth):
            guard let credential = auth.credential as? ASAuthorizationAppleIDCredential,
                  let tokenData = credential.identityToken,
                  let token = String(data: tokenData, encoding: .utf8) else {
                problem = "Apple did not send a sign-in. Try again."
                return
            }
            let code = credential.authorizationCode.flatMap { String(data: $0, encoding: .utf8) } ?? ""
            await send(provider: "apple", token: token, nonce: appleNonce, code: code)
        }
    }

    // MARK: Google

    @MainActor
    private func google() async {
        guard let root = UIApplication.shared.connectedScenes
            .compactMap({ ($0 as? UIWindowScene)?.keyWindow?.rootViewController }).first else { return }
        // The top-most screen presents the sheet.
        var top = root
        while let next = top.presentedViewController { top = next }
        do {
            // No nonce: this version of Google's package does not take one
            // on this call, and the server's nonce check is optional - it
            // still verifies the token's signature and that it was issued
            // for FAM's own iOS client (`oauth.verify`).
            let result = try await GIDSignIn.sharedInstance.signIn(withPresenting: top)
            guard let token = result.user.idToken?.tokenString else {
                problem = "Google did not send a sign-in. Try again."
                return
            }
            await send(provider: "google", token: token, nonce: "", code: "")
        } catch {
            // -5 is Google's "the user cancelled" (kGIDSignInErrorCodeCanceled).
            if (error as NSError).code != -5 {
                problem = "Sign in with Google did not finish. Try again."
            }
        }
    }

    // MARK: Both

    private func send(provider: String, token: String, nonce: String, code: String) async {
        working = true
        defer { working = false }
        problem = ""
        do {
            if linking {
                try await model.link(provider: provider, idToken: token, nonce: nonce,
                                     authorizationCode: code)
            } else {
                try await model.signIn(provider: provider, idToken: token, nonce: nonce,
                                       authorizationCode: code, acceptTerms: making && agreed)
            }
        } catch {
            problem = error.localizedDescription
        }
    }

    /// A random string for this one sign-in, so a captured token cannot be replayed.
    static func randomNonce(length: Int = 32) -> String {
        let chars = Array("0123456789ABCDEFGHIJKLMNOPQRSTUVXYZabcdefghijklmnopqrstuvwxyz-._")
        var bytes = [UInt8](repeating: 0, count: length)
        _ = SecRandomCopyBytes(kSecRandomDefault, length, &bytes)
        return String(bytes.map { chars[Int($0) % chars.count] })
    }

    /// Apple is sent the hash; the server is sent the raw value and checks both.
    static func sha256(_ text: String) -> String {
        SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }
}

/// Google's four-colour G, drawn from its published geometry.
struct GoogleMark: View {
    var body: some View {
        Canvas { context, size in
            let s = size.width / 48
            func path(_ build: (inout Path) -> Void) -> Path {
                var p = Path(); build(&p); return p.applying(CGAffineTransform(scaleX: s, y: s))
            }
            context.fill(path { p in
                p.move(to: CGPoint(x: 24, y: 9.5))
                p.addCurve(to: CGPoint(x: 33.21, y: 13.1), control1: CGPoint(x: 27.54, y: 9.5), control2: CGPoint(x: 30.71, y: 10.72))
                p.addLine(to: CGPoint(x: 40.06, y: 6.25))
                p.addCurve(to: CGPoint(x: 24, y: 0), control1: CGPoint(x: 35.9, y: 2.38), control2: CGPoint(x: 30.47, y: 0))
                p.addCurve(to: CGPoint(x: 2.56, y: 13.22), control1: CGPoint(x: 14.62, y: 0), control2: CGPoint(x: 6.51, y: 5.38))
                p.addLine(to: CGPoint(x: 10.54, y: 19.41))
                p.addCurve(to: CGPoint(x: 24, y: 9.5), control1: CGPoint(x: 12.43, y: 13.72), control2: CGPoint(x: 17.74, y: 9.5))
            }, with: .color(Color(hex: 0xEA4335)))
            context.fill(path { p in
                p.move(to: CGPoint(x: 46.98, y: 24.55))
                p.addCurve(to: CGPoint(x: 46.6, y: 20), control1: CGPoint(x: 46.98, y: 22.98), control2: CGPoint(x: 46.83, y: 21.46))
                p.addLine(to: CGPoint(x: 24, y: 20))
                p.addLine(to: CGPoint(x: 24, y: 29.02))
                p.addLine(to: CGPoint(x: 36.94, y: 29.02))
                p.addCurve(to: CGPoint(x: 32.16, y: 36.2), control1: CGPoint(x: 36.36, y: 31.98), control2: CGPoint(x: 34.68, y: 34.5))
                p.addLine(to: CGPoint(x: 39.89, y: 42.2))
                p.addCurve(to: CGPoint(x: 46.98, y: 24.55), control1: CGPoint(x: 44.4, y: 38.02), control2: CGPoint(x: 46.98, y: 31.84))
            }, with: .color(Color(hex: 0x4285F4)))
            context.fill(path { p in
                p.move(to: CGPoint(x: 10.53, y: 28.59))
                p.addCurve(to: CGPoint(x: 9.77, y: 24), control1: CGPoint(x: 10.05, y: 27.14), control2: CGPoint(x: 9.77, y: 25.6))
                p.addCurve(to: CGPoint(x: 10.53, y: 19.41), control1: CGPoint(x: 9.77, y: 22.4), control2: CGPoint(x: 10.04, y: 20.86))
                p.addLine(to: CGPoint(x: 2.55, y: 13.22))
                p.addCurve(to: CGPoint(x: 0, y: 24), control1: CGPoint(x: 0.92, y: 16.46), control2: CGPoint(x: 0, y: 20.12))
                p.addCurve(to: CGPoint(x: 2.56, y: 34.78), control1: CGPoint(x: 0, y: 27.88), control2: CGPoint(x: 0.92, y: 31.54))
                p.addLine(to: CGPoint(x: 10.53, y: 28.59))
            }, with: .color(Color(hex: 0xFBBC05)))
            context.fill(path { p in
                p.move(to: CGPoint(x: 24, y: 48))
                p.addCurve(to: CGPoint(x: 39.89, y: 42.19), control1: CGPoint(x: 30.48, y: 48), control2: CGPoint(x: 35.93, y: 45.87))
                p.addLine(to: CGPoint(x: 32.16, y: 36.19))
                p.addCurve(to: CGPoint(x: 24, y: 38.49), control1: CGPoint(x: 30.01, y: 37.64), control2: CGPoint(x: 27.24, y: 38.49))
                p.addCurve(to: CGPoint(x: 10.53, y: 28.58), control1: CGPoint(x: 17.74, y: 38.49), control2: CGPoint(x: 12.43, y: 34.27))
                p.addLine(to: CGPoint(x: 2.55, y: 34.77))
                p.addCurve(to: CGPoint(x: 24, y: 48), control1: CGPoint(x: 6.51, y: 42.62), control2: CGPoint(x: 14.62, y: 48))
            }, with: .color(Color(hex: 0x34A853)))
        }
        .accessibilityHidden(true)
    }
}
