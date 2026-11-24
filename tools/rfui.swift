// rfui — ayudante de RawFlow para manejar apps sin API (Imagen, Bridge).
//   rfui ocr                      -> JSON [{t, x, y, w, h}] en puntos (pantalla principal, sin RawFlow encima)
//   rfui shot RUTA.png            -> la misma captura, a archivo (pruebas)
//   rfui click X Y [n]            -> clic (n=2 doble clic)
//   rfui drag X0 Y0 X1 Y1         -> arrastrar
//   rfui scroll X Y DY N          -> rueda
//   rfui type "texto"             -> escribir texto (unicode)
//   rfui key CODE [cmd,shift,alt,ctrl]
//   rfui block [segundos]         -> bloquea ratón/trackpad/teclado físicos y deja el cursor quieto
//                                    (0 o nada = sin límite; Esc ×3 sale; stdin cerrado suelta) — RawFlow ya no lo usa
//   rfui escuchar                 -> NO bloquea nada: solo avisa («CANCEL») si pulsas Esc ×3; stdin cerrado termina
//   rfui check                    -> "ax=1 screen=1" permisos
//   rfui cull < rutas             -> Selección IA: una línea JSON por foto (caras, ojos, inclinación, nitidez, luz, hora, EV,
//                                    escena, estética, parecido con las 4 anteriores), con la IA del Mac, sin internet
//   rfui caras < JSON por línea    -> vuelve a buscar caras en zonas concretas (las que Vision no vio en una toma)
import Foundation
import ImageIO
import CoreGraphics
import Vision
import ApplicationServices
import ScreenCaptureKit

let args = Array(CommandLine.arguments.dropFirst())
let src = CGEventSource(stateID: .hidSystemState)
func num(_ i: Int) -> Double { Double(args[i])! }
func mouse(_ t: CGEventType, _ p: CGPoint, clicks: Int64 = 1) {
    let e = CGEvent(mouseEventSource: src, mouseType: t, mouseCursorPosition: p, mouseButton: .left)
    e?.setIntegerValueField(.mouseEventClickState, value: clicks)
    e?.post(tap: .cghidEventTap)
}
func flags(_ s: String) -> CGEventFlags {
    var f: CGEventFlags = []
    for m in s.split(separator: ",") {
        switch m { case "cmd": f.insert(.maskCommand); case "shift": f.insert(.maskShift)
        case "alt": f.insert(.maskAlternate); case "ctrl": f.insert(.maskControl); default: break }
    }
    return f
}

// Captura la pantalla principal SIN las ventanas de RawFlow: se ve lo que hay debajo (Imagen, Bridge…),
// así RawFlow puede quedarse encima tapándolo todo mientras maneja esas apps.
func captureBelowRawFlow(_ did: CGDirectDisplayID) -> CGImage? {
    let me = getppid()
    let extra = Int32(ProcessInfo.processInfo.environment["RFUI_EXCLUDE_PID"] ?? "") ?? -1
    let sem = DispatchSemaphore(value: 0)
    var result: CGImage? = nil
    SCShareableContent.getExcludingDesktopWindows(false, onScreenWindowsOnly: true) { content, _ in
        guard let content = content, let disp = content.displays.first(where: { $0.displayID == did }) else {
            sem.signal(); return }
        let skip = content.applications.filter {
            $0.bundleIdentifier == "com.rawflow.app" || $0.processID == me || $0.processID == extra }
        let filter = SCContentFilter(display: disp, excludingApplications: skip, exceptingWindows: [])
        let cfg = SCStreamConfiguration()
        var scale: CGFloat = 2                        // Retina: píxeles reales (como screencapture)
        if let m = CGDisplayCopyDisplayMode(did), m.width > 0 { scale = CGFloat(m.pixelWidth) / CGFloat(m.width) }
        cfg.width = Int(CGFloat(disp.width) * max(scale, 1)); cfg.height = Int(CGFloat(disp.height) * max(scale, 1))
        cfg.showsCursor = false
        SCScreenshotManager.captureImage(contentFilter: filter, configuration: cfg) { im, _ in
            result = im; sem.signal() }
    }
    _ = sem.wait(timeout: .now() + 8)
    return result
}

func captureFull() -> CGImage? {
    let tmp = NSTemporaryDirectory() + "rfui-\(getpid()).png"
    let sc = Process(); sc.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
    sc.arguments = ["-x", "-m", "-t", "png", tmp]; try? sc.run(); sc.waitUntilExit()
    defer { try? FileManager.default.removeItem(atPath: tmp) }
    guard let ds = CGDataProvider(filename: tmp) else { return nil }
    return CGImage(pngDataProviderSource: ds, decode: nil, shouldInterpolate: false, intent: .defaultIntent)
}

// ---- bloqueo de entrada: descarta ratón, trackpad y teclado FÍSICOS (el trackpad del Mac y CUALQUIER ratón,
// trackpad o teclado USB/Bluetooth: todos llegan con PID 0) y deja pasar los sintéticos (los de RawFlow/rfui y
// System Events llevan el PID de quien los envía). El cursor se queda quieto donde lo dejó RawFlow.
// Dura lo que dure el evento (sin límite de horas). Salidas:
//   · Esc ×3 (tres pulsaciones en 2,5 s; mantener la tecla no cuenta) → «CANCEL» y se suelta al instante.
//   · stdin cerrado (RawFlow lo soltó o se cerró) o RawFlow muerto → se suelta.
//   · Con la pantalla bloqueada (al despertar del reposo) deja pasar todo, para poder escribir la contraseña.
// «rfui escuchar» (lo que usa RawFlow desde el 23-nov-2026): el mismo filtro, pero deja pasar TODO (ratón, trackpad y teclado
// siguen libres) y solo mira si pulsas Esc ×3 para cancelar. Imprime «LISTENING …» en vez de «BLOCKING …».
var passThrough = false
var blockTap: CFMachPort? = nil
var escTimes: [Double] = []
var blockLog: FileHandle? = nil
var blockSeen = 0
var holdPoint = CGPoint.zero                  // donde se queda el cursor (solo lo mueve RawFlow)
var screenLocked = false
let testPID = Int64(ProcessInfo.processInfo.environment["RFUI_BLOCK_TEST_PID"] ?? "") ?? -1   // pruebas: ese PID = hardware
let moveTypes: [CGEventType] = [.mouseMoved, .leftMouseDragged, .rightMouseDragged, .otherMouseDragged]
let pointerTypes: [CGEventType] = moveTypes + [.leftMouseDown, .leftMouseUp, .rightMouseDown, .rightMouseUp,
                                               .otherMouseDown, .otherMouseUp]
let blockCallback: CGEventTapCallBack = { _, type, event, _ in
    if type == .tapDisabledByTimeout || type == .tapDisabledByUserInput {
        if let t = blockTap { CGEvent.tapEnable(tap: t, enable: true) }
        return Unmanaged.passUnretained(event)
    }
    let pid = event.getIntegerValueField(.eventSourceUnixProcessID)
    if blockSeen < 40, let h = blockLog {
        blockSeen += 1
        h.write("type=\(type.rawValue) pid=\(pid)\n".data(using: .utf8)!)
    }
    if pid != 0 && pid != testPID {                 // sintético (RawFlow): pasa, y el cursor se queda donde lo deje
        if pointerTypes.contains(type) { holdPoint = event.location }
        return Unmanaged.passUnretained(event)
    }
    if screenLocked { return Unmanaged.passUnretained(event) }
    if type == .keyDown && event.getIntegerValueField(.keyboardEventKeycode) == 53
        && event.getIntegerValueField(.keyboardEventAutorepeat) == 0 {          // Esc ×3 = salir
        let now = Date().timeIntervalSince1970
        escTimes = escTimes.filter { now - $0 < 2.5 } + [now]
        if escTimes.count >= 3 {
            escTimes = []
            print("CANCEL"); fflush(stdout)
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { exit(0) }   // la 3.ª Esc no llega a ninguna app
        }
    }
    if passThrough { return Unmanaged.passUnretained(event) }                // «rfui escuchar»: nada se bloquea
    if moveTypes.contains(type) { CGWarpMouseCursorPosition(holdPoint) }     // el cursor no se mueve
    return nil
}

func isScreenLocked() -> Bool {
    guard let d = CGSessionCopyCurrentDictionary() as? [String: Any] else { return false }
    if let v = d["CGSSessionScreenIsLocked"] as? Bool { return v }
    if let v = d["CGSSessionScreenIsLocked"] as? Int { return v != 0 }
    return false
}

func runBlock(maxSeconds: Double) {
    let mask: CGEventMask = ~CGEventMask(0)
    if let path = ProcessInfo.processInfo.environment["RFUI_BLOCK_LOG"] {
        FileManager.default.createFile(atPath: path, contents: nil); blockLog = FileHandle(forWritingAtPath: path)
    }
    holdPoint = CGEvent(source: nil)?.location ?? .zero
    screenLocked = isScreenLocked()
    for loc in [CGEventTapLocation.cghidEventTap, .cgSessionEventTap] {
        blockTap = CGEvent.tapCreate(tap: loc, place: .headInsertEventTap, options: .defaultTap,
                                     eventsOfInterest: mask, callback: blockCallback, userInfo: nil)
        if blockTap != nil {
            print("\(passThrough ? "LISTENING" : "BLOCKING") \(loc == .cghidEventTap ? "hid" : "session")"); break
        }
    }
    fflush(stdout)
    guard let tap = blockTap else { print("ERROR no event tap"); exit(3) }
    let src = CFMachPortCreateRunLoopSource(nil, tap, 0)
    CFRunLoopAddSource(CFRunLoopGetMain(), src, .commonModes)
    CGEvent.tapEnable(tap: tap, enable: true)
    let parent = getppid(), t0 = Date()
    Thread.detachNewThread {                         // stdin cerrado (RawFlow terminó o se cerró) → soltar
        while let _ = readLine() {}
        exit(0)
    }
    Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { _ in
        if getppid() != parent || (maxSeconds > 0 && Date().timeIntervalSince(t0) > maxSeconds) { exit(0) }
        let locked = isScreenLocked()
        if locked != screenLocked {
            screenLocked = locked
            print(locked ? "PAUSE pantalla bloqueada" : "RESUME"); fflush(stdout)
        }
        if !CGEvent.tapIsEnabled(tap: tap) { CGEvent.tapEnable(tap: tap, enable: true) }   // por si macOS lo apagó
    }
    CFRunLoopRun()
}


// ---------------------------------------------------------------- Selección IA (rfui cull)
func cullPreview(_ url: URL) -> (CGImage?, [String: Double]) {
    guard let s = CGImageSourceCreateWithURL(url as CFURL, nil) else { return (nil, [:]) }
    let o: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageIfAbsent: true,       // la vista previa del RAW
                              kCGImageSourceThumbnailMaxPixelSize: 1600,
                              kCGImageSourceCreateThumbnailWithTransform: true]
    let img = CGImageSourceCreateThumbnailAtIndex(s, 0, o as CFDictionary)
    var info: [String: Double] = [:]
    if let p = CGImageSourceCopyPropertiesAtIndex(s, 0, nil) as? [String: Any], let ex = p["{Exif}"] as? [String: Any] {
        if let d = ex["DateTimeOriginal"] as? String {                       // hora de la toma, con centésimas
            let f = DateFormatter(); f.locale = Locale(identifier: "en_US_POSIX"); f.dateFormat = "yyyy:MM:dd HH:mm:ss"
            if let dt = f.date(from: d) {
                var t = dt.timeIntervalSince1970
                if let ss = ex["SubsecTimeOriginal"] as? String, let v = Double("0." + ss) { t += v }
                info["t"] = t
            }
        }
        // exposición elegida en la cámara (EV a ISO 100): dos tomas iguales con EV distinto son un horquillado (HDR),
        // no fotos repetidas
        let n = (ex["FNumber"] as? NSNumber)?.doubleValue ?? 0, sh = (ex["ExposureTime"] as? NSNumber)?.doubleValue ?? 0
        let iso = ((ex["ISOSpeedRatings"] as? [NSNumber])?.first)?.doubleValue ?? 0
        if n > 0, sh > 0, iso > 0 { info["ev"] = log2(n * n / sh) - log2(iso / 100) }
    }
    return (img, info)
}

// la observación de «lista» que corresponde a la cara «f» (mismo recuadro): Vision no promete el mismo orden
func sameFace(_ f: VNFaceObservation, _ lista: [VNFaceObservation]) -> VNFaceObservation? {
    var best: VNFaceObservation? = nil, bd = 1e9
    for g in lista {
        let d = abs(Double(g.boundingBox.midX - f.boundingBox.midX)) + abs(Double(g.boundingBox.midY - f.boundingBox.midY))
            + abs(Double(g.boundingBox.width - f.boundingBox.width))
        if d < bd { bd = d; best = g }
    }
    return bd < 0.02 ? best : nil
}

func grayPixels(_ img: CGImage, _ w: Int) -> ([UInt8], Int, Int) {
    let h = max(1, Int(Double(img.height) * Double(w) / Double(img.width)))
    var px = [UInt8](repeating: 0, count: w * h)
    let ctx = CGContext(data: &px, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                        space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
    ctx.interpolationQuality = .medium
    ctx.draw(img, in: CGRect(x: 0, y: 0, width: w, height: h))
    return (px, w, h)
}

// luz media, % negro, % quemado, desviación y nitidez (varianza del laplaciano) de un rectángulo (píxeles)
func regionStats(_ px: [UInt8], _ W: Int, _ H: Int, _ r: CGRect) -> [String: Double] {
    let x0 = max(1, Int(r.minX)), y0 = max(1, Int(r.minY)), x1 = min(W - 2, Int(r.maxX)), y1 = min(H - 2, Int(r.maxY))
    if x1 <= x0 + 2 || y1 <= y0 + 2 { return ["lum": -1, "nit": -1] }
    var n = 0.0, sum = 0.0, sq = 0.0, dark = 0.0, blown = 0.0, ls = 0.0, lsq = 0.0
    for y in y0...y1 { for x in x0...x1 {
        let v = Double(px[y * W + x]); n += 1; sum += v; sq += v * v
        if v < 12 { dark += 1 }; if v > 250 { blown += 1 }
        let l = 4 * v - Double(px[y * W + x - 1]) - Double(px[y * W + x + 1]) - Double(px[(y - 1) * W + x]) - Double(px[(y + 1) * W + x])
        ls += l; lsq += l * l } }
    let mean = sum / n, lm = ls / n
    return ["lum": mean, "negro": dark / n * 100, "quemado": blown / n * 100,
            "desv": max(0, sq / n - mean * mean).squareRoot(), "nit": max(0, lsq / n - lm * lm)]
}

// alto/ancho del ojo en píxeles, medido A LO LARGO DEL OJO (de comisura a comisura): con la cabeza ladeada, el
// recuadro recto de un ojo cerrado salía alto y parecía abierto
func eyeRatio(_ pts: [CGPoint], _ fw: Double, _ fh: Double) -> Double {
    guard pts.count >= 4 else { return -1 }
    let p = pts.map { (Double($0.x) * fw, Double($0.y) * fh) }
    var a = 0, b = 1, far = -1.0
    for i in 0..<p.count { for j in (i + 1)..<p.count {
        let d = (p[i].0 - p[j].0) * (p[i].0 - p[j].0) + (p[i].1 - p[j].1) * (p[i].1 - p[j].1)
        if d > far { far = d; a = i; b = j } } }                       // las comisuras: los dos puntos más alejados
    let ang = atan2(p[b].1 - p[a].1, p[b].0 - p[a].0), c = cos(-ang), s = sin(-ang)
    let xs = p.map { $0.0 * c - $0.1 * s }, ys = p.map { $0.0 * s + $0.1 * c }
    let wd = xs.max()! - xs.min()!, ht = ys.max()! - ys.min()!
    return wd > 0 ? ht / wd : -1
}

// La vista previa GRANDE del RAW (las cámaras guardan una a tamaño completo, ≈50 ms): ojos, calidad, nitidez y luz de
// cada cara se miden ahí. En una foto de grupo una cara mide ~50 px en la de 1600 y ~200 px en la grande.
func bigPreview(_ url: URL) -> CGImage? {
    guard let s = CGImageSourceCreateWithURL(url as CFURL, nil) else { return nil }
    let o: [CFString: Any] = [kCGImageSourceCreateThumbnailFromImageIfAbsent: true,
                              kCGImageSourceThumbnailMaxPixelSize: 6400,
                              kCGImageSourceCreateThumbnailWithTransform: true]
    return CGImageSourceCreateThumbnailAtIndex(s, 0, o as CFDictionary)
}

// luz y nitidez de una cara, recortada de la imagen grande y llevada a ≤256 px de ancho (comparables entre tomas)
func faceStats(_ img: CGImage, _ b: CGRect) -> [String: Double] {
    let W = Double(img.width), H = Double(img.height)
    let r = CGRect(x: b.minX * W, y: (1 - b.maxY) * H, width: b.width * W, height: b.height * H).integral
        .intersection(CGRect(x: 0, y: 0, width: W, height: H))
    guard r.width >= 12, r.height >= 12, let crop = img.cropping(to: r) else { return ["lum": -1, "nit": -1] }
    let (px, cw, ch) = grayPixels(crop, min(256, Int(r.width)))
    return regionStats(px, cw, ch, CGRect(x: 0, y: 0, width: cw, height: ch))
}

// rfui caras < JSON por línea {"foto": ruta, "cajas": [[x, y, w, h], …]}: vuelve a buscar una cara en esas zonas
// (Vision, en una foto de grupo, a veces no ve en una toma una cara que en la de al lado sí). Por cada caja:
// la cara encontrada ahí («rescatada», con ojos, calidad, giro, nitidez y luz) o «oculta» (tapada o girada).
func runFaces() {
    while let line = readLine() {
        guard let data = line.data(using: .utf8),
              let req = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any],
              let path = req["foto"] as? String, let cajas = req["cajas"] as? [[Double]] else { continue }
        let url = URL(fileURLWithPath: path)
        var o: [String: Any] = ["foto": url.lastPathComponent]
        guard let img = bigPreview(url) else {
            o["error"] = "sin vista previa"
            print(String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!); fflush(stdout); continue
        }
        let handler = VNImageRequestHandler(cgImage: img, options: [:])
        var found: [[String: Any]] = [], hidden: [[Double]] = []
        for c in cajas where c.count == 4 {
            let b = CGRect(x: c[0], y: c[1], width: c[2], height: c[3])
            let roi = b.insetBy(dx: -0.6 * b.width, dy: -0.6 * b.height).intersection(CGRect(x: 0, y: 0, width: 1, height: 1))
            let det = VNDetectFaceRectanglesRequest(); det.revision = VNDetectFaceRectanglesRequestRevision3
            det.regionOfInterest = roi
            try? handler.perform([det])
            var best: VNFaceObservation? = nil, bd = 1e9
            for f in det.results ?? [] {
                // Vision (macOS 27) da la caja respecto a TODA la foto; por si otra versión la da respecto a la zona,
                // se prueban las dos lecturas y vale la que cae en el sitio
                let rel = CGRect(x: roi.minX + f.boundingBox.minX * roi.width, y: roi.minY + f.boundingBox.minY * roi.height,
                                 width: f.boundingBox.width * roi.width, height: f.boundingBox.height * roi.height)
                let dAbs = hypot(Double(f.boundingBox.midX - b.midX), Double(f.boundingBox.midY - b.midY))
                let dRel = hypot(Double(rel.midX - b.midX), Double(rel.midY - b.midY))
                let fb = dAbs <= dRel ? f.boundingBox : rel
                let d = min(dAbs, dRel)
                let ratio = Double(fb.width / max(b.width, 1e-6))
                if d <= 0.6 * Double(b.width), ratio > 0.5, ratio < 2, d < bd {
                    bd = d
                    best = VNFaceObservation(requestRevision: VNDetectFaceRectanglesRequestRevision3, boundingBox: fb,
                                             roll: f.roll, yaw: f.yaw, pitch: f.pitch)
                }
            }
            guard let face = best else { hidden.append(c); continue }
            let lm = VNDetectFaceLandmarksRequest(); lm.inputFaceObservations = [face]; lm.constellation = .constellation76Points
            let q = VNDetectFaceCaptureQualityRequest(); q.inputFaceObservations = [face]
            try? handler.perform([lm, q])
            let fb = face.boundingBox, st = faceStats(img, fb)
            let fw = Double(fb.width) * Double(img.width), fh = Double(fb.height) * Double(img.height)
            var d: [String: Any] = ["x": fb.minX, "y": fb.minY, "w": fb.width, "h": fb.height, "lum": st["lum"] ?? -1,
                                    "nit": st["nit"] ?? -1, "px": Int(fw), "rescatada": true]
            if let p = face.pitch { d["pitch"] = p.doubleValue }
            if let y = face.yaw { d["yaw"] = y.doubleValue }
            if let r = face.roll { d["roll"] = r.doubleValue }
            let lf = lm.results?.first
            d["ojoI"] = eyeRatio(lf?.landmarks?.leftEye?.normalizedPoints ?? [], fw, fh)
            d["ojoD"] = eyeRatio(lf?.landmarks?.rightEye?.normalizedPoints ?? [], fw, fh)
            if let c = q.results?.first?.faceCaptureQuality { d["calidad"] = Double(c) }
            found.append(d)
        }
        o["caras"] = found
        o["ocultas"] = hidden
        print(String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!)
        fflush(stdout)
    }
}

let CULL_BACK = 4                   // parecido con las 4 tomas anteriores (ráfagas, y dos cámaras intercaladas)

func runCull() {
    var prevFPs: [VNFeaturePrintObservation?] = []          // la última, al final
    func remember(_ fp: VNFeaturePrintObservation?) {
        prevFPs.append(fp)
        if prevFPs.count > CULL_BACK { prevFPs.removeFirst() }
    }
    while let line = readLine() {
        let path = line.trimmingCharacters(in: .whitespaces)
        if path.isEmpty { continue }
        let url = URL(fileURLWithPath: path)
        var o: [String: Any] = ["foto": url.lastPathComponent]
        let (prev, info) = cullPreview(url)
        guard let img = prev else {
            o["error"] = "sin vista previa"
            print(String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!); fflush(stdout)
            remember(nil); continue
        }
        for (k, v) in info { o[k] = v }
        let handler = VNImageRequestHandler(cgImage: img, options: [:])
        let rects = VNDetectFaceRectanglesRequest()
        rects.revision = VNDetectFaceRectanglesRequestRevision3
        let humans = VNDetectHumanRectanglesRequest()
        let classify = VNClassifyImageRequest()
        let fp = VNGenerateImageFeaturePrintRequest()
        var reqs: [VNRequest] = [rects, humans, classify, fp]
        var aest: VNRequest? = nil
        if #available(macOS 15.0, *) { let a = VNCalculateImageAestheticsScoresRequest(); reqs.append(a); aest = a }
        try? handler.perform(reqs)
        let faces = rects.results ?? []
        var lmFaces: [VNFaceObservation] = [], qual: [VNFaceObservation] = []
        var src = img                                          // donde se miden las caras: la grande, si la hay
        if !faces.isEmpty {
            if let big = bigPreview(url), big.width > img.width { src = big }
            let h2 = src === img ? handler : VNImageRequestHandler(cgImage: src, options: [:])
            let lm = VNDetectFaceLandmarksRequest(); lm.inputFaceObservations = faces
            lm.constellation = .constellation76Points          // más puntos en los párpados: ojos más precisos
            let q = VNDetectFaceCaptureQualityRequest(); q.inputFaceObservations = faces
            try? h2.perform([lm, q])
            lmFaces = lm.results ?? []; qual = q.results ?? []
        }
        let (px, W, H) = grayPixels(img, 768)
        var g = regionStats(px, W, H, CGRect(x: 0, y: 0, width: W, height: H))
        o["lum"] = g["lum"]; o["negro"] = g["negro"]; o["quemado"] = g["quemado"]; o["desv"] = g["desv"]; o["nit"] = g["nit"]
        g = [:]
        var fo: [[String: Any]] = []
        for (i, f) in faces.enumerated() {
            let b = f.boundingBox
            let st: [String: Double]
            if src === img {
                let r = CGRect(x: b.minX * Double(W), y: (1 - b.maxY) * Double(H), width: b.width * Double(W), height: b.height * Double(H))
                st = regionStats(px, W, H, r)
            } else {
                st = faceStats(src, b)
            }
            var d: [String: Any] = ["x": b.minX, "y": b.minY, "w": b.width, "h": b.height,
                                    "lum": st["lum"] ?? -1, "nit": st["nit"] ?? -1,
                                    "px": Int(Double(b.width) * Double(src.width))]     // ancho de la cara medida, en píxeles
            if let p = f.pitch { d["pitch"] = p.doubleValue }
            if let y = f.yaw { d["yaw"] = y.doubleValue }
            if let r = f.roll { d["roll"] = r.doubleValue }
            let lf = sameFace(f, lmFaces) ?? (i < lmFaces.count ? lmFaces[i] : nil)
            let fw = Double(b.width) * Double(src.width), fh = Double(b.height) * Double(src.height)
            d["ojoI"] = eyeRatio(lf?.landmarks?.leftEye?.normalizedPoints ?? [], fw, fh)
            d["ojoD"] = eyeRatio(lf?.landmarks?.rightEye?.normalizedPoints ?? [], fw, fh)
            if let qf = sameFace(f, qual) ?? (i < qual.count ? qual[i] : nil), let c = qf.faceCaptureQuality {
                d["calidad"] = Double(c)
            }
            fo.append(d)
        }
        o["caras"] = fo
        o["personas"] = humans.results?.count ?? 0
        o["escena"] = (classify.results ?? []).filter { $0.confidence > 0.25 }.prefix(6).map { ["\($0.identifier)", Double($0.confidence)] }
        if #available(macOS 15.0, *), let a = (aest as? VNCalculateImageAestheticsScoresRequest)?.results?.first {
            o["estetica"] = Double(a.overallScore)
        }
        let cur = fp.results?.first
        if let cur = cur {
            // dists[0] = con la anterior, dists[1] = con la de antes… (-1 = esa no se pudo comparar)
            var ds: [Double] = []
            for p in prevFPs.reversed() {
                var dist: Float = 0
                if let p = p, (try? p.computeDistance(&dist, to: cur)) != nil { ds.append(Double(dist)) } else { ds.append(-1) }
            }
            if let d0 = ds.first, d0 >= 0 { o["dist_prev"] = d0 }
            if !ds.isEmpty { o["dists"] = ds }
        }
        remember(cur)
        print(String(data: try! JSONSerialization.data(withJSONObject: o), encoding: .utf8)!)
        fflush(stdout)
    }
}

switch args.first ?? "" {
case "cull":
    runCull()
case "caras":
    runFaces()
case "check":
    let ax = AXIsProcessTrusted()
    let sc = CGPreflightScreenCaptureAccess()
    print("ax=\(ax ? 1 : 0) screen=\(sc ? 1 : 0)")
case "ask":
    _ = AXIsProcessTrustedWithOptions([kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary)
    _ = CGRequestScreenCaptureAccess()
case "ocr":
    let did = CGMainDisplayID()
    let b = CGDisplayBounds(did)
    guard let img = captureBelowRawFlow(did) ?? captureFull() else { print("[]"); exit(2) }
    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.recognitionLanguages = ["es-ES", "en-US"]
    req.usesLanguageCorrection = false
    try? VNImageRequestHandler(cgImage: img, options: [:]).perform([req])
    var out: [[String: Any]] = []
    for o in req.results ?? [] {
        guard let c = o.topCandidates(1).first else { continue }
        let r = o.boundingBox
        out.append(["t": c.string,
                    "x": Double(r.minX) * b.width, "y": Double(1 - r.maxY) * b.height,
                    "w": Double(r.width) * b.width, "h": Double(r.height) * b.height])
    }
    let d = try! JSONSerialization.data(withJSONObject: out)
    print(String(data: d, encoding: .utf8)!)
case "shot":
    let did = CGMainDisplayID()
    guard let img = captureBelowRawFlow(did), let dst = CGImageDestinationCreateWithURL(
        URL(fileURLWithPath: args[1]) as CFURL, "public.png" as CFString, 1, nil) else { exit(2) }
    CGImageDestinationAddImage(dst, img, nil); CGImageDestinationFinalize(dst)
case "block":
    runBlock(maxSeconds: args.count > 1 ? num(1) : 0)          // 0 = sin límite (hasta que RawFlow lo suelte)
case "escuchar":                                               // solo Esc ×3 para cancelar: no bloquea nada
    passThrough = true
    runBlock(maxSeconds: 0)
case "click":
    let p = CGPoint(x: num(1), y: num(2)); let n = args.count > 3 ? Int64(args[3])! : 1
    mouse(.mouseMoved, p); usleep(120000)
    for k in 1...n { mouse(.leftMouseDown, p, clicks: k); usleep(60000); mouse(.leftMouseUp, p, clicks: k); usleep(80000) }
case "drag":
    let p0 = CGPoint(x: num(1), y: num(2)), p1 = CGPoint(x: num(3), y: num(4))
    mouse(.mouseMoved, p0); usleep(200000); mouse(.leftMouseDown, p0); usleep(300000)
    for i in 1...30 { let f = Double(i) / 30
        mouse(.leftMouseDragged, CGPoint(x: p0.x + (p1.x - p0.x) * f, y: p0.y + (p1.y - p0.y) * f)); usleep(30000) }
    usleep(500000); mouse(.leftMouseUp, p1)
case "scroll":
    let p = CGPoint(x: num(1), y: num(2)); mouse(.mouseMoved, p); usleep(120000)
    for _ in 0..<Int(num(4)) { CGEvent(scrollWheelEvent2Source: src, units: .line, wheelCount: 1, wheel1: Int32(num(3)), wheel2: 0, wheel3: 0)?.post(tap: .cghidEventTap); usleep(40000) }
case "type":
    for ch in args[1].utf16 {
        var c = ch
        for down in [true, false] {
            let e = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: down)
            e?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &c)
            e?.post(tap: .cghidEventTap)
        }
        usleep(25000)
    }
case "key":
    let code = CGKeyCode(args[1])!; let f = args.count > 2 ? flags(args[2]) : []
    for down in [true, false] {
        let e = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: down); e?.flags = f
        e?.post(tap: .cghidEventTap); usleep(40000)
    }
default:
    FileHandle.standardError.write("uso: rfui ocr|click|drag|scroll|type|key|check|ask|cull|caras\n".data(using: .utf8)!); exit(1)
}
