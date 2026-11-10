import Foundation
import UIKit

/// The story card as a PNG (`shareStoryCard` / `svgToPngBlob` on the web).
/// Instagram and Snapchat take a picture for a story, not a sentence with a
/// link, and neither takes an SVG - so the server's card
/// (`sharing.story_card`, 1080x1920) is drawn here and handed to the share
/// sheet as a file. The server's SVG stays the one source of the card: its
/// words, wrapping, pill and colours are read from the markup, not restated.
///
/// It draws what `story_card` writes and nothing more: rectangles filled
/// with a colour or a linear or radial gradient (`objectBoundingBox`, the
/// SVG default), stroked and rounded; and text with `tspan` lines (`x`,
/// `dy`), its fill, size, weight and letter spacing. The faces are FAM's
/// own bundled Bricolage Grotesque and Geist, which the markup names first
/// (a browser drawing the SVG as an image falls back to Helvetica/Arial).
enum StoryCardRenderer {
    static func png(svg: String) -> Data? {
        guard let data = svg.data(using: .utf8) else { return nil }
        let reader = StoryCardSVG()
        let parser = XMLParser(data: data)
        parser.delegate = reader
        parser.shouldResolveExternalEntities = false
        guard parser.parse() else { return nil }
        return draw(reader)
    }

    private static func draw(_ card: StoryCardSVG) -> Data? {
        let size = CGSize(width: card.width, height: card.height)
        guard size.width > 0, size.height > 0 else { return nil }
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        let image = renderer.image { context in
            let cg = context.cgContext
            UIColor.black.setFill()
            cg.fill(CGRect(origin: .zero, size: size))
            for item in card.items {
                switch item {
                case .rect(let r):
                    drawRect(r, card: card, in: cg)
                case .text(let t):
                    drawText(t)
                }
            }
        }
        return image.pngData()
    }

    // MARK: - Rectangles

    private static func drawRect(_ r: StoryCardSVG.Rect, card: StoryCardSVG, in cg: CGContext) {
        let frame = CGRect(x: r.x, y: r.y, width: r.width, height: r.height)
        let path = UIBezierPath(roundedRect: frame, cornerRadius: r.rx)
        if let id = gradientID(r.fill), let g = card.gradients[id] {
            cg.saveGState()
            cg.addPath(path.cgPath)
            cg.clip()
            fillGradient(g, frame: frame, in: cg)
            cg.restoreGState()
        } else if let colour = StoryCardSVG.colour(r.fill, opacity: r.fillOpacity) {
            colour.setFill()
            path.fill()
        }
        if let stroke = StoryCardSVG.colour(r.stroke, opacity: 1), r.strokeWidth > 0 {
            stroke.setStroke()
            path.lineWidth = r.strokeWidth
            path.stroke()
        }
    }

    private static func gradientID(_ fill: String) -> String? {
        let trimmed = fill.trimmingCharacters(in: .whitespaces)
        guard trimmed.hasPrefix("url(#"), trimmed.hasSuffix(")") else { return nil }
        return String(trimmed.dropFirst(5).dropLast())
    }

    private static func fillGradient(_ g: StoryCardSVG.Gradient, frame: CGRect, in cg: CGContext) {
        let colours = g.stops.map { $0.colour.cgColor }
        let locations = g.stops.map { $0.offset }
        guard !colours.isEmpty,
              let space = CGColorSpace(name: CGColorSpace.sRGB),
              let gradient = CGGradient(colorsSpace: space, colors: colours as CFArray,
                                        locations: locations) else { return }
        // In the box's own units, as `objectBoundingBox` means.
        cg.translateBy(x: frame.minX, y: frame.minY)
        cg.scaleBy(x: frame.width, y: frame.height)
        let both: CGGradientDrawingOptions = [.drawsBeforeStartLocation, .drawsAfterEndLocation]
        if g.radial {
            let centre = CGPoint(x: g.cx, y: g.cy)
            cg.drawRadialGradient(gradient, startCenter: centre, startRadius: 0,
                                  endCenter: centre, endRadius: g.r, options: both)
        } else {
            cg.drawLinearGradient(gradient, start: CGPoint(x: g.x1, y: g.y1),
                                  end: CGPoint(x: g.x2, y: g.y2), options: both)
        }
    }

    // MARK: - Text

    private static func drawText(_ t: StoryCardSVG.TextRun) {
        let words = t.text
        guard !words.isEmpty else { return }
        let font = face(t.family, weight: t.weight, size: t.size)
        let colour = StoryCardSVG.colour(t.fill, opacity: 1) ?? .white
        let attributes: [NSAttributedString.Key: Any] = [
            .font: font,
            .foregroundColor: colour,
            .kern: t.letterSpacing,
        ]
        let line = NSAttributedString(string: words, attributes: attributes)
        // SVG places text on its baseline; UIKit draws from the line's top.
        line.draw(at: CGPoint(x: t.x, y: t.y - font.ascender))
    }

    private static func face(_ family: String, weight: Int, size: CGFloat) -> UIFont {
        let w: String
        if weight >= 700 {
            w = "Bold"
        } else if weight >= 600 {
            w = "SemiBold"
        } else if weight >= 500 {
            w = "Medium"
        } else {
            w = "Regular"
        }
        let name = family.contains("Bricolage") ? "BricolageGrotesque-" + w : "Geist-" + w
        if let font = UIFont(name: name, size: size) { return font }
        let system: UIFont.Weight = weight >= 700 ? .bold : (weight >= 600 ? .semibold : (weight >= 500 ? .medium : .regular))
        return UIFont.systemFont(ofSize: size, weight: system)
    }
}

/// The parts of the card's markup, in document order.
final class StoryCardSVG: NSObject, XMLParserDelegate {
    struct Stop {
        let offset: CGFloat
        let colour: UIColor
    }

    struct Gradient {
        var radial = false
        var x1: CGFloat = 0
        var y1: CGFloat = 0
        var x2: CGFloat = 1
        var y2: CGFloat = 0
        var cx: CGFloat = 0.5
        var cy: CGFloat = 0.5
        var r: CGFloat = 0.5
        var stops: [Stop] = []
    }

    struct Rect {
        let x: CGFloat
        let y: CGFloat
        let width: CGFloat
        let height: CGFloat
        let rx: CGFloat
        let fill: String
        let fillOpacity: CGFloat
        let stroke: String
        let strokeWidth: CGFloat
    }

    struct TextRun {
        var x: CGFloat
        var y: CGFloat
        var text: String
        let fill: String
        let family: String
        let size: CGFloat
        let weight: Int
        let letterSpacing: CGFloat
    }

    enum Item {
        case rect(Rect)
        case text(TextRun)
    }

    private(set) var width: CGFloat = 1080
    private(set) var height: CGFloat = 1920
    private(set) var gradients: [String: Gradient] = [:]
    private(set) var items: [Item] = []

    private var gradientID: String?
    private var gradient = Gradient()
    /// The `<text>` being read: its attributes, where the next line goes.
    private var textAttributes: [String: String]?
    private var cursorX: CGFloat = 0
    private var cursorY: CGFloat = 0
    private var run: TextRun?

    func parser(_ parser: XMLParser, didStartElement name: String, namespaceURI: String?,
                qualifiedName: String?, attributes: [String: String] = [:]) {
        switch name {
        case "svg":
            width = Self.number(attributes["width"], 1080)
            height = Self.number(attributes["height"], 1920)
        case "linearGradient":
            gradientID = attributes["id"]
            var g = Gradient()
            g.x1 = Self.fraction(attributes["x1"], 0)
            g.y1 = Self.fraction(attributes["y1"], 0)
            g.x2 = Self.fraction(attributes["x2"], 1)
            g.y2 = Self.fraction(attributes["y2"], 0)
            gradient = g
        case "radialGradient":
            gradientID = attributes["id"]
            var g = Gradient()
            g.radial = true
            g.cx = Self.fraction(attributes["cx"], 0.5)
            g.cy = Self.fraction(attributes["cy"], 0.5)
            g.r = Self.fraction(attributes["r"], 0.5)
            gradient = g
        case "stop":
            let opacity = Self.number(attributes["stop-opacity"], 1)
            let colour = Self.colour(attributes["stop-color"] ?? "#000000", opacity: opacity) ?? .clear
            gradient.stops.append(Stop(offset: Self.fraction(attributes["offset"], 0), colour: colour))
        case "rect":
            let rect = Rect(x: Self.number(attributes["x"], 0), y: Self.number(attributes["y"], 0),
                            width: Self.number(attributes["width"], 0),
                            height: Self.number(attributes["height"], 0),
                            rx: Self.number(attributes["rx"], 0),
                            fill: attributes["fill"] ?? "#000000",
                            fillOpacity: Self.number(attributes["fill-opacity"], 1),
                            stroke: attributes["stroke"] ?? "none",
                            strokeWidth: Self.number(attributes["stroke-width"], 1))
            items.append(.rect(rect))
        case "text":
            textAttributes = attributes
            cursorX = Self.number(attributes["x"], 0)
            cursorY = Self.number(attributes["y"], 0)
            run = newRun(x: cursorX, y: cursorY)
        case "tspan":
            closeRun()
            if let x = attributes["x"] { cursorX = Self.number(x, cursorX) }
            cursorY += Self.number(attributes["dy"], 0)
            run = newRun(x: cursorX, y: cursorY)
        default:
            break
        }
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) {
        guard textAttributes != nil, run != nil else { return }
        run?.text += string
    }

    func parser(_ parser: XMLParser, didEndElement name: String, namespaceURI: String?,
                qualifiedName: String?) {
        switch name {
        case "linearGradient", "radialGradient":
            if let id = gradientID { gradients[id] = gradient }
            gradientID = nil
        case "tspan":
            closeRun()
        case "text":
            closeRun()
            textAttributes = nil
        default:
            break
        }
    }

    private func newRun(x: CGFloat, y: CGFloat) -> TextRun? {
        guard let a = textAttributes else { return nil }
        return TextRun(x: x, y: y, text: "", fill: a["fill"] ?? "#000000",
                       family: a["font-family"] ?? "", size: Self.number(a["font-size"], 16),
                       weight: Int(Self.number(a["font-weight"], 400)),
                       letterSpacing: Self.number(a["letter-spacing"], 0))
    }

    /// A line ends: its words with the white space collapsed, as SVG does.
    private func closeRun() {
        guard var r = run else { return }
        run = nil
        let words = r.text.components(separatedBy: .whitespacesAndNewlines).filter { !$0.isEmpty }
        r.text = words.joined(separator: " ")
        if !r.text.isEmpty { items.append(.text(r)) }
    }

    // MARK: - Values

    static func number(_ text: String?, _ fallback: CGFloat) -> CGFloat {
        guard let text else { return fallback }
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if let v = Double(trimmed) { return CGFloat(v) }
        let digits = trimmed.prefix { "0123456789.-".contains($0) }
        if let v = Double(String(digits)) { return CGFloat(v) }
        return fallback
    }

    /// "60%" or "0.6".
    static func fraction(_ text: String?, _ fallback: CGFloat) -> CGFloat {
        guard let text else { return fallback }
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.hasSuffix("%") {
            return number(String(trimmed.dropLast()), fallback * 100) / 100
        }
        return number(trimmed, fallback)
    }

    /// "#RRGGBB" (or "#RGB"); nil for "none" or anything else.
    static func colour(_ text: String, opacity: CGFloat) -> UIColor? {
        var hex = text.trimmingCharacters(in: .whitespaces)
        guard hex.hasPrefix("#") else { return nil }
        hex.removeFirst()
        if hex.count == 3 {
            var wide = ""
            for c in hex { wide.append(c); wide.append(c) }
            hex = wide
        }
        guard hex.count == 6, let value = UInt32(hex, radix: 16) else { return nil }
        let r = CGFloat((value >> 16) & 0xFF) / 255
        let g = CGFloat((value >> 8) & 0xFF) / 255
        let b = CGFloat(value & 0xFF) / 255
        return UIColor(red: r, green: g, blue: b, alpha: opacity)
    }
}
