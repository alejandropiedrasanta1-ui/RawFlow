import Foundation
import ImageIO
import UniformTypeIdentifiers
// prevjpg salida_dir maxpx RAW... → salida_dir/<nombre>.jpg (la vista previa del RAW, como la ve rfui)
let a = Array(CommandLine.arguments.dropFirst())
let out = URL(fileURLWithPath: a[0]), mx = Int(a[1])!
for path in a.dropFirst(2) {
    let url = URL(fileURLWithPath: path)
    guard let s = CGImageSourceCreateWithURL(url as CFURL, nil) else { continue }
    let o: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageIfAbsent: true, kCGImageSourceThumbnailMaxPixelSize: mx,
                              kCGImageSourceCreateThumbnailWithTransform: true]
    guard let img = CGImageSourceCreateThumbnailAtIndex(s, 0, o as CFDictionary) else { continue }
    let dst = out.appendingPathComponent(url.deletingPathExtension().lastPathComponent + ".jpg")
    guard let d = CGImageDestinationCreateWithURL(dst as CFURL, UTType.jpeg.identifier as CFString, 1, nil) else { continue }
    CGImageDestinationAddImage(d, img, [kCGImageDestinationLossyCompressionQuality: 0.85] as CFDictionary)
    CGImageDestinationFinalize(d)
}
