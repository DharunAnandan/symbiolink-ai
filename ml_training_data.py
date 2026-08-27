"""
Synthetic training data generators for four of the five trained models in this
project (see train_ml_models.py -- the fifth, the photo material classifier,
generates its own synthetic images directly in photo_classifier.py):

  1. Intake need/waste classifier   -- labeled (message, "waste"|"need") pairs.
  2. Demand forecasting regressor   -- (features, [next_interval_days, next_qty_kg]) pairs.
  3. Match-acceptance ranker        -- (features, accepted 0/1) pairs.
  4. Order-risk model               -- (features, completed 0/1) pairs.

Every generator here is a *labeled example generator*, not a lookup table -- the
point of separating this from train_ml_models.py is that the same data (and the
same random seed) is reusable from tests, so "the classifier scores >90% on held-out
data" is something CI actually re-checks, not a number that was true once and then
went stale.

All four datasets are synthetic (there's no real historical WhatsApp/order log to
train on for a hackathon-stage product), so every generator documents exactly the
rule it used to assign labels/targets -- honesty about "this is simulated, and here
is the generating process" matters more than pretending it's real production data.
"""

import random

import numpy as np

import data
from ml_models import CATEGORY_INTERVAL_FACTOR
from ml_text import MATERIAL_ALIASES

RANDOM_SEED = 42

# ---------------------------------------------------------------------------
# 1. Intake classifier: need vs waste
# ---------------------------------------------------------------------------
WASTE_TEMPLATES = [
    "we have {qty}kg of {material} to give away",
    "we have extra {material}, about {qty}kg",
    "selling {qty}kg {material}",
    "{qty}kg {material} surplus from our unit",
    "we generate {qty}kg {material} every week, up for grabs",
    "byproduct: {material}, {qty}kg available",
    "extra {material} lying around, roughly {qty}kg",
    "giving away {qty}kg {material}, first come first served",
    "our unit produces {material}, currently {qty}kg piled up",
    "{qty}kg {material} going spare this month",
]
NEED_TEMPLATES = [
    "we need {qty}kg of {material}",
    "looking for {material}, around {qty}kg",
    "want to buy {qty}kg {material}",
    "require {material} for our production line, {qty}kg",
    "in need of {qty}kg {material}",
    "seeking {material} supply, {qty}kg would help",
    "can anyone supply {qty}kg {material}?",
    "our unit is short on {material}, need about {qty}kg",
    "buying {material} this week, {qty}kg needed",
    "would like to purchase {qty}kg {material}",
]
QTY_VALUES = [15, 30, 45, 60, 80, 100, 120, 150, 180, 220, 250]

# Deliberately harder examples that don't lean on one obvious trigger word --
# regional/colloquial phrasing, and a couple of genuinely ambiguous-if-taken-
# alone lines resolved by context elsewhere in the sentence. These exist so the
# held-out test accuracy means something (if the model were just memorizing
# "have"="waste"/"need"="need" as a lookup, these would be exactly the
# examples it would get wrong).
HARD_WASTE_EXAMPLES = [
    "{qty}kg {material} sitting idle in our yard, someone take it",
    "our line throws off {material} daily, {qty}kg piled up and growing",
    "{material} coming out of production, {qty}kg and no use for it here",
    "not sure what to do with {qty}kg {material}, happy to hand it off cheap",
    "{material} stock is overflowing, {qty}kg free to whoever wants it",
]
HARD_NEED_EXAMPLES = [
    "our stock of {material} just ran out, {qty}kg would sort us out",
    "production stalled without {material}, {qty}kg urgently",
    "does anyone nearby have {material} to spare? {qty}kg or so",
    "{material} order fell through, {qty}kg short for this week's run",
    "our supplier of {material} dropped us, {qty}kg needed fast",
]

# Romanized Hindi-English mixed ("Hinglish") phrasing -- real MSME owners
# texting over WhatsApp very often write like this rather than clean English
# (see the project README / ml_text.py's module docstring). Mixed in below at
# the same 40% sampling rate as HARD_WASTE_EXAMPLES/HARD_NEED_EXAMPLES so the
# trained classifier genuinely learns this phrasing as its own signal,
# instead of only relying on whatsapp_stub.py's keyword-fallback list ever
# seeing it (that fallback only runs when the trained model *isn't*
# confident -- the point of training on these too is that it should be).
HINGLISH_WASTE_EXAMPLES = [
    "{qty}kg {material} hai humare paas, becna hai",
    "{material} ka {qty}kg extra pada hai, koi le jaye",
    "hamare yahan {qty}kg {material} roz nikal raha hai",
    "{qty}kg {material} faltu pada hai yahan",
    "{material} bahut ho gaya hai, {qty}kg de denge sasta mein",
    "{qty}kg {material} available hai, chahiye toh bata do",
]
HINGLISH_NEED_EXAMPLES = [
    "hume {qty}kg {material} chahiye",
    "{material} ki zaroorat hai, {qty}kg",
    "{qty}kg {material} kharidna hai humein",
    "{material} khatam ho gaya, {qty}kg turant chahiye",
    "koi {material} de sakta hai kya, {qty}kg chahiye",
    "{material} ka stock khatam ho gaya, {qty}kg mangwana hai",
]


def generate_intake_examples():
    """Every canonical material name and every alias, run through every template
    (both the direct keyword-style templates and the harder ones above that avoid
    an obvious trigger word), at a spread of quantities -- gives the classifier
    phrasing variety, not just one fixed sentence shape per class, without needing
    any hand-labeled data."""
    rng = random.Random(RANDOM_SEED)
    examples = []
    all_material_phrases = []
    for key in data.PRICE_TABLE_RS_PER_KG:
        all_material_phrases.append(key.replace("_", " "))
        all_material_phrases.extend(MATERIAL_ALIASES.get(key, []))

    for phrase in all_material_phrases:
        for template in WASTE_TEMPLATES:
            qty = rng.choice(QTY_VALUES)
            examples.append((template.format(material=phrase, qty=qty), "waste"))
        for template in NEED_TEMPLATES:
            qty = rng.choice(QTY_VALUES)
            examples.append((template.format(material=phrase, qty=qty), "need"))
        # Only a subset of materials get the harder phrasing (every material x
        # every hard template would over-represent these relative to the
        # keyword-style ones) -- still plenty of coverage across the corpus.
        if rng.random() < 0.4:
            examples.append((rng.choice(HARD_WASTE_EXAMPLES).format(material=phrase, qty=rng.choice(QTY_VALUES)), "waste"))
            examples.append((rng.choice(HARD_NEED_EXAMPLES).format(material=phrase, qty=rng.choice(QTY_VALUES)), "need"))
        if rng.random() < 0.4:
            examples.append((rng.choice(HINGLISH_WASTE_EXAMPLES).format(material=phrase, qty=rng.choice(QTY_VALUES)), "waste"))
            examples.append((rng.choice(HINGLISH_NEED_EXAMPLES).format(material=phrase, qty=rng.choice(QTY_VALUES)), "need"))

    rng.shuffle(examples)

    # A small fraction of real-world WhatsApp messages would be genuinely
    # ambiguous or mislabeled by whoever's tagging the training set (typos in
    # rushed factory-floor texting, sarcasm, a message about a *past* delivery
    # rather than a current offer/need) -- 4% label noise simulates that,
    # rather than pretending the training data would be perfectly clean. This
    # is also why the held-out test accuracy below is meaningfully <100%: a
    # classifier that memorized keyword lists would still often get the
    # correctly-labeled majority of the noisy set right but wouldn't show the
    # graceful, realistic accuracy a genuinely generalizing model does.
    noisy_examples = []
    for text, label in examples:
        if rng.random() < 0.04:
            label = "need" if label == "waste" else "waste"
        noisy_examples.append((text, label))
    return noisy_examples


# ---------------------------------------------------------------------------
# 2. Demand forecasting regressor
# ---------------------------------------------------------------------------
# Each synthetic (unit, material) "profile" has a true underlying interval/qty
# rhythm; a day-of-week effect (listings logged Sat/Sun tend to run ~1.5 days
# longer until the next one -- a pattern a plain per-listing moving average has
# no way to use, since it never looks at *when* a listing happened, only how
# many days apart) is layered on top, plus per-listing noise. The regressor
# gets many (unit, material) profiles pooled together and can learn the
# day-of-week effect from the full pool; the moving-average baseline only ever
# sees one listing's own short history and can't.
# (CATEGORY_INTERVAL_FACTOR itself lives in ml_models.py, imported above, so
# predictive.py computes this same feature the same way at inference time.)


def _weekday_effect(weekday):
    # weekday: 0=Mon .. 6=Sun. Weekend listings -> next one tends to land ~3.5
    # days further out (skeleton crew, restart-of-week catch-up effect). Sized
    # well above the per-step noise (std 1.5) so it's a real, learnable signal
    # rather than something that would wash out either way.
    return 3.5 if weekday >= 5 else 0.0


def generate_forecast_dataset(n_profiles=1500, histories_per_profile=5, test_fraction=0.25):
    """Returns (X_train, y_train, X_test, y_test, baseline_test_mae) where each
    row of X is [avg_interval_recent, avg_qty_recent, std_interval, category_factor,
    last_weekday] and each row of y is [next_interval_days, next_qty_kg].
    baseline_test_mae is the moving-average approach's MAE on the same test rows,
    computed the same way predictive.py's forecast_next_listing() does it today,
    so the two are a fair apples-to-apples comparison."""
    rng = np.random.default_rng(RANDOM_SEED)
    materials = list(data.PRICE_TABLE_RS_PER_KG.keys())
    categories = list(CATEGORY_INTERVAL_FACTOR.keys())

    rows_X, rows_y, baseline_errors_interval = [], [], []
    test_cutoff = int(n_profiles * (1 - test_fraction))

    for i in range(n_profiles):
        material = materials[i % len(materials)]
        category = categories[rng.integers(0, len(categories))]
        cat_factor = CATEGORY_INTERVAL_FACTOR[category]
        true_base_interval = rng.uniform(5, 30) * cat_factor
        true_base_qty = rng.uniform(20, 300)

        intervals, qtys, weekdays = [], [], []
        weekday = int(rng.integers(0, 7))
        for _ in range(histories_per_profile):
            weekday_bump = _weekday_effect(weekday)
            interval = max(1.0, true_base_interval + weekday_bump + rng.normal(0, 1.5))
            qty = max(1.0, true_base_qty + rng.normal(0, true_base_qty * 0.08))
            intervals.append(interval)
            qtys.append(qty)
            weekdays.append(weekday)
            weekday = (weekday + int(round(interval))) % 7

        # "recent" window = all but the last point, which becomes the prediction target
        recent_intervals, recent_qtys = intervals[:-1], qtys[:-1]
        next_interval, next_qty = intervals[-1], qtys[-1]
        # weekdays[-1] is the weekday recorded alongside intervals[-1] (the gap
        # we're predicting) -- i.e. the weekday of the listing immediately
        # before that gap, which is exactly the "last known weekday" a real
        # forecast would have at prediction time. (Using weekdays[-2] here was
        # an earlier off-by-one bug: it fed the model the weekday belonging to
        # the *previous* gap instead, which threw away the one signal a plain
        # moving average baseline structurally cannot use.)
        last_weekday = weekdays[-1]

        avg_interval_recent = float(np.mean(recent_intervals))
        avg_qty_recent = float(np.mean(recent_qtys))
        std_interval = float(np.std(recent_intervals)) if len(recent_intervals) > 1 else 0.0

        rows_X.append([avg_interval_recent, avg_qty_recent, std_interval, cat_factor, last_weekday])
        rows_y.append([next_interval, next_qty])

        # Baseline prediction for this same row: plain moving average of the
        # recent window, exactly what predictive.py does today.
        baseline_errors_interval.append(abs(avg_interval_recent - next_interval))

    X = np.array(rows_X)
    y = np.array(rows_y)
    baseline_mae_all = np.array(baseline_errors_interval)

    X_train, y_train = X[:test_cutoff], y[:test_cutoff]
    X_test, y_test = X[test_cutoff:], y[test_cutoff:]
    baseline_test_mae = float(baseline_mae_all[test_cutoff:].mean())

    return X_train, y_train, X_test, y_test, baseline_test_mae


# ---------------------------------------------------------------------------
# 3. Match-acceptance ranker
# ---------------------------------------------------------------------------
# Simulates "would a unit actually accept this match" outcomes. The generating
# rule below intentionally mirrors the spirit of the current hand-tuned
# score_chain() weights (savings matter most, then distance, then trust) so the
# trained model should recover similar-looking weights from data rather than a
# human guess -- but it also adds a similarity penalty and a hop-count penalty
# the fixed formula didn't explicitly model, and Bernoulli noise so the
# classifier has to find a genuine signal, not fit a deterministic rule.
def generate_match_outcomes(n_samples=1200, test_fraction=0.25):
    """Returns (X_train, y_train, X_test, y_test, feature_names). Each row of X
    is [normalized_saving, distance_factor, confidence_factor, similarity, hops]."""
    rng = np.random.default_rng(RANDOM_SEED)
    n = n_samples

    normalized_saving = rng.beta(2, 2, n)  # 0..1, mass in the middle
    distance_factor = rng.beta(2, 1.5, n)  # skewed toward "closer is common"
    confidence_factor = rng.uniform(0.5, 1.0, n)  # trust/5.0 rarely below 2.5/5
    similarity = np.where(rng.uniform(0, 1, n) < 0.35, rng.uniform(0.55, 0.99, n), 1.0)  # ~35% are fuzzy matches
    hops = rng.integers(1, 4, n).astype(float)  # 1-3 hop chains

    logit = (
        3.4 * normalized_saving
        + 1.6 * distance_factor
        + 1.0 * confidence_factor
        + 0.8 * (similarity - 0.5)
        - 0.35 * (hops - 1)
        - 2.6  # intercept so an average-ish match sits near 50/50, not saturated
        + rng.normal(0, 0.5, n)  # real-world noise a perfect formula wouldn't have
    )
    accept_prob = 1 / (1 + np.exp(-logit))
    accepted = (rng.uniform(0, 1, n) < accept_prob).astype(int)

    X = np.column_stack([normalized_saving, distance_factor, confidence_factor, similarity, hops])
    y = accepted

    cutoff = int(n * (1 - test_fraction))
    feature_names = ["normalized_saving", "distance_factor", "confidence_factor", "similarity", "hops"]
    return X[:cutoff], y[:cutoff], X[cutoff:], y[cutoff:], feature_names


# ---------------------------------------------------------------------------
# 4. Order-risk model
# ---------------------------------------------------------------------------
# Simulates whether a placed order actually completes, or falls through
# (cancelled or disputed), as a function of five factors a seller/admin can
# see at order time: how large the order is (value and quantity), how far
# apart the two units are, and how much trust history each side already has.
# The generating rule mirrors the same "closer/higher-trust is safer"
# intuition score_chain() already uses for ranking matches (see
# generate_match_outcomes() above), applied here to whether the transaction
# itself goes through, not whether a match gets accepted in the first place --
# bigger, farther, higher-quantity orders between lower-trust parties are
# modeled as more likely to fall through than small, nearby orders between
# two units with a solid track record.
def generate_order_outcomes(n_samples=1200, test_fraction=0.25):
    """Returns (X_train, y_train, X_test, y_test, feature_names). Each row of X
    is [order_value_factor, distance_factor, seller_trust_factor,
    buyer_trust_factor, qty_factor]. y is 1 if the order completed
    successfully, 0 if it was cancelled or disputed."""
    rng = np.random.default_rng(RANDOM_SEED)
    n = n_samples

    order_value_factor = rng.beta(2, 2, n)          # 0..1, normalized order value
    distance_factor = rng.beta(2, 1.5, n)           # closer is common, same shape as the match ranker
    seller_trust_factor = rng.uniform(0.5, 1.0, n)  # trust/5.0, rarely below 2.5/5 (see trust.py's DEFAULT_SCORE)
    buyer_trust_factor = rng.uniform(0.5, 1.0, n)
    qty_factor = rng.beta(2, 2, n)                  # 0..1, normalized order quantity

    logit = (
        3.6 * seller_trust_factor
        + 2.3 * buyer_trust_factor
        + 2.0 * distance_factor
        - 2.5 * order_value_factor
        - 1.3 * qty_factor
        - 1.3  # intercept: most orders should succeed (~88%), not sit at a coin flip
        + rng.normal(0, 0.5, n)  # real-world noise a perfect formula wouldn't have
    )
    success_prob = 1 / (1 + np.exp(-logit))
    completed = (rng.uniform(0, 1, n) < success_prob).astype(int)

    X = np.column_stack([order_value_factor, distance_factor, seller_trust_factor, buyer_trust_factor, qty_factor])
    y = completed

    cutoff = int(n * (1 - test_fraction))
    feature_names = ["order_value_factor", "distance_factor", "seller_trust_factor", "buyer_trust_factor", "qty_factor"]
    return X[:cutoff], y[:cutoff], X[cutoff:], y[cutoff:], feature_names
