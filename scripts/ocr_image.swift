import Foundation
import Vision
import AppKit

guard CommandLine.arguments.count > 1 else {
    fputs("Usage: ocr_image.swift <image-path>\n", stderr)
    exit(1)
}

let imagePath = CommandLine.arguments[1]
let url = URL(fileURLWithPath: imagePath)

guard let image = NSImage(contentsOf: url) else {
    fputs("Unable to load image at \(imagePath)\n", stderr)
    exit(1)
}

guard let tiff = image.tiffRepresentation,
      let bitmap = NSBitmapImageRep(data: tiff),
      let cgImage = bitmap.cgImage else {
    fputs("Unable to convert image to CGImage.\n", stderr)
    exit(1)
}

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
request.recognitionLanguages = ["zh-Hans", "zh-Hant", "en-US"]

let handler = VNImageRequestHandler(cgImage: cgImage, options: [:])
try handler.perform([request])

let observations = request.results ?? []
let lines = observations.compactMap { observation -> [String: Any]? in
    guard let candidate = observation.topCandidates(1).first else {
        return nil
    }
    let bbox = observation.boundingBox
    return [
        "text": candidate.string,
        "confidence": candidate.confidence,
        "bounding_box": [
            "x": bbox.origin.x,
            "y": bbox.origin.y,
            "width": bbox.size.width,
            "height": bbox.size.height
        ]
    ]
}

let fullText = lines.compactMap { $0["text"] as? String }.joined(separator: "\n")
let payload: [String: Any] = [
    "text": fullText,
    "lines": lines
]
let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
print(String(decoding: data, as: UTF8.self))
