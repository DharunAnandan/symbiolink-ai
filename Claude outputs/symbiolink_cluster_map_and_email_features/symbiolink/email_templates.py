"""
Branded HTML email templates for SymbioLink AI.

Why a separate module: email_service.py is the *transport* (SMTP, connection
reuse, recipient lookup) and shouldn't grow a few hundred lines of markup.
This is the *presentation* layer -- the same split templates/ already has
from app.py.

Email HTML is not web HTML. Gmail/Outlook/Apple Mail strip <style> blocks,
ignore flexbox and grid, and drop external stylesheets entirely, so
everything here is deliberately old-fashioned:

  * table-based layout, never div + flex
  * every style inlined on the element via style="..."
  * a solid bgcolor alongside every gradient (Outlook renders the bgcolor and
    ignores the gradient, which is why the two have to agree in tone)
  * width capped at 600px, the widest that survives every mobile client

Colors are lifted from static/style.css's custom properties so an email looks
like it came from the same product as the page it links to -- see PALETTE
below. Each email picks one ACCENTS entry, which is what makes a login alert
read blue and a new-listing alert read green with no per-template markup.
"""

# Straight from static/style.css's :root -- keep in sync if the app rebrands.
PALETTE = {
    "brand": "#16a34a",      # --brand, SymbioLink green
    "ink": "#164e63",        # --ink, deep teal text
    "muted": "#64748b",      # --muted
    "bg": "#ecfeff",         # --bg-solid, pale cyan page ground
    "surface": "#ffffff",    # --surface-solid
    "border": "#d7e7ec",     # flattened --border (email needs solid, not rgba)
    "cyan": "#0e7490",       # --link-cyan
    "violet": "#6d28d9",     # --cat-chemical-tx
    "amber": "#b45309",      # --amber
    "critical": "#dc2626",   # --critical
}

# Per-email-type accent: strong color, soft tint for panels, gradient partner.
# The tint is what stat cards and detail panels sit on, so it stays light
# enough for --ink text on top of it to keep a readable contrast ratio.
ACCENTS = {
    "login":   {"main": "#0e7490", "tint": "#e0f7fb", "grad": "#22d3ee", "emoji": "&#128274;"},
    "listing": {"main": "#16a34a", "tint": "#e7f9ee", "grad": "#4ade80", "emoji": "&#9851;"},
    "match":   {"main": "#6d28d9", "tint": "#f1ebfe", "grad": "#a78bfa", "emoji": "&#128279;"},
    "order":   {"main": "#b45309", "tint": "#fef4e2", "grad": "#fbbf24", "emoji": "&#128230;"},
    "alert":   {"main": "#dc2626", "tint": "#fdecec", "grad": "#f87171", "emoji": "&#9888;"},
    "welcome": {"main": "#16a34a", "tint": "#e7f9ee", "grad": "#22d3ee", "emoji": "&#127881;"},
}

FONT = ("-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, "
        "'Helvetica Neue', Arial, sans-serif")


def _esc(value):
    """Minimal HTML escape. Everything interpolated into these templates is
    user-supplied somewhere upstream (company names, free-text material names
    typed into the WhatsApp intake), so none of it can go in raw -- an
    apostrophe in "Farmer's Co-op" would only be cosmetic, but a stray angle
    bracket would break the layout for every recipient."""
    return (str(value)
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&#39;"))


def _stat_cards(stats, accent):
    """A row of colorful figure tiles (quantity, value, CO2 saved...).

    One <table> row of <td>s rather than separate blocks: a table row is the
    only construct that reliably sits side-by-side in Outlook and still
    degrades gracefully on a narrow phone screen."""
    if not stats:
        return ""
    width = max(1, 100 // len(stats))
    cells = []
    for label, value in stats:
        cells.append(
            '<td width="{w}%" align="center" bgcolor="{tint}" '
            'style="padding:14px 8px;border-radius:10px;">'
            '<div style="font:700 19px {font};color:{main};letter-spacing:-0.2px;">{v}</div>'
            '<div style="font:600 10px {font};color:{muted};text-transform:uppercase;'
            'letter-spacing:0.7px;padding-top:4px;">{l}</div></td>'.format(
                w=width, tint=accent["tint"], main=accent["main"], font=FONT,
                muted=PALETTE["muted"], v=_esc(value), l=_esc(label))
        )
        cells.append('<td width="10" style="font-size:0;line-height:0;">&nbsp;</td>')
    cells.pop()  # drop the trailing spacer cell
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="margin:0 0 22px 0;"><tr>' + "".join(cells) + '</tr></table>')


def _detail_rows(details):
    """Key/value lines under the message -- order ids, materials, distances."""
    if not details:
        return ""
    rows = []
    for i, (label, value) in enumerate(details):
        bg = PALETTE["surface"] if i % 2 == 0 else "#f8fcfd"
        rows.append(
            '<tr bgcolor="{bg}">'
            '<td style="padding:9px 14px;font:600 12px {font};color:{muted};'
            'white-space:nowrap;">{l}</td>'
            '<td style="padding:9px 14px;font:600 13px {font};color:{ink};" '
            'align="right">{v}</td></tr>'.format(
                bg=bg, font=FONT, muted=PALETTE["muted"], ink=PALETTE["ink"],
                l=_esc(label), v=_esc(value))
        )
    return ('<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
            'style="border:1px solid {b};border-radius:10px;border-collapse:separate;'
            'overflow:hidden;margin:0 0 22px 0;">{rows}</table>'.format(
                b=PALETTE["border"], rows="".join(rows)))


def _code_block(code, accent):
    """A one-time passcode, rendered to be read and retyped at a glance.

    Large, monospaced and letter-spaced, because the entire job of this
    element is to be transcribed correctly on the first try from a phone
    screen. Plain text inside a coloured panel rather than an image: image
    blocking is on by default in a lot of mail clients, and an OTP nobody can
    see is an account nobody can get into.
    """
    if not code:
        return ""
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 22px 0;"><tr>'
        '<td align="center" bgcolor="{tint}" style="padding:22px 12px;border-radius:12px;">'
        '<div style="font:600 10px {font};color:{muted};text-transform:uppercase;'
        'letter-spacing:1.2px;padding-bottom:10px;">Your sign-in code</div>'
        '<div style="font:700 34px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;'
        'color:{main};letter-spacing:9px;line-height:1;">{code}</div>'
        '</td></tr></table>'.format(
            tint=accent["tint"], font=FONT, muted=PALETTE["muted"],
            main=accent["main"], code=_esc(code))
    )


def _button(label, url, accent):
    """Call-to-action. Wrapped in its own table because a styled bare <a>
    loses its padding in Outlook -- the table cell is what holds the shape."""
    if not (label and url):
        return ""
    return (
        '<table role="presentation" cellpadding="0" cellspacing="0" '
        'style="margin:0 0 24px 0;"><tr>'
        '<td bgcolor="{main}" style="border-radius:8px;">'
        '<a href="{url}" style="display:inline-block;padding:12px 26px;'
        'font:700 14px {font};color:#ffffff;text-decoration:none;">{l}</a>'
        '</td></tr></table>'.format(
            main=accent["main"], url=_esc(url), font=FONT, l=_esc(label))
    )


def render(kind, heading, intro, stats=None, details=None, code=None,
           button_label=None, button_url=None, footnote=None, preheader=None):
    """Build the full branded HTML email.

    `kind` picks the accent from ACCENTS. An unknown kind falls back to the
    brand green rather than raising -- a mistyped kind should still deliver a
    readable email, not drop the notification entirely.
    """
    accent = ACCENTS.get(kind, ACCENTS["listing"])

    # The preheader is the grey preview line clients show next to the subject.
    # Hidden in the body itself; without one, clients scrape the first visible
    # text, which here would be the "SymbioLink AI" wordmark on every email.
    preheader_html = (
        '<div style="display:none;font-size:1px;color:{bg};max-height:0;'
        'overflow:hidden;">{t}</div>'.format(bg=PALETTE["bg"], t=_esc(preheader or intro))
    )

    return """<!doctype html>
<html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title></head>
<body style="margin:0;padding:0;background-color:{bg};">
{preheader}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0"
       bgcolor="{bg}" style="background-color:{bg};">
<tr><td align="center" style="padding:28px 12px;">

  <table role="presentation" width="600" cellpadding="0" cellspacing="0"
         style="width:100%;max-width:600px;background-color:{surface};
                border-radius:16px;overflow:hidden;
                box-shadow:0 2px 10px rgba(22,78,99,0.10);">

    <!-- Gradient masthead. bgcolor is the Outlook fallback for the gradient. -->
    <tr><td bgcolor="{accent_main}"
            style="background-color:{accent_main};
                   background-image:linear-gradient(135deg,{accent_main} 0%,{accent_grad} 100%);
                   padding:26px 32px;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0"><tr>
        <td style="font:800 17px {font};color:#ffffff;letter-spacing:-0.3px;">
          {emoji}&nbsp; SymbioLink <span style="font-weight:400;opacity:0.85;">AI</span>
        </td>
        <td align="right" style="font:600 10px {font};color:#ffffff;opacity:0.85;
                                 text-transform:uppercase;letter-spacing:1px;">
          Industrial Symbiosis
        </td>
      </tr></table>
    </td></tr>

    <tr><td style="padding:32px 32px 8px 32px;">
      <h1 style="margin:0 0 12px 0;font:800 23px {font};color:{ink};
                 letter-spacing:-0.4px;line-height:1.25;">{heading}</h1>
      <p style="margin:0 0 22px 0;font:400 15px/1.6 {font};color:{muted};">{intro}</p>
    </td></tr>

    <tr><td style="padding:0 32px;">
      {code_block}
      {cards}
      {rows}
      {button}
    </td></tr>

    <tr><td style="padding:0 32px 30px 32px;">
      <div style="height:1px;background-color:{border};margin:0 0 16px 0;"></div>
      <p style="margin:0;font:400 12px/1.6 {font};color:{muted};">{footnote}</p>
    </td></tr>
  </table>

  <p style="margin:16px 0 0 0;font:400 11px {font};color:{muted};max-width:600px;">
    SymbioLink AI &middot; turning one factory&#39;s waste into another&#39;s raw material
  </p>

</td></tr></table>
</body></html>""".format(
        title=_esc(heading), bg=PALETTE["bg"], preheader=preheader_html,
        surface=PALETTE["surface"], accent_main=accent["main"],
        accent_grad=accent["grad"], emoji=accent["emoji"], font=FONT,
        ink=PALETTE["ink"], muted=PALETTE["muted"], border=PALETTE["border"],
        heading=_esc(heading), intro=_esc(intro),
        code_block=_code_block(code, accent),
        cards=_stat_cards(stats, accent), rows=_detail_rows(details),
        button=_button(button_label, button_url, accent),
        footnote=_esc(footnote or "You are receiving this because your company "
                                 "is registered on SymbioLink AI."),
    )


def plain_text(heading, intro, stats=None, details=None, code=None,
               button_label=None, button_url=None, footnote=None):
    """The text/plain alternative part.

    Not optional politeness: a multipart email shipping HTML with no text
    fallback scores markedly worse with spam filters, and Gmail's own
    accessibility readers use this part. Mirrors the same content as
    render() rather than saying "view this in an HTML client"."""
    lines = ["SYMBIOLINK AI", "=" * 46, "", heading, "", intro, ""]
    if code:
        lines += ["    " + str(code), ""]
    for label, value in (stats or []):
        lines.append("  {}: {}".format(label, value))
    if stats:
        lines.append("")
    for label, value in (details or []):
        lines.append("  {}: {}".format(label, value))
    if details:
        lines.append("")
    if button_label and button_url:
        lines += ["{}: {}".format(button_label, button_url), ""]
    lines += ["-" * 46,
              footnote or ("You are receiving this because your company is "
                           "registered on SymbioLink AI.")]
    return "\n".join(lines)
