"""
Enforces the "a retrained model only ships if it beats its own fallback"
rule described in the project's Data plan and AI approach writeups, as an
actual callable function rather than just a stated policy -- so it's
something a reviewer can point at and run, and something a future real-data
retraining pipeline can import directly, instead of trusting someone to
remember the rule by hand each time a retrain is considered.

Nothing in this app calls this yet -- there is no live retraining pipeline
against real orders today, because there is no real order history yet (see
README's "Honest scope notes"). This module exists so the promotion rule
itself is precise and tested well before it has real data to run against,
the same "build the discipline before you need it" approach already used
for every model's fail-closed fallback (see ml_models.py).
"""

MIN_REAL_EXAMPLES = 100
# Floor for having enough real accept/reject (or complete/fail) decisions to
# evaluate a binary classifier's held-out performance meaningfully at all.
# Below this, a measured "win" over the fallback is mostly noise -- it is a
# floor on trustworthy measurement, not a claim that 100 examples produces a
# good model.

MIN_IMPROVEMENT_MARGIN = 0.0
# A retrained candidate must score >= the fallback's held-out score by at
# least this margin to be promoted. 0.0 means "at least as good, not worse" --
# raise this above 0 if a tie should default to keeping the simpler, already-
# trusted fallback instead of switching for no measurable gain.


def is_ready_to_promote(real_example_count, candidate_holdout_score, fallback_holdout_score):
    """Decide whether a retrained model should replace its fallback.

    Both scores must be measured on the SAME held-out split of real data for
    this comparison to mean anything -- that split, and computing both
    scores on it, is the caller's responsibility, not this function's.

    Returns (ready: bool, reason: str) -- the reason is meant to be logged or
    shown to whoever is reviewing a promotion decision, not just checked
    programmatically.
    """
    if real_example_count < MIN_REAL_EXAMPLES:
        return False, (
            f"only {real_example_count} real example(s) so far -- need at least "
            f"{MIN_REAL_EXAMPLES} before a held-out comparison is meaningful"
        )

    required = fallback_holdout_score + MIN_IMPROVEMENT_MARGIN
    if candidate_holdout_score < required:
        return False, (
            f"candidate scored {candidate_holdout_score:.3f} on held-out data, "
            f"which doesn't beat the fallback's {fallback_holdout_score:.3f} "
            f"by the required margin (needs >= {required:.3f})"
        )

    return True, (
        f"{real_example_count} real examples and the candidate beat the fallback "
        f"on held-out data ({candidate_holdout_score:.3f} vs {fallback_holdout_score:.3f}) "
        f"-- ready to promote"
    )
