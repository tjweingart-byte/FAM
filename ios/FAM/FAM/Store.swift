import Foundation
import StoreKit

/// Apple in-app purchase (§653): the three plans the web sells through
/// Stripe, sold here through StoreKit 2 so nobody leaves the app to pay.
///
/// The server decides the plan, never this file. Every transaction Apple
/// hands the app - a purchase, a renewal noticed at launch, a restore - is
/// sent to `/api/billing/apple` as Apple signed it (`jwsRepresentation`);
/// the server checks the signature against Apple's root and that the
/// purchase was made with this account's `appAccountToken`, then sets the
/// plan. A transaction is finished only once the server has it, so one
/// that could not be sent is offered again by `Transaction.updates`.
@MainActor
final class FAMStore: ObservableObject {
    /// Plan ("student", "single", "fam") -> the App Store's product, with
    /// Apple's price for this listener's storefront (`displayPrice`).
    @Published private(set) var products: [String: Product] = [:]
    /// A purchase or restore is under way: the sheet's buttons wait.
    @Published private(set) var busy = false

    private let api: FAMAPI
    private var updates: Task<Void, Never>?
    private var setup: AppleSetup?
    /// Called once the server has a new plan, so the screens re-read it.
    var onPlanChanged: (() -> Void)?

    init(api: FAMAPI) {
        self.api = api
    }

    /// Listen for transactions Apple delivers outside a purchase - a
    /// renewal, a purchase approved later (Ask to Buy), one made on another
    /// device. Started once, at launch.
    func start() {
        guard updates == nil else { return }
        updates = Task { [weak self] in
            for await result in Transaction.updates {
                await self?.deliver(result)
            }
        }
    }

    /// The products, from the plan -> product id map the server gives.
    func load() async {
        do {
            let found = try await api.appleSetup()
            setup = found
            let byID = try await Product.products(for: Array(found.products.values))
            var map: [String: Product] = [:]
            for (plan, id) in found.products {
                if let product = byID.first(where: { $0.id == id }) { map[plan] = product }
            }
            products = map
        } catch {
            // No products is an honest state: the sheet says the plans
            // could not be loaded rather than drawing buttons that fail.
            products = [:]
        }
    }

    enum Outcome { case bought, pending, cancelled }

    /// Apple took the payment but the server has not confirmed it. The
    /// transaction is left unfinished, so `Transaction.updates` offers it
    /// again; Restore purchases sends it now.
    struct Unconfirmed: Error {}

    /// Apple's sheet for one plan, bought with this account's token.
    func buy(_ plan: String) async throws -> Outcome {
        guard let product = products[plan] else { throw FAMError.unreadable }
        // Asked fresh for every purchase, never kept: the token is the
        // signed-in account's, and another account may have signed in on
        // this phone since the sheet last loaded (§656 review).
        let fresh = try await api.appleSetup()
        setup = fresh
        guard let token = UUID(uuidString: fresh.token) else {
            throw FAMError.unreadable
        }
        busy = true
        defer { busy = false }
        let result = try await product.purchase(options: [.appAccountToken(token)])
        switch result {
        case .success(let verification):
            do {
                try await send(verification)
            } catch {
                throw Unconfirmed()
            }
            return .bought
        case .pending:
            // Ask to Buy, or a bank step: it arrives on `Transaction.updates`.
            return .pending
        case .userCancelled:
            return .cancelled
        @unknown default:
            return .cancelled
        }
    }

    /// "Restore purchases": ask Apple for this Apple ID's current
    /// subscriptions and send each to the server.
    /// True when anything was restored. A transaction the server refuses
    /// (bought by another FAM account on this Apple ID) is skipped, not fatal.
    @discardableResult
    func restore() async throws -> Bool {
        busy = true
        defer { busy = false }
        try await AppStore.sync()
        var restored = false
        for await result in Transaction.currentEntitlements {
            if (try? await send(result)) != nil { restored = true }
        }
        return restored
    }

    private func deliver(_ result: VerificationResult<Transaction>) async {
        try? await send(result)
    }

    /// The signed transaction to the server, then finished.
    private func send(_ result: VerificationResult<Transaction>) async throws {
        // An unverified transaction is still sent: the server checks the
        // signature itself and is the one whose answer counts.
        _ = try await api.appleTransaction(result.jwsRepresentation)
        if case .verified(let transaction) = result {
            await transaction.finish()
        }
        onPlanChanged?()
    }
}
