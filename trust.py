"""
Trust score layer.

After every completed exchange, both units rate each other (1-5). This module rolls up
all ratings a unit has received into a single cumulative trust score, which the matching
engine (see matching.score_chain) uses as a confidence signal, and which is shown to
owners before they commit to a new match with someone they haven't worked with before.

Backed by the real `trust_ratings` table (see models.py) -- record_exchange() commits
two rows (one per direction) straight to the database, so scores survive a server
restart instead of living only in a process-local dict. Historical seed ratings are
loaded once by database_migration.py's seed_trust_ratings(), not by this module, so
restarting the server (which re-creates this ledger) never re-seeds or duplicates them.
"""

import data
from models import db, TrustRating

# UNITS must stay a data.UNITS attribute lookup, not a bare "from data import UNITS" --
# see the note in matching.py for why. TrustLedger is a long-lived singleton (created
# once in app.py), so all_scores()/summary() re-read data.UNITS on every call rather
# than freezing the unit list at construction time -- otherwise a unit registered after
# startup would never show up in the trust leaderboard.

DEFAULT_SCORE = 3.5  # neutral starting point for a unit with no rating history yet

# --- Ring-trading / gaming mitigations --------------------------------------
# A pair of units that trade almost exclusively with each other can inflate
# both sides' trust scores and "completed exchanges" counts without creating
# any real cluster-wide value -- previously an acknowledged but unmitigated
# risk. None of the three rules below need machine learning; they're cheap,
# transparent stopgaps that apply from day one, while full graph-based
# anomaly detection across the whole trade network stays a scoped, harder
# Phase 2 item.

MAX_COUNTED_RATINGS_PER_COUNTERPARTY = 3
# Ratings received from the same counterparty beyond this many stop moving
# the score -- caps how much a single repeat trading partner can inflate a
# unit's number, without discarding the underlying exchange history itself
# (completed_exchanges below still reports the real, uncapped count).

MIN_DISTINCT_COUNTERPARTIES_FOR_TOP_TIER = 2
# app.py's "Highly recommended" badge now requires proof of trading with more
# than one counterparty, not just enough total exchanges -- closes the gap
# where two units trading only with each other could look highly recommended
# despite zero evidence they work with anyone else in the cluster.

CONCENTRATION_FLAG_MIN_EXCHANGES = 3
# Only judge concentration once there's enough history to judge at all --
# a unit's first exchange is trivially "100% with one partner" and flagging
# that would just be noise.

CONCENTRATION_FLAG_RATIO = 0.7
# A unit whose completed exchanges are >=70% with a single counterparty gets
# surfaced to admins for manual review (see concentrated_pairs_report below)
# -- not blocked automatically, since a real, legitimate two-unit partnership
# can look the same from the numbers alone; a human makes the actual call.


class _RatingsReceivedView:
    """Read-only, dict-like view over the trust_ratings table, keyed by
    to_unit_id -- kept so existing code (app.py's _trust_badge_info) that does
    `ledger.ratings_received.get(unit_id, [])` keeps working unchanged, while
    the values themselves are read live from the database instead of a
    snapshot frozen at process start."""

    def get(self, unit_id, default=None):
        ratings = TrustRating.query.filter_by(to_unit_id=unit_id).all()
        if not ratings:
            return [] if default is None else default
        return [r.rating for r in ratings]

    def with_counterparty(self, unit_id):
        """Same underlying rows as get(), but paired with who gave each
        rating (from_unit_id) -- get() alone discards that, and the
        ring-trading cap, the diversity check, and the concentration report
        all need to know *who* rated a unit, not just what they rated it."""
        rows = TrustRating.query.filter_by(to_unit_id=unit_id).all()
        return [(r.from_unit_id, r.rating) for r in rows]

    def __getitem__(self, unit_id):
        return self.get(unit_id)


class TrustLedger:
    def __init__(self):
        self.ratings_received = _RatingsReceivedView()

    def record_exchange(self, from_id, to_id, rating_from_gives_to_to, rating_to_gives_to_from, order_id_str=None):
        """Both sides rate each other after a completed exchange -- persisted as
        two TrustRating rows so the score survives a restart. order_id_str, if
        given, is the order's public "ORD001"-style id; it's parsed back to the
        numeric primary key so the rating rows link to the order that produced
        them (see TrustRating.order_id in models.py)."""
        order_id = None
        if order_id_str and order_id_str.startswith("ORD"):
            try:
                order_id = int(order_id_str[3:])
            except ValueError:
                order_id = None

        db.session.add(TrustRating(
            from_unit_id=from_id, to_unit_id=to_id,
            rating=rating_from_gives_to_to, order_id=order_id,
        ))
        db.session.add(TrustRating(
            from_unit_id=to_id, to_unit_id=from_id,
            rating=rating_to_gives_to_from, order_id=order_id,
        ))
        db.session.commit()

    def _counted_ratings(self, unit_id):
        """Ratings received, capped at MAX_COUNTED_RATINGS_PER_COUNTERPARTY
        per counterparty -- see the ring-trading mitigation notes above.
        A unit trading exclusively with one partner still gets a score (it's
        not zeroed out or excluded), but that partner can't keep pushing the
        number up indefinitely just by rating the same unit over and over."""
        seen_from = {}
        counted = []
        for from_id, rating in self.ratings_received.with_counterparty(unit_id):
            seen = seen_from.get(from_id, 0)
            if seen < MAX_COUNTED_RATINGS_PER_COUNTERPARTY:
                counted.append(rating)
                seen_from[from_id] = seen + 1
        return counted

    def score(self, unit_id):
        ratings = self._counted_ratings(unit_id)
        if not ratings:
            return DEFAULT_SCORE
        return round(sum(ratings) / len(ratings), 2)

    def distinct_counterparties(self, unit_id):
        """How many different units a unit has actually completed exchanges
        with -- used to gate app.py's "Highly recommended" badge so a large
        exchange count with a single repeat partner doesn't read the same as
        a real track record across the cluster."""
        pairs = self.ratings_received.with_counterparty(unit_id)
        return len({from_id for from_id, _ in pairs})

    def concentrated_pairs_report(self):
        """Flags units whose completed exchanges are concentrated almost
        entirely with one counterparty -- a cheap, rule-based stand-in for
        full graph-based anomaly detection (still a scoped Phase 2 item), so
        there's a real, working mitigation for ring-trading from day one
        rather than just a named, unaddressed risk. Surfaced to admins on the
        dashboard for manual review, not auto-blocked -- a genuine two-unit
        partnership can look identical in the raw numbers, so a human makes
        the actual call."""
        from collections import Counter

        flagged = []
        for u in data.UNITS:
            pairs = self.ratings_received.with_counterparty(u["id"])
            total = len(pairs)
            if total < CONCENTRATION_FLAG_MIN_EXCHANGES:
                continue
            counts = Counter(from_id for from_id, _ in pairs)
            top_partner_id, top_count = counts.most_common(1)[0]
            if top_count / total < CONCENTRATION_FLAG_RATIO:
                continue
            partner_unit = data.unit_by_id(top_partner_id)
            flagged.append({
                "unit_id": u["id"],
                "unit_name": u["name"],
                "partner_id": top_partner_id,
                "partner_name": partner_unit["name"] if partner_unit else top_partner_id,
                "shared_exchanges": top_count,
                "total_exchanges": total,
                "concentration_pct": round(100 * top_count / total),
            })
        flagged.sort(key=lambda r: r["concentration_pct"], reverse=True)
        return flagged

    def all_scores(self):
        return {u["id"]: self.score(u["id"]) for u in data.UNITS}

    def summary(self):
        out = []
        for u in data.UNITS:
            ratings = self.ratings_received.get(u["id"], [])
            out.append({
                "unit_id": u["id"],
                "name": u["name"],
                "trust_score": self.score(u["id"]),
                "completed_exchanges": len(ratings),
            })
        out.sort(key=lambda r: r["trust_score"], reverse=True)
        return out


if __name__ == "__main__":
    from app import app
    with app.app_context():
        ledger = TrustLedger()
        print("Trust scores (persisted in the trust_ratings table):\n")
        for row in ledger.summary():
            tag = "no history yet -> neutral default" if row["completed_exchanges"] == 0 else f"{row['completed_exchanges']} rated exchange(s)"
            print(f"{row['name']}: {row['trust_score']} / 5  ({tag})")
