import XCTest
@testable import FAMSafety

/// The shapes `/api/v1/consent`, `/api/v1/report` and `/api/v1/blocks`
/// return, as the server writes them (`tests/test_ios_safety_contract.py`
/// checks the server still does).
final class DecodingTests: XCTestCase {
    func testConsentDecodesUnasked() throws {
        let json = """
        {"ai": {"scope": "ai", "provider": "Anthropic", "version": 1,
                "notice": {"title": "t", "body": "b", "unaffected": "u",
                           "allow": "Allow", "decline": "Not now"},
                "asked": false, "given": false,
                "answered_version": null, "at": null}}
        """.data(using: .utf8)!
        let ai = try SafetyAPI.decoder.decode(ConsentEnvelope.self, from: json).ai
        XCTAssertFalse(ai.given)
        XCTAssertNil(ai.answeredVersion)
        XCTAssertEqual(ai.notice.allow, "Allow")
    }

    func testReportOptionsDecode() throws {
        let json = """
        {"reasons": [{"id": "spam", "label": "Spam or scams"}],
         "kinds": ["comment"], "review_hours": 24, "contact": "ian@familiarize.net"}
        """.data(using: .utf8)!
        let options = try SafetyAPI.decoder.decode(ReportOptions.self, from: json)
        XCTAssertEqual(options.reviewHours, 24)
        XCTAssertEqual(options.reasons.first?.id, "spam")
    }

    func testAReportNamesItsSubject() {
        let comment = ReportSubject.comment(id: 7).body(reason: "spam")
        XCTAssertEqual(comment["kind"] as? String, "comment")
        XCTAssertEqual(comment["target"] as? String, "7")
        let episode = ReportSubject.episode(query: "why", minutes: 2).body(reason: "false")
        XCTAssertEqual(episode["query"] as? String, "why")
        XCTAssertEqual(episode["minutes"] as? Int, 2)
        XCTAssertNil(episode["target"])
    }

    func testOnlyTheConsentHeaderIsAConsentRefusal() {
        let url = URL(string: "https://example.com/api/v1/audio")!
        let asked = HTTPURLResponse(url: url, statusCode: 403, httpVersion: nil,
                                    headerFields: ["X-FAM-Consent": "ai"])!
        let other = HTTPURLResponse(url: url, statusCode: 403, httpVersion: nil,
                                    headerFields: [:])!
        XCTAssertTrue(isConsentRefusal(asked))
        XCTAssertFalse(isConsentRefusal(other))
    }
}
