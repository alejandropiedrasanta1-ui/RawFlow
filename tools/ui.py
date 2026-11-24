import json, subprocess, sys, time, unicodedata
RF = "/Users/cinemaproductions/Desktop/EDITAR FOTOS  EN RAPIDO/Programa/tools/rfui"
def norm(s): return "".join(c for c in unicodedata.normalize("NFD", s.lower()) if unicodedata.category(c) != "Mn")
def ocr(): return json.loads(subprocess.check_output([RF, "ocr"]))
def find(txt, exact=False, region=None):
    t = norm(txt); out = []
    for b in ocr():
        n = norm(b["t"])
        if (n == t if exact else t in n):
            if region and not (region[0] <= b["x"] <= region[2] and region[1] <= b["y"] <= region[3]): continue
            out.append(b)
    return out
def center(b): return b["x"] + b["w"] / 2, b["y"] + b["h"] / 2
def click(x, y, n=1): subprocess.run([RF, "click", str(x), str(y), str(n)])
if __name__ == "__main__":
    for b in ocr(): print(round(b["x"]), round(b["y"]), b["t"])
