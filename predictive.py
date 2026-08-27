"""
Predictive layer.

Rather than waiting for a unit to manually post a new listing, this module studies each
listing's simulated history (how often that unit has posted that material before) and
forecasts the next likely listing date and quantity. That forecast can be surfaced to a
potential match partner in advance, so a buyer is already lined up before the waste is
even generated -- proactive rather than reactive matching.

A trained regressor (see train_ml_models.py / ml_models.py) predicts the next interval
and quantity from [avg_interval_recent, avg_qty_recent, std_interval, category_factor,
last_weekday] -- learned from data, rather than the plain moving-average baseline this
started as. The two aren't just swapped silently: forecast_next_listing() reports which
one actually produced each forecast, and benchmark_vs_baseline() exposes the honest,
held-out MAE comparison between them (computed once at training time; see
ml_models.forecast_model_benchmark()), so "the model beats the naive baseline by X%" is
a real, checkable number, not a claim. If the model hasn't been trained yet (fresh
clone, before `python train_ml_models.py` has run), every forecast falls back to the
original moving average automatically -- see ml_models.py's docstring for why every ML
call in this project is written to degrade like that instead of raising.
"""

from datetime import timedelta
from statistics import mean, pstdev

import data
import ml_models
from data import seed_listing_history

# LISTINGS/unit_by_id must stay data.LISTINGS / data.unit_by_id(...) attribute lookups,
# not bare "from data import ..." -- see the note in matching.py for why.


def forecast_next_listing(listing):
    history = seed_listing_history(listing, cycles=5)
    intervals = [(history[i + 1] - history[i]).days for i in range(len(history) - 1)]
    avg_interval = round(mean(intervals), 1) if intervals else listing["interval_days"]
    last_seen = history[-1]

    unit = data.unit_by_id(listing["unit_id"])
    unit_name = unit["name"] if unit else listing["unit_id"]
    category_factor = ml_models.category_interval_factor(unit["category"] if unit else "other")
    std_interval = pstdev(intervals) if len(intervals) > 1 else 0.0

    predicted = ml_models.predict_next_listing(
        avg_interval_recent=avg_interval,
        avg_qty_recent=listing["qty_kg"],
        std_interval=std_interval,
        category_factor=category_factor,
        last_weekday=last_seen.weekday(),
    )
    if predicted:
        pred_interval, pred_qty = predicted
        method = "ml"
    else:
        pred_interval, pred_qty = avg_interval, listing["qty_kg"]
        method = "moving_average"

    predicted_next = last_seen + timedelta(days=pred_interval)

    return {
        "unit_id": listing["unit_id"],
        "unit_name": unit_name,
        "type": listing["type"],
        "material": listing["material"],
        "avg_qty_kg": round(pred_qty, 1),
        "avg_interval_days": round(pred_interval, 1),
        "last_listed": last_seen.date().isoformat(),
        "predicted_next_listing": predicted_next.date().isoformat(),
        "days_until_predicted": (predicted_next - last_seen).days,
        "forecast_method": method,
    }


def forecast_all(material_filter=None, type_filter="waste"):
    """By default, forecast upcoming WASTE listings (the side worth predicting, so a
    buyer can be lined up in advance)."""
    listings = [l for l in data.LISTINGS if l["type"] == type_filter]
    if material_filter:
        listings = [l for l in listings if l["material"] == material_filter]
    forecasts = [forecast_next_listing(l) for l in listings]
    forecasts.sort(key=lambda f: f["predicted_next_listing"])
    return forecasts


def benchmark_vs_baseline():
    """Held-out MAE comparison between the trained model and the plain moving
    average, computed once at training time and persisted with the model (see
    train_ml_models.py::train_forecast_model()). Returns None if the model
    hasn't been trained yet."""
    return ml_models.forecast_model_benchmark()


if __name__ == "__main__":
    print("Predicted upcoming WASTE listings (proactive matching candidates):\n")
    for f in forecast_all():
        tag = "[ML]" if f["forecast_method"] == "ml" else "[moving avg]"
        print(f"{tag} {f['unit_name']}: ~{f['avg_qty_kg']}kg of {f['material']} expected around "
              f"{f['predicted_next_listing']} (every ~{f['avg_interval_days']} days, last posted {f['last_listed']})")

    bench = benchmark_vs_baseline()
    if bench:
        print(f"\nModel vs baseline (held-out test set): baseline MAE={bench['baseline_mae']}d, "
              f"model MAE={bench['model_mae']}d, improvement={bench['improvement_pct']}%")
