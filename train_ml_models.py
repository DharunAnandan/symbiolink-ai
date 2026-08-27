"""
Trains and persists the five ML models this project uses at runtime:

  1. Intake classifier   -- is an inbound WhatsApp/voice-note message a WASTE
                             offer or a NEED request? (whatsapp_stub.py)
  2. Forecast regressor   -- predicts a unit's next listing interval/quantity,
                             replacing a plain moving average. (predictive.py)
  3. Match ranker         -- predicts how likely a matched chain is to actually
                             be accepted, replacing fixed 0.60/0.25/0.15
                             weights. (matching.py)
  4. Photo material classifier -- guesses a material family + rough quantity
                             from an uploaded photo instead of a typed
                             description. (photo_classifier.py)
  5. Order-risk model    -- predicts how likely a placed order is to complete
                             successfully vs. fall through (cancelled or
                             disputed), so at-risk orders can be flagged
                             before they fail. (app.py's _order_risk_score())

Run it directly to (re)train everything and print real accuracy/MAE numbers:

    python train_ml_models.py

This is meant to be run once after cloning (or whenever ml_training_data.py's
generators change) -- the app itself only ever *loads* the saved artifacts in
ml_artifacts/ (see ml_models.py), it never trains at request time. All three
training sets are synthetic (see ml_training_data.py's docstring for exactly
how, and why -- there's no real historical log to train on yet at this stage),
so the metrics below describe how well each model recovers the *generating
rule*, which is still a meaningful sanity check: if a model can't beat a naive
baseline on data explicitly built to reward the smarter approach, something is
wrong with the model or its features, not just with "real world data is messy."
"""

import os

import joblib
import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import accuracy_score, mean_absolute_error, roc_auc_score

import ml_training_data as td
import photo_classifier

ARTIFACT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_artifacts")


def train_intake_classifier():
    print("=" * 70)
    print("1/5  Intake classifier (need vs waste)")
    print("=" * 70)
    examples = td.generate_intake_examples()
    texts = [t for t, _ in examples]
    labels = [l for _, l in examples]

    cutoff = int(len(texts) * 0.8)
    X_train_text, X_test_text = texts[:cutoff], texts[cutoff:]
    y_train, y_test = labels[:cutoff], labels[cutoff:]

    vectorizer = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1)
    X_train = vectorizer.fit_transform(X_train_text)
    X_test = vectorizer.transform(X_test_text)

    clf = LogisticRegression(max_iter=1000)
    clf.fit(X_train, y_train)

    train_acc = accuracy_score(y_train, clf.predict(X_train))
    test_acc = accuracy_score(y_test, clf.predict(X_test))
    print(f"  training examples: {len(X_train_text)}  |  held-out test examples: {len(X_test_text)}")
    print(f"  train accuracy: {train_acc:.1%}   test accuracy: {test_acc:.1%}")

    joblib.dump({"vectorizer": vectorizer, "model": clf}, os.path.join(ARTIFACT_DIR, "intake_classifier.joblib"))
    print(f"  saved -> ml_artifacts/intake_classifier.joblib\n")
    return test_acc


def train_forecast_model():
    print("=" * 70)
    print("2/5  Demand forecasting regressor")
    print("=" * 70)
    X_train, y_train, X_test, y_test, baseline_test_mae = td.generate_forecast_dataset()
    # Model choice note: a tree ensemble (RandomForest/GradientBoosting) was
    # the first thing tried here, since that's the usual reach for tabular
    # forecasting -- but it consistently did *worse* than the plain
    # moving-average baseline on this data (verified empirically, not assumed).
    # The reason is structural: the target is very close to a linear function
    # of avg_interval_recent plus a small additive weekday correction, and
    # tree ensembles predict the *mean of a leaf region* rather than
    # extrapolating a continuous relationship -- which is the wrong shape of
    # model for "mostly identity, plus a small correction" unless given far
    # more training data than a few thousand synthetic profiles. A linear
    # model (Ridge, L2-regularized so it doesn't overfit the noise) both
    # matches the true shape of the relationship and reliably beats the
    # baseline by learning the weekday effect the baseline can't see at all
    # (it only ever looks at *how many days apart* past listings were, never
    # *what day of the week* -- see ml_training_data.py's _weekday_effect()).
    model = Ridge(alpha=1.0)
    model.fit(X_train, y_train)

    preds = model.predict(X_test)
    model_mae_interval = mean_absolute_error(y_test[:, 0], preds[:, 0])
    model_mae_qty = mean_absolute_error(y_test[:, 1], preds[:, 1])

    improvement = (baseline_test_mae - model_mae_interval) / baseline_test_mae * 100 if baseline_test_mae else 0
    print(f"  training profiles: {len(X_train)}  |  held-out test profiles: {len(X_test)}")
    print(f"  baseline (moving average) MAE on next-interval: {baseline_test_mae:.2f} days")
    print(f"  trained model MAE on next-interval:              {model_mae_interval:.2f} days")
    print(f"  trained model MAE on next-quantity:               {model_mae_qty:.2f} kg")
    print(f"  -> model beats the moving-average baseline by {improvement:.1f}% on next-interval MAE")

    joblib.dump({"model": model, "baseline_test_mae": baseline_test_mae, "model_test_mae": model_mae_interval},
                os.path.join(ARTIFACT_DIR, "forecast_model.joblib"))
    print(f"  saved -> ml_artifacts/forecast_model.joblib\n")
    return improvement


def train_match_ranker():
    print("=" * 70)
    print("3/5  Match-acceptance ranker")
    print("=" * 70)
    X_train, y_train, X_test, y_test, feature_names = td.generate_match_outcomes()

    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train)

    test_pred = model.predict(X_test)
    test_proba = model.predict_proba(X_test)[:, 1]
    acc = accuracy_score(y_test, test_pred)
    auc = roc_auc_score(y_test, test_proba)

    print(f"  training samples: {len(X_train)}  |  held-out test samples: {len(X_test)}")
    print(f"  test accuracy: {acc:.1%}   test AUC: {auc:.3f}")
    print("  learned feature weights (standardized by feature scale, so sign/relative size is what matters):")
    for name, coef in zip(feature_names, model.coef_[0]):
        print(f"    {name:20s} {coef:+.3f}")

    joblib.dump({"model": model, "feature_names": feature_names, "test_accuracy": acc, "test_auc": auc},
                os.path.join(ARTIFACT_DIR, "match_ranker.joblib"))
    print(f"  saved -> ml_artifacts/match_ranker.joblib\n")
    return acc


def train_photo_classifier():
    print("=" * 70)
    print("4/5  Photo material classifier")
    print("=" * 70)
    acc = photo_classifier.train_and_save(model_path=os.path.join(ARTIFACT_DIR, "photo_classifier.joblib"))
    print(f"  families: {len(photo_classifier.FAMILY_LABELS)}  ({', '.join(photo_classifier.FAMILY_LABELS)})")
    print(f"  held-out accuracy on synthetic images: {acc:.1%}")
    print("  NOTE: trained on procedurally-generated synthetic images approximating each")
    print("  family's color/texture, not real waste photos -- see photo_classifier.py's")
    print("  module docstring for the full honest-scope explanation.")
    print(f"  saved -> ml_artifacts/photo_classifier.joblib\n")
    return acc


def train_order_risk_model():
    print("=" * 70)
    print("5/5  Order-risk model (will this order complete or fall through?)")
    print("=" * 70)
    X_train, y_train, X_test, y_test, feature_names = td.generate_order_outcomes()

    model = LogisticRegression(max_iter=1000)
    model.fit(X_train, y_train)

    test_pred = model.predict(X_test)
    test_proba = model.predict_proba(X_test)[:, 1]
    acc = accuracy_score(y_test, test_pred)
    auc = roc_auc_score(y_test, test_proba)

    print(f"  training samples: {len(X_train)}  |  held-out test samples: {len(X_test)}")
    print(f"  test accuracy: {acc:.1%}   test AUC: {auc:.3f}")
    print("  learned feature weights (standardized by feature scale, so sign/relative size is what matters):")
    for name, coef in zip(feature_names, model.coef_[0]):
        print(f"    {name:20s} {coef:+.3f}")

    joblib.dump({"model": model, "feature_names": feature_names, "test_accuracy": acc, "test_auc": auc},
                os.path.join(ARTIFACT_DIR, "order_risk_model.joblib"))
    print(f"  saved -> ml_artifacts/order_risk_model.joblib\n")
    return acc, auc


if __name__ == "__main__":
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    print("\nTraining all SymbioLink AI models (synthetic data -- see ml_training_data.py)\n")
    intake_acc = train_intake_classifier()
    forecast_improvement = train_forecast_model()
    ranker_acc = train_match_ranker()
    photo_acc = train_photo_classifier()
    risk_acc, risk_auc = train_order_risk_model()
    print("=" * 70)
    print("Done. Summary:")
    print(f"  intake classifier test accuracy:     {intake_acc:.1%}")
    print(f"  forecast model vs baseline MAE:       {forecast_improvement:+.1f}%")
    print(f"  match ranker test accuracy:           {ranker_acc:.1%}")
    print(f"  photo classifier test accuracy:       {photo_acc:.1%}  (synthetic images -- see note above)")
    print(f"  order-risk model test accuracy:       {risk_acc:.1%}   test AUC: {risk_auc:.3f}")
    print("=" * 70)
