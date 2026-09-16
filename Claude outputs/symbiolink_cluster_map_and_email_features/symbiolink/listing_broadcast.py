"""
Cluster-wide announcement when a company posts a new listing.

The premise of an industrial-symbiosis cluster is that one factory's waste is
another's raw material -- but only if the other factory hears about it. Before
this module, a new listing only reached the units the matcher had already
paired it with (matching.notify_new_matches_for_unit()). That silently misses
everyone who *would* buy the material but has no open NEED listing for it on
file, which is most of the cluster most of the time.

So: a new listing sends two different emails to two different audiences.

  * Units the matcher paired with the listing get the existing, specific
    "New match" email -- it can quote an actual estimated saving.
  * Everybody else gets the general announcement built here.

These audiences are deliberately disjoint. announce() takes the matched unit
ids and excludes them, so no user ever receives two emails about one listing.
That exclusion is the whole reason this runs *after* the match notification
rather than beside it -- see the call sites in app.py.

Like every other notification path in this codebase (notifications.py,
email_service.py), everything here is best-effort and non-raising: a
broadcast failing must never break the listing creation that triggered it.
"""

import data
from config import Config
from email_service import email_service


def _price_estimate(material, listing_type):
    """Rough rupee value of the listing, for the stat tiles.

    A waste listing is priced at the material's byproduct rate (what the
    seller can realistically get for it); a need listing is priced at the
    new-material rate (what the buyer would otherwise pay a virgin
    supplier). Returns None for a material with no price on file rather
    than guessing -- the template simply drops the tile, which is better
    than showing a confidently wrong number to the whole cluster.
    """
    try:
        prices = data.PRICE_TABLE_RS_PER_KG.get(material)
        if not prices:
            return None
        rate = prices.get("byproduct" if listing_type == "waste" else "new_material")
        return None if rate is None else rate
    except Exception:
        return None


def _marketplace_url():
    """Absolute URL of the marketplace, for the email's CTA button.

    Built here rather than at each call site because one of those call sites
    is the WhatsApp webhook, where a failure to build an external URL (no
    SERVER_NAME configured, no usable request context) would raise *before*
    announce()'s own try/except could contain it -- and a missing button is
    never a good enough reason to lose a listing. Returns None on failure;
    the template simply omits the button.
    """
    try:
        from flask import url_for
        return url_for("search", _external=True)
    except Exception:
        return None


def announce(unit_id, listing, exclude_unit_ids=None, link_url=None):
    """Email every registered user about `listing`, except the poster and
    anyone in `exclude_unit_ids`.

    Returns the number of recipients queued (0 if the feature is switched
    off, nobody is left to notify, or anything went wrong). Delivery itself
    happens on a background thread -- see email_service.send_async() -- so
    this returns immediately and the user posting the listing never waits on
    25 SMTP round-trips before their confirmation page renders.
    """
    if not getattr(Config, "NOTIFY_ALL_ON_NEW_LISTING", True):
        return 0

    try:
        unit = data.unit_by_id(unit_id)
        if not unit or not listing:
            return 0

        material = listing.get("material", "")
        material_label = material.replace("_", " ").title()
        qty = listing.get("qty_kg") or 0
        listing_type = listing.get("type", "waste")
        company = unit.get("name", "A company")

        # Exclude the poster plus whoever already got the match email.
        excluded = {unit_id} | set(exclude_unit_ids or ())
        recipients = email_service.all_user_emails(exclude_unit_ids=excluded)
        if not recipients:
            return 0

        # A waste listing is something to BUY; a need listing is something to
        # SELL INTO. Getting this backwards would send the entire cluster a
        # confidently wrong call to action, so the two are spelled out in
        # full rather than sharing one generic "new listing" wording.
        if listing_type == "waste":
            subject = f"New material available: {material_label} ({qty:g} kg) from {company}"
            heading = f"{material_label} is available in your cluster"
            intro = (f"{company} has just listed {qty:g} kg of {material_label.lower()} "
                     f"as available waste. If your process can take this as an input, "
                     f"it is cheaper than virgin material and it is already nearby.")
        else:
            subject = f"Wanted: {material_label} ({qty:g} kg) by {company}"
            heading = f"{company} is looking for {material_label.lower()}"
            intro = (f"{company} has posted a need for {qty:g} kg of "
                     f"{material_label.lower()}. If this is a byproduct you are "
                     f"currently paying to dispose of, there is now a buyer for it "
                     f"in your cluster.")

        stats = [("Quantity", f"{qty:g} kg")]
        rate = _price_estimate(material, listing_type)
        if rate:
            stats.append(("Ref. rate", f"Rs {rate:g}/kg"))
            stats.append(("Lot value", f"Rs {rate * qty:,.0f}"))

        details = [
            ("Material", material_label),
            ("Listing type", "Available waste" if listing_type == "waste" else "Material needed"),
            ("Posted by", company),
            ("Sector", (unit.get("category") or "other").title()),
        ]
        if listing.get("interval_days"):
            details.append(("Repeats every", f"{listing['interval_days']} days"))

        return email_service.broadcast_template(
            recipients, subject, "listing", heading, intro,
            stats=stats,
            details=details,
            button_label="Browse the marketplace",
            button_url=link_url or _marketplace_url(),
            preheader=f"{qty:g} kg of {material_label.lower()} just listed by {company}.",
            footnote=("You are receiving this because you are registered on SymbioLink AI. "
                      "Every member of the cluster is notified when new material is posted."),
        )
    except Exception as e:
        # Never let an announcement failure escape into the request that
        # created the listing -- the listing itself is already saved.
        print(f"Error broadcasting new listing for unit {unit_id}: {e}")
        return 0
