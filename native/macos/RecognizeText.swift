// pixelfm-ocr: reads the text in a screenshot with Apple's Vision framework.
//
//   pixelfm-ocr <image>                an image file (PNG, JPEG, HEIC, ...)
//   pixelfm-ocr --clipboard <out.png>  save the clipboard's image (⌃⇧⌘4
//                                      screenshots) as a PNG, then read it
//                                      like any file
//
// Reading prints one JSON object: {"width":px,"height":px,"lines":[{"text":..,
// "x":..,"y":..,"w":..,"h":..,"confidence":0..1}]}, boxes in pixels from the
// top left. PixelFM turns the lines into songs; this tool only reads text.
// It runs on the Mac only: the image never leaves the computer.
//
// Exit status: 0 done, 2 unreadable image or bad usage, 3 no image on the
// clipboard.

import AppKit
import Foundation
import Vision

func fail(_ message: String, _ status: Int32) -> Never {
    FileHandle.standardError.write(Data("pixelfm-ocr: \(message)\n".utf8))
    exit(status)
}

func clipboardImage() -> CGImage? {
    guard let image = NSImage(pasteboard: NSPasteboard.general) else { return nil }
    var rect = CGRect(origin: .zero, size: image.size)
    return image.cgImage(forProposedRect: &rect, context: nil, hints: nil)
}

func fileImage(_ path: String) -> CGImage? {
    let url = URL(fileURLWithPath: path)
    guard let source = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    return CGImageSourceCreateImageAtIndex(source, 0, nil)
}

func savePNG(_ image: CGImage, to path: String) -> Bool {
    let url = URL(fileURLWithPath: path)
    guard let target = CGImageDestinationCreateWithURL(
        url as CFURL, "public.png" as CFString, 1, nil
    ) else { return false }
    CGImageDestinationAddImage(target, image, nil)
    return CGImageDestinationFinalize(target)
}

let arguments = Array(CommandLine.arguments.dropFirst())
if arguments.count == 2 && arguments[0] == "--clipboard" {
    guard let found = clipboardImage() else { fail("no image on the clipboard", 3) }
    guard savePNG(found, to: arguments[1]) else {
        fail("can't save the clipboard's image to \(arguments[1])", 2)
    }
    exit(0)
}
guard arguments.count == 1 else {
    fail("usage: pixelfm-ocr <image> | --clipboard <out.png>", 2)
}
guard let image = fileImage(arguments[0]) else {
    fail("can't open \(arguments[0]) as an image", 2)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
if #available(macOS 13.0, *) {
    request.automaticallyDetectsLanguage = true
}
do {
    try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
} catch {
    fail("text recognition failed: \(error.localizedDescription)", 2)
}

let width = Double(image.width)
let height = Double(image.height)
var lines: [[String: Any]] = []
for observation in request.results ?? [] {
    guard let best = observation.topCandidates(1).first else { continue }
    let box = observation.boundingBox  // normalised, origin at the bottom left
    lines.append([
        "text": best.string,
        "x": box.minX * width,
        "y": (1 - box.maxY) * height,
        "w": box.width * width,
        "h": box.height * height,
        "confidence": Double(best.confidence),
    ])
}
let output: [String: Any] = ["width": width, "height": height, "lines": lines]
guard let data = try? JSONSerialization.data(withJSONObject: output) else {
    fail("could not encode the result", 2)
}
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write(Data("\n".utf8))
