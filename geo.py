"""Geometry helpers. All coordinates are normalized to 0-1000 (x right, y down); boxes are [ymin, xmin, ymax, xmax]."""
import io, json, os
from PIL import Image, ImageDraw
ZF = "data/zones.json"
COL = {"clearance": (255, 190, 0), "restricted": (255, 60, 60)}

def load_zones(): return json.load(open(ZF)) if os.path.exists(ZF) else {}
def save_zones(z):
    os.makedirs("data", exist_ok=True); json.dump(z, open(ZF, "w"))
def footprint(box): y0, x0, y1, x1 = box; return ((x0 + x1) / 2, y1)  # ground contact point = bottom centre
def inside(pt, poly):
    if len(poly) < 3: return False
    x, y, c = pt[0], pt[1], False
    for i in range(len(poly)):
        (x1, y1), (x2, y2) = poly[i], poly[(i + 1) % len(poly)]
        if (y1 > y) != (y2 > y) and x < (x2 - x1) * (y - y1) / (y2 - y1) + x1: c = not c
    return c

def annotate(img, objects, zones, highlight=()):
    im = Image.open(io.BytesIO(img)).convert("RGBA"); W, H = im.size
    ov = Image.new("RGBA", im.size); d = ImageDraw.Draw(ov)
    sx = lambda p: (p[0] * W / 1000, p[1] * H / 1000)
    for n, poly in zones.items():
        if len(poly) >= 3:
            col = COL.get(n, (0, 150, 255)); d.polygon([sx(p) for p in poly], fill=col + (60,), outline=col + (255,))
            d.text(sx(poly[0]), n, fill=(255, 255, 255, 255))
    for o in objects:
        if not o.get("box"): continue
        y0, x0, y1, x1 = o["box"]; hot = o["label"] in highlight
        d.rectangle([x0 * W / 1000, y0 * H / 1000, x1 * W / 1000, y1 * H / 1000], outline=(255, 0, 0, 255) if hot else (0, 220, 120, 255), width=5 if hot else 2)
        d.text((x0 * W / 1000 + 4, y0 * H / 1000 + 4), f"{o['label']} {o['confidence']:.0%}", fill=(255, 255, 255, 255))
    b = io.BytesIO(); Image.alpha_composite(im, ov).convert("RGB").save(b, "PNG"); return b.getvalue()

def derive_zones(objects, spec):
    """Zones from the detected (largest) aircraft box: clearance = wide band under/around it, restricted = lower part right at the fuselage."""
    ac = [o for o in objects if o["type"] == "aircraft" and o.get("box")]
    if not ac: return {}
    y0, x0, y1, x1 = max(ac, key=lambda o: (o["box"][2] - o["box"][0]) * (o["box"][3] - o["box"][1]))["box"]
    w, h, z = x1 - x0, y1 - y0, {}
    for n, s in spec.items():
        L, R, T, B = max(0, x0 - s["x_margin"] * w), min(1000, x1 + s["x_margin"] * w), y0 + s["top_frac"] * h, min(1000, y1 + s["bottom_margin"] * h)
        z[n] = [[L, T], [R, T], [R, B], [L, B]]
    return z

def duration(path):
    import cv2
    cap = cv2.VideoCapture(path); fps, n = cap.get(cv2.CAP_PROP_FPS), cap.get(cv2.CAP_PROP_FRAME_COUNT); cap.release()
    return n / fps if fps and n else 0

def frame_at(path, t):
    import cv2
    cap = cv2.VideoCapture(path); cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000); ok, fr = cap.read(); cap.release()
    if not ok: return None
    return cv2.imencode(".png", fr)[1].tobytes()