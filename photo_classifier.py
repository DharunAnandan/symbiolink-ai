"""
Photo-based material classification -- "see" the waste, don't just read a
description of it.

How a photo is classified now (see classify_photo): Gemini vision when
GEMINI_API_KEY is set, otherwise the colour heuristic (heuristic_classify).
The RandomForest described below is still trained by train_ml_models.py but
is no longer used for predictions: trained only on synthetic squares, it
labelled almost every real photo "metal" or "electronic scrap" (a cardboard
pile came back as metal scrap). The notes below describe that original model.

The problem: typing a clear listing description ("220kg metal shavings") is
easy for some owners and genuinely hard for others -- not everyone writes
confident English or Hindi, but everyone can point a phone camera at a pile of
waste and take a photo. This module reads that photo and guesses a material
family + a rough quantity, the same way whatsapp_stub.py reads a typed message
and guesses a material + quantity -- a second, independent AI skill (computer
vision, not text) feeding the exact same listing auto-fill path.

Honest scope, read this before assuming more than is actually here: there is
no dataset of real photographed industrial waste to train on, and downloading
a several-hundred-MB pretrained deep vision model (torchvision/ImageNet-style)
would (a) hit the exact PyTorch-sized-dependency problem ml_text.py's
docstring already explains this project deliberately avoided once, and (b)
still be the wrong tool -- ImageNet's 1000 classes are everyday objects/animals,
not "metal shavings" vs "cardboard offcuts", so its raw predictions wouldn't
map onto this domain any better than random guessing. What's built here
instead is a real, small, trained scikit-learn classifier (RandomForest, same
library the rest of the ML layer already uses) over classic, explainable
computer-vision features -- mean/spread of color channels in RGB and HSV,
plus an edge-density/texture proxy -- trained on procedurally GENERATED
synthetic images whose color/texture profile approximates each material
family's real-world look (metal: grey/silver with speckle; cardboard: tan/
brown with grain; fabric: soft colorful blotches; etc. -- see
FAMILY_VISUAL_PROFILES below for exactly what's simulated). That is a
materially weaker claim than "trained on real waste photos," and this
docstring says so on purpose, same as README.md's "Honest scope notes" already
does for the dataset and the other three ML models -- a real deployment would
retrain this on actual labelled photos from the field the moment enough exist.

Quantity from a single 2D photo with no depth sensor or size reference is
fundamentally a rough estimate, not a measurement -- classify_photo() returns
a wide illustrative kg RANGE (derived from how much of the frame looks filled
with material, not empty background), explicitly labelled as such, and its
own docstring says to prefer an IoT bin sensor's actual load-cell weight (see
iot_sensors.py) over this guess whenever one is registered for the same
material -- the two features are complementary, not competing, and the
project should be honest about which one is actually measuring vs. guessing.
"""

import io
import math
import os
import random

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ARTIFACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_artifacts")
MODEL_PATH = os.path.join(ARTIFACT_DIR, "photo_classifier.joblib")
IMG_SIZE = 64  # small, fixed size for consistent, fast feature extraction

# Material "family" = what a photo can plausibly distinguish visually. Several
# of data.py's specific material_keys look identical in a photo (e.g.
# metal_shavings vs cast_offcuts vs sheet_trimmings are all "metal scrap"), so
# a family maps to a short list of specific materials; classify_photo() picks
# the first one as its best single guess but reports the full family so the
# UI can offer the others too.
MATERIAL_FAMILIES = {
    "metal_scrap": ["metal_shavings", "cast_offcuts", "sheet_trimmings", "metal_dust"],
    "cardboard_paper": ["cardboard_offcuts", "paper_pulp_reject", "bagasse"],
    "fabric_textile": ["fabric_offcuts"],
    "plastic": ["plastic_regrind"],
    "wood": ["sawdust"],
    "rubber": ["rubber_scrap"],
    "leather": ["leather_scraps"],
    "glass": ["glass_cullet"],
    "organic_sludge": ["fruit_pulp_waste", "press_mud", "dye_sludge", "process_effluent_solids"],
    "electronic_scrap": ["electronic_scrap"],
}

# Approximate visual signature per family, used ONLY to generate synthetic
# training images (see generate_synthetic_image below) -- base RGB color, how
# much per-pixel color noise (roughness/shine), and how much fine edge/grain
# texture to draw on top. Deliberately simple and disclosed, not meant to
# look photorealistic -- see module docstring's honest-scope note.
FAMILY_VISUAL_PROFILES = {
    "metal_scrap":       {"base": (150, 152, 158), "noise": 42, "grain": "speckle"},
    "cardboard_paper":   {"base": (177, 141, 96),  "noise": 18, "grain": "fiber"},
    "fabric_textile":    {"base": (150, 90, 130),  "noise": 30, "grain": "blotch"},
    "plastic":           {"base": (60, 140, 210),  "noise": 14, "grain": "smooth"},
    "wood":              {"base": (140, 100, 60),  "noise": 20, "grain": "streak"},
    "rubber":            {"base": (35, 35, 38),    "noise": 12, "grain": "smooth"},
    "leather":           {"base": (92, 58, 40),    "noise": 16, "grain": "fiber"},
    "glass":             {"base": (205, 225, 222), "noise": 10, "grain": "smooth"},
    "organic_sludge":    {"base": (100, 95, 55),   "noise": 26, "grain": "blotch"},
    "electronic_scrap":  {"base": (40, 70, 45),    "noise": 35, "grain": "speckle_bright"},
}

FAMILY_LABELS = sorted(FAMILY_VISUAL_PROFILES.keys())


def primary_material_for_family(family):
    materials = MATERIAL_FAMILIES.get(family)
    return materials[0] if materials else None


# ---------------------------------------------------------------------------
# Synthetic training image generation
# ---------------------------------------------------------------------------

def generate_synthetic_image(family, seed=None):
    """One procedurally-generated IMG_SIZE x IMG_SIZE training image
    approximating a material family's rough color/texture look. See module
    docstring for why this is synthetic, not a real photo."""
    rng = random.Random(seed)
    profile = FAMILY_VISUAL_PROFILES[family]
    base = profile["base"]
    noise = profile["noise"]

    arr = np.zeros((IMG_SIZE, IMG_SIZE, 3), dtype=np.int16)
    for c in range(3):
        arr[:, :, c] = base[c] + rng.gauss(0, 6)
    arr += np.random.default_rng(rng.randint(0, 2**31 - 1)).normal(0, noise, arr.shape).astype(np.int16)
    arr = np.clip(arr, 0, 255).astype(np.uint8)
    img = Image.fromarray(arr, mode="RGB")

    draw = ImageDraw.Draw(img)
    grain = profile["grain"]
    if grain == "speckle":
        for _ in range(rng.randint(80, 160)):
            x, y = rng.randint(0, IMG_SIZE - 1), rng.randint(0, IMG_SIZE - 1)
            shade = rng.choice([255, 255, 200, 40])
            draw.point((x, y), fill=(shade, shade, shade))
    elif grain == "speckle_bright":
        for _ in range(rng.randint(40, 90)):
            x, y = rng.randint(0, IMG_SIZE - 1), rng.randint(0, IMG_SIZE - 1)
            color = rng.choice([(220, 60, 60), (230, 200, 40), (60, 200, 230), (255, 255, 255)])
            r = rng.randint(1, 2)
            draw.ellipse((x - r, y - r, x + r, y + r), fill=color)
    elif grain == "fiber":
        for _ in range(rng.randint(20, 40)):
            x0, y0 = rng.randint(0, IMG_SIZE - 1), rng.randint(0, IMG_SIZE - 1)
            length, angle = rng.randint(6, 18), rng.uniform(0, math.pi)
            x1 = int(x0 + length * math.cos(angle))
            y1 = int(y0 + length * math.sin(angle))
            shade = min(255, base[0] + rng.randint(-30, 20))
            draw.line((x0, y0, x1, y1), fill=(shade, shade, shade), width=1)
    elif grain == "blotch":
        for _ in range(rng.randint(10, 22)):
            x, y = rng.randint(0, IMG_SIZE - 1), rng.randint(0, IMG_SIZE - 1)
            r = rng.randint(4, 12)
            shade = tuple(max(0, min(255, base[i] + rng.randint(-40, 40))) for i in range(3))
            draw.ellipse((x - r, y - r, x + r, y + r), fill=shade)
    elif grain == "streak":
        for _ in range(rng.randint(15, 30)):
            y = rng.randint(0, IMG_SIZE - 1)
            shade = tuple(max(0, min(255, base[i] + rng.randint(-25, 25))) for i in range(3))
            draw.line((0, y, IMG_SIZE - 1, y + rng.randint(-3, 3)), fill=shade, width=1)
    # "smooth" families get no extra grain pass -- flat-ish color + base pixel noise only.

    return img.filter(ImageFilter.SMOOTH_MORE if grain == "smooth" else ImageFilter.SMOOTH)


# ---------------------------------------------------------------------------
# Feature extraction -- shared by training AND real classification
# ---------------------------------------------------------------------------

def extract_features(image: Image.Image):
    """Classic, explainable CV features: RGB mean/std, HSV mean/std, and an
    edge-density proxy (mean gradient magnitude from a simple edge filter) as
    a stand-in for surface roughness/texture. No deep model, no GPU, works
    identically on a synthetic training image or a real uploaded photo."""
    img = image.convert("RGB").resize((IMG_SIZE, IMG_SIZE))
    rgb = np.asarray(img, dtype=np.float32)

    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)

    gray = img.convert("L")
    edges = np.asarray(gray.filter(ImageFilter.FIND_EDGES), dtype=np.float32)

    features = [
        rgb[:, :, 0].mean(), rgb[:, :, 0].std(),
        rgb[:, :, 1].mean(), rgb[:, :, 1].std(),
        rgb[:, :, 2].mean(), rgb[:, :, 2].std(),
        hsv[:, :, 0].mean(), hsv[:, :, 0].std(),
        hsv[:, :, 1].mean(), hsv[:, :, 1].std(),
        hsv[:, :, 2].mean(),
        edges.mean(), edges.std(),
    ]
    return np.array(features, dtype=np.float32)


def _fill_fraction(image: Image.Image):
    """Very rough proxy for 'how much of the frame is filled with material
    vs. empty background' -- pixels far from the image's own median color
    (assumed to skew toward background/floor) count as 'filled'. Used only to
    scale the illustrative quantity range, never presented as a precise
    measurement -- see module docstring."""
    img = image.convert("RGB").resize((IMG_SIZE, IMG_SIZE))
    arr = np.asarray(img, dtype=np.float32).reshape(-1, 3)
    median = np.median(arr, axis=0)
    dist = np.linalg.norm(arr - median, axis=1)
    threshold = max(15.0, np.percentile(dist, 40))
    return float((dist > threshold).mean())


# ---------------------------------------------------------------------------
# Training
# ---------------------------------------------------------------------------

def build_training_set(samples_per_family=60, seed=42):
    X, y = [], []
    counter = 0
    for family in FAMILY_LABELS:
        for i in range(samples_per_family):
            img = generate_synthetic_image(family, seed=seed * 1000 + counter)
            X.append(extract_features(img))
            y.append(family)
            counter += 1
    return np.array(X), np.array(y)


def train_and_save(model_path=MODEL_PATH, samples_per_family=60):
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.model_selection import train_test_split
    from sklearn.metrics import accuracy_score
    import joblib
    import os

    X, y = build_training_set(samples_per_family=samples_per_family)
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.25, random_state=42, stratify=y)

    clf = RandomForestClassifier(n_estimators=120, max_depth=8, random_state=42)
    clf.fit(X_train, y_train)
    held_out_accuracy = accuracy_score(y_test, clf.predict(X_test))

    os.makedirs(os.path.dirname(model_path), exist_ok=True)
    joblib.dump(clf, model_path)
    return held_out_accuracy


_model_cache = None


def _load_model():
    global _model_cache
    if _model_cache is not None:
        return _model_cache
    try:
        import joblib
        _model_cache = joblib.load(MODEL_PATH)
    except Exception:
        _model_cache = False  # tried and failed -- don't retry every call
    return _model_cache or None


# ---------------------------------------------------------------------------
# Colour heuristic -- the offline fallback
# ---------------------------------------------------------------------------
#
# The RandomForest above only ever saw flat synthetic squares, and on real
# photos (floors, walls, shadows, print on boxes) it labelled nearly
# everything "metal" or "electronic scrap" -- a pile of cardboard came back as
# metal scrap. This fallback instead measures what fraction of the centre of
# the photo falls into a few colour groups that separate the families in real
# photos (tan/brown board vs. grey metal vs. dark saturated rust, vivid mixed
# plastics, PCB green, black rubber) and scores each family from those.
# Transparent and dependency-free (no pickled model, so no scikit-learn
# version coupling), but still a guess: classify_photo() prefers Gemini
# vision when a key is configured, and the page always lets the owner pick a
# different material before posting.

def _colour_groups(image: Image.Image):
    """Fractions of the photo's central region in each colour group."""
    img = image.convert("RGB")
    w, h = img.size
    # The material is usually centred; the edges are mostly floor, wall or sky.
    cw, ch = max(1, int(w * 0.7)), max(1, int(h * 0.7))
    img = img.crop(((w - cw) // 2, (h - ch) // 2, (w + cw) // 2, (h + ch) // 2)).resize((96, 96))
    hsv = np.asarray(img.convert("HSV"), dtype=np.float32)
    H = hsv[..., 0] * 360.0 / 255.0
    S = hsv[..., 1] / 255.0
    V = hsv[..., 2] / 255.0
    edges = float(np.asarray(img.convert("L").filter(ImageFilter.FIND_EDGES), dtype=np.float32).mean())

    browns = (H >= 15) & (H <= 50) & (S >= 0.18)
    board = browns & (S <= 0.62) & (V >= 0.40) & (V <= 0.97)       # cardboard / paper / sawdust tan
    rust = browns & (S > 0.45) & (V < 0.52)                         # corroded steel
    dark_brown = browns & (V >= 0.15) & (V < 0.40) & ~rust          # leather, wet wood, flute shadows
    grey = (S < 0.13) & (V >= 0.22) & (V <= 0.88)                   # bare metal, concrete
    white = (S < 0.13) & (V > 0.88)                                 # paper, film, clear glass glare
    black = V < 0.16                                                # rubber, shadows
    vivid = (S > 0.45) & (V > 0.40)
    green = (H >= 75) & (H <= 170) & (S > 0.25) & (V > 0.15)
    blue = (H >= 180) & (H <= 260) & (S > 0.25) & (V > 0.20)
    olive = (H >= 45) & (H < 75) & (S > 0.2) & (V < 0.55)

    coloured = (S > 0.30) & (V > 0.30)
    hist, _ = np.histogram(H[coloured], bins=12, range=(0, 360))
    hist = hist / max(1, hist.sum())
    distinct_hues = int((hist > 0.08).sum())

    return {
        "board": float(board.mean()), "rust": float(rust.mean()),
        "dark_brown": float(dark_brown.mean()), "grey": float(grey.mean()),
        "white": float(white.mean()), "black": float(black.mean()),
        "vivid": float(vivid.mean()), "green": float(green.mean()),
        "blue": float(blue.mean()), "olive": float(olive.mean()),
        "distinct_hues": distinct_hues, "edges": edges,
    }


def heuristic_scores(image: Image.Image):
    """Score every family from colour-group fractions; higher = more likely."""
    g = _colour_groups(image)
    multi = 1.0 if g["distinct_hues"] >= 3 else 0.4
    busy = min(1.0, g["edges"] / 60.0)  # fine detail, e.g. circuit boards, shavings
    brown_total = g["board"] + g["rust"] + g["dark_brown"]
    # Corrugated board has dark brown flute edges that look rust-like; brown
    # only counts as rust (metal) when rust is most of the brown in the frame.
    rust_share = g["rust"] / brown_total if brown_total else 0.0
    scores = {
        "cardboard_paper": (1.5 * (g["board"] + 0.6 * g["dark_brown"] + 0.4 * g["rust"] * (1 - rust_share))
                            + 0.3 * g["white"]),
        "metal_scrap": 0.8 * g["grey"] + 1.4 * g["rust"] * rust_share + 0.1 * busy,
        "plastic": 0.9 * g["blue"] + 0.6 * g["vivid"] * multi * (1 - rust_share) + 0.15 * g["white"],
        "electronic_scrap": 0.9 * g["green"] * busy + 0.3 * g["vivid"] * multi * busy,
        "rubber": 1.1 * max(0.0, g["black"] - 0.12),
        "wood": 0.35 * g["board"] * busy + 0.3 * g["dark_brown"],
        "leather": 0.4 * g["dark_brown"] * (1.0 - busy),
        "fabric_textile": 0.5 * g["vivid"] * (1.0 - busy) * (1 - rust_share),
        "glass": 0.35 * g["green"] * (1.0 - busy) + 0.15 * g["white"],
        "organic_sludge": 0.8 * g["olive"] + 0.15 * g["dark_brown"],
    }
    return {k: max(0.0, v) for k, v in scores.items()}, g


def heuristic_classify(image: Image.Image):
    """(family, confidence, ranked [(family, share)]) from the colour heuristic."""
    scores, _groups = heuristic_scores(image)
    total = sum(scores.values()) or 1.0
    ranked = sorted(((f, s / total) for f, s in scores.items()), key=lambda x: -x[1])
    best, share = ranked[0]
    # Confidence reflects how clearly the winner beats the runner-up, capped:
    # colour alone can't be certain about a material.
    margin = share - ranked[1][1]
    confidence = round(min(0.75, 0.35 + margin * 1.5), 2)
    return best, confidence, ranked


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

# A photo alone gives no reliable sense of absolute scale -- illustrative
# range only, same "honest estimate" framing as data.py's price/CO2 tables.
QTY_RANGE_KG_BY_FILL = [
    (0.15, (10, 35)),
    (0.35, (25, 70)),
    (0.60, (50, 130)),
    (1.01, (90, 220)),
]


def _qty_range_for_fill(fill_fraction):
    for threshold, rng in QTY_RANGE_KG_BY_FILL:
        if fill_fraction <= threshold:
            return rng
    return QTY_RANGE_KG_BY_FILL[-1][1]


ALL_MATERIALS = [m for family in FAMILY_LABELS for m in MATERIAL_FAMILIES[family]]

VISION_PROMPT = """You are classifying a photo of industrial waste for a marketplace where factories \
sell their waste as another factory's raw material.

Pick the ONE material family that best describes the main material visible, and the most specific \
material from that family's list:
{families}

Also estimate the visible quantity in kg as a rough range (a single box is a few kg; a bale or \
pallet is roughly 100-500 kg; a truckload or large heap is 1000+ kg).

Reply with JSON only:
{{"family": "<family key>", "material": "<material key>", "confidence": <0-1>, \
"qty_kg_low": <number>, "qty_kg_high": <number>, "description": "<under 15 words, what you see>"}}
If the photo shows no waste material at all, use the closest family with confidence below 0.3."""


def _vision_classify(image: Image.Image):
    """Ask Gemini (free tier) to classify the photo. Returns a dict or raises."""
    import base64
    import json

    import assistant  # shared Gemini call with model fallback

    img = image.convert("RGB")
    img.thumbnail((768, 768))  # plenty for recognition; keeps the request small
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)

    families = "\n".join(f"- {fam}: {', '.join(MATERIAL_FAMILIES[fam])}" for fam in FAMILY_LABELS)
    body = {
        "contents": [{"role": "user", "parts": [
            {"text": VISION_PROMPT.format(families=families)},
            {"inline_data": {"mime_type": "image/jpeg",
                             "data": base64.b64encode(buf.getvalue()).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048,
                             "responseMimeType": "application/json",
                             "thinkingConfig": {"thinkingLevel": "low"}},
    }
    text = assistant.gemini_generate(body)
    reply = json.loads(text[text.find("{"): text.rfind("}") + 1])

    family = reply.get("family")
    if family not in MATERIAL_FAMILIES:
        raise ValueError(f"unknown family from vision model: {family!r}")
    material = reply.get("material")
    if material not in MATERIAL_FAMILIES[family]:
        material = primary_material_for_family(family)
    try:
        confidence = max(0.0, min(1.0, float(reply.get("confidence", 0.6))))
    except (TypeError, ValueError):
        confidence = 0.6
    try:
        lo, hi = float(reply["qty_kg_low"]), float(reply["qty_kg_high"])
        qty = (int(round(lo)), int(round(hi))) if 0 < lo <= hi <= 100000 else None
    except (KeyError, TypeError, ValueError):
        qty = None
    return {"family": family, "material": material, "confidence": confidence,
            "qty": qty, "description": str(reply.get("description") or "")[:120]}


def classify_photo(image_bytes):
    """Classify a photo (raw bytes, e.g. from a Flask file upload) into a
    material family + rough quantity range. Returns a dict shaped similarly
    to whatsapp_stub.parse_message()'s result so callers can reuse the same
    'preview, then let the owner confirm before posting' UI pattern.

    Uses Gemini vision when GEMINI_API_KEY is configured (it genuinely
    recognises cardboard, metal, plastic... in real photos), and the colour
    heuristic above otherwise or if the API call fails. Either way the
    result lists every material so the page can let the owner correct it."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception:
        return {"ok": False, "reason": "Couldn't read that file as an image -- try a JPEG or PNG photo."}

    from config import Config

    vision = None
    if getattr(Config, "GEMINI_API_KEY", None):
        try:
            vision = _vision_classify(image)
        except Exception:
            import logging
            logging.getLogger("symbiolink.photo").exception(
                "Gemini vision classification failed; using the colour heuristic")

    _best, heur_conf, ranked = heuristic_classify(image)
    if vision:
        family, material, confidence = vision["family"], vision["material"], vision["confidence"]
        method, description = "ai-vision", vision["description"]
        qty_lo, qty_hi = vision["qty"] or _qty_range_for_fill(_fill_fraction(image))
    else:
        family, confidence = _best, heur_conf
        material = primary_material_for_family(family)
        method, description = "heuristic", ""
        qty_lo, qty_hi = _qty_range_for_fill(_fill_fraction(image))

    # Other likely families, best first, for the "not right? pick another" list.
    alternatives = [f for f, _share in ranked if f != family][:3]
    ordered_families = [family] + [f for f, _s in ranked if f != family]
    material_choices = [m for f in ordered_families for m in MATERIAL_FAMILIES[f]]

    note = ("Recognised by Gemini vision. " if method == "ai-vision" else
            "Best guess from the photo's colours (AI vision not configured or unavailable), "
            "so check it. ")
    note += ("The quantity is a rough visual estimate, not a measurement. If a bin sensor is "
             "registered for this material, use its measured weight instead (see IoT Bin Sensors).")

    return {
        "ok": True,
        "family": family,
        "family_label": family.replace("_", " "),
        "confidence": round(confidence, 2),
        "material": material,
        "material_options": MATERIAL_FAMILIES.get(family, []),
        "material_choices": material_choices,
        "alternatives": [a.replace("_", " ") for a in alternatives],
        "description": description,
        "type": "waste",  # photo intake is for "here's my waste pile", not a need
        "qty_kg_low": qty_lo,
        "qty_kg_high": qty_hi,
        "qty_kg_estimate": round((qty_lo + qty_hi) / 2),
        "method": method,
        "note": note,
    }


if __name__ == "__main__":
    acc = train_and_save()
    print(f"Photo classifier trained. Held-out accuracy on synthetic data: {acc * 100:.1f}%")
    for family in FAMILY_LABELS:
        img = generate_synthetic_image(family, seed=999)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        result = classify_photo(buf.getvalue())
        print(f"  {family:18s} -> predicted {result.get('family')} "
              f"(confidence {result.get('confidence')}, material={result.get('material')})")
