import CoreGraphics
import Foundation

let targetNames = Set(["WeChat", "微信"])
let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]] ?? []

func string(_ dict: [String: Any], _ key: String) -> String? {
    return dict[key] as? String
}

func int(_ dict: [String: Any], _ key: String) -> Int? {
    if let n = dict[key] as? NSNumber { return n.intValue }
    return nil
}

var best: [String: Any]?
var bestArea = 0

for window in windows {
    guard let owner = string(window, kCGWindowOwnerName as String), targetNames.contains(owner) else {
        continue
    }
    let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]
    let width = (bounds["Width"] as? NSNumber)?.intValue ?? 0
    let height = (bounds["Height"] as? NSNumber)?.intValue ?? 0
    let area = width * height
    if area > bestArea {
        best = window
        bestArea = area
    }
}

guard let window = best else {
    fputs("No visible WeChat window found.\n", stderr)
    exit(1)
}

let bounds = window[kCGWindowBounds as String] as? [String: Any] ?? [:]
let payload: [String: Any] = [
    "window_id": int(window, kCGWindowNumber as String) ?? 0,
    "owner_name": string(window, kCGWindowOwnerName as String) ?? "",
    "window_name": string(window, kCGWindowName as String) ?? "",
    "bounds": [
        "x": (bounds["X"] as? NSNumber)?.intValue ?? 0,
        "y": (bounds["Y"] as? NSNumber)?.intValue ?? 0,
        "width": (bounds["Width"] as? NSNumber)?.intValue ?? 0,
        "height": (bounds["Height"] as? NSNumber)?.intValue ?? 0
    ]
]

let data = try JSONSerialization.data(withJSONObject: payload, options: [.prettyPrinted])
print(String(decoding: data, as: UTF8.self))
