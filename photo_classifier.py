"""
Photo-based material classification -- "see" the waste, don't just read a
description of it.

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


def classify_photo(image_bytes):
    """Classify a photo (raw bytes, e.g. from a Flask file upload) into a
    material family + rough quantity range. Returns a dict shaped similarly
    to whatsapp_stub.parse_message()'s result so callers can reuse the same
    'preview, then let the owner confirm before posting' UI pattern."""
    try:
        image = Image.open(io.BytesIO(image_bytes))
        image.load()
    except Exception:
        return {"ok": False, "reason": "Couldn't read that file as an image -- try a JPEG or PNG photo."}

    model = _load_model()
    if model is None:
        return {"ok": False, "reason": "Photo classifier hasn't been trained yet -- run `python train_ml_models.py`."}

    features = extract_features(image).reshape(1, -1)
    family = model.predict(features)[0]
    proba = model.predict_proba(features)[0]
    confidence = float(proba.max())

    fill_fraction = _fill_fraction(image)
    qty_lo, qty_hi = _qty_range_for_fill(fill_fraction)
    material_key = primary_material_for_family(family)

    return {
        "ok": True,
        "family": family,
        "family_label": family.replace("_", " "),
        "confidence": round(confidence, 2),
        "material": material_key,
        "material_options": MATERIAL_FAMILIES.get(family, []),
        "type": "waste",  # photo intake is for "here's my waste pile", not a need
        "qty_kg_low": qty_lo,
        "qty_kg_high": qty_hi,
        "qty_kg_estimate": round((qty_lo + qty_hi) / 2),
        "method": "ml",
        "note": (
            "Rough visual estimate from photo fill -- not a measurement. If a bin sensor "
            "is registered for this material, use its measured weight instead (see IoT Bin Sensors)."
        ),
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
