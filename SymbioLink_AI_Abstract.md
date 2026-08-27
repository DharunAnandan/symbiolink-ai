# SymbioLink AI — Abstract

**Team CircuLink · Sustainability Hackathon 2026 · Theme: Circular Economy**

SymbioLink AI is a working prototype for a digital industrial-symbiosis platform that
connects small and medium manufacturing units (MSMEs) within a shared industrial
cluster, turning one factory's waste into another factory's cheap raw material. Rather
than a simple one-to-one classifieds board, the system models the cluster as a graph
and searches for both direct matches and 2–3 hop exchange chains — Unit A's waste
feeding Unit B, whose own byproduct feeds Unit C — ranking each by estimated cost
savings, transport distance, and a trust score built from past completed exchanges.

A predictive layer forecasts a unit's next likely waste listing from its posting
history, so a buyer can be lined up before the waste is even generated, and a
logistics-pooling layer groups nearby low-volume matches into shared transport runs
that would otherwise be too small to justify a solo pickup. Listings can be created
through a lightweight web form or, in a simulated preview of the intended production
path, through a WhatsApp or voice-note message parsed into a structured listing —
removing the literacy and app-adoption barriers that keep many MSME owners out of
digital marketplaces today.

A full order lifecycle — placed, confirmed, picked up, delivered, completed — closes
the loop: every completed exchange feeds a two-way rating back into the trust ledger,
which in turn raises or lowers that unit's confidence score in future match rankings,
creating a self-reinforcing feedback cycle between transactions and trust.

Built with Flask on a synthetic dataset modeled on documented Indian industrial
cluster patterns (e.g. Muzaffarnagar's paper/sugar-mill belt, Mysore's mixed
manufacturing cluster), the prototype is intentionally rules-based and transparent
rather than a black-box model, with reference prices and transport rates flagged
honestly as illustrative estimates pending real-world tuning ahead of a pilot
deployment.
