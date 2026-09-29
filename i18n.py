"""
Lightweight English/Hindi UI toggle.

Deliberately NOT a full i18n framework (no Flask-Babel, no .po/.mo compile
step, no per-string extraction tooling) -- that's a much larger effort that
would touch every one of this app's ~25 templates for a hackathon demo. This
covers the highest-visibility surfaces instead: the public landing page, the
login page, and the sidebar navigation that's on screen for every
authenticated page -- the parts a first-time visitor or a judge actually
reads before deciding whether the platform "speaks their language."

t(key) looks up `key` (the English string, used as its own lookup key) in
TRANSLATIONS[current_lang()] and falls back to returning `key` itself if
there's no entry -- so an untranslated string on a Hindi-selected page still
renders correctly in English rather than showing a blank or a raw lookup
key. That's an honest partial-coverage design: nothing ever looks broken,
it just silently stays English wherever translation hasn't been added yet.
"""

from flask import session

DEFAULT_LANG = "en"
SUPPORTED_LANGS = ("en", "hi")
LANG_LABELS = {"en": "EN", "hi": "हिंदी"}

TRANSLATIONS = {
    "hi": {
        # ---- Sidebar: brand / nav group labels ----
        "Techno Bytes": "टीम टेक्नो बाइट्स",
        "Overview": "अवलोकन",
        "Cluster Engine": "क्लस्टर इंजन",
        "Marketplace": "बाज़ार",
        "Admin": "प्रशासन",

        # ---- Sidebar: nav item labels ----
        "Dashboard": "डैशबोर्ड",
        "Access & Listing": "पहुंच और सूची",
        "Predictive": "पूर्वानुमान",
        "Matching Engine": "मिलान इंजन",
        "Logistics Pooling": "रसद पूलिंग",
        "Trust": "विश्वास",
        "Register a Listing": "सूची दर्ज करें",
        "Search Marketplace": "बाज़ार खोजें",
        "Market Prices": "बाज़ार मूल्य",
        "Units Directory": "इकाई निर्देशिका",
        "Orders": "ऑर्डर",
        "Admin Dashboard": "प्रशासन डैशबोर्ड",
        "User Management": "उपयोगकर्ता प्रबंधन",
        "Disputes": "विवाद",
        "Audit Logs": "ऑडिट लॉग",

        # ---- Sidebar: footer / identity card ----
        "switch company": "कंपनी बदलें",
        "log out": "लॉग आउट",
        "Act as your company →": "अपनी कंपनी के रूप में कार्य करें →",
        "Acting as": "के रूप में कार्यरत",

        # ---- Notification bell ----
        "Notifications": "सूचनाएं",
        "Mark all read": "सभी पढ़ा हुआ चिह्नित करें",
        "View all": "सभी देखें",

        # ---- Login page ----
        "Turn waste into raw material.": "कचरे को कच्चे माल में बदलें।",
        "Sign in to match, track, and manage your orders.": "मिलान, ट्रैकिंग और अपने ऑर्डर प्रबंधित करने के लिए साइन इन करें।",
        "Real-time matches": "रीयल-टाइम मिलान",
        "Order tracking": "ऑर्डर ट्रैकिंग",
        "Waste forecasting": "अपशिष्ट पूर्वानुमान",
        "Welcome back": "वापसी पर स्वागत है",
        "Sign in to continue to your dashboard.": "अपने डैशबोर्ड पर जारी रखने के लिए साइन इन करें।",
        "Username": "उपयोगकर्ता नाम",
        "Password": "पासवर्ड",
        "Remember me": "मुझे याद रखें",
        "Sign in": "साइन इन करें",
        "Don’t have an account?": "खाता नहीं है?",
        "Register here": "यहां पंजीकरण करें",
        "Demo credentials": "डेमो लॉगिन जानकारी",

        # ---- Landing page ----
        "Get started": "शुरू करें",
        "One MSME's waste is another's raw material.": "एक एमएसएमई का कचरा दूसरे का कच्चा माल है।",
        "SymbioLink AI finds the nearby business that needs exactly what your factory is throwing away — and matches it, automatically, across your whole industrial cluster.": "सिम्बायोलिंक एआई उस नज़दीकी व्यवसाय को ढूंढता है जिसे आपकी फैक्ट्री जो कचरा फेंक रही है उसकी ज़रूरत है — और उसे आपके पूरे औद्योगिक समूह में स्वचालित रूप से जोड़ता है।",
        "See how it works": "यह कैसे काम करता है देखें",
        "Multi-hop matching": "बहु-चरण मिलान",
        "Finds 2-3 step chains across the cluster, not just direct one-to-one matches.": "क्लस्टर में केवल सीधे मिलान ही नहीं, बल्कि 2-3 चरणों की श्रृंखलाएं भी ढूंढता है।",
        "Verified trust scores": "सत्यापित विश्वास स्कोर",
        "Every completed exchange is rated, so you know who has a real track record.": "हर पूर्ण लेनदेन को रेट किया जाता है, ताकि आपको पता चले कि किसका वास्तविक ट्रैक रिकॉर्ड है।",
        "Real cost & CO2e savings": "वास्तविक लागत और CO2e बचत",
        "Every match shows the rupees saved and the emissions avoided versus buying new.": "हर मिलान नए माल की खरीद की तुलना में बचाए गए रुपये और टाले गए उत्सर्जन को दिखाता है।",
        "Built for India's MSME clusters": "भारत के एमएसएमई क्लस्टरों के लिए बनाया गया",
        "WhatsApp-based listing intake, low-volume logistics pooling, and a marketplace designed for how small manufacturers actually work.": "व्हाट्सएप-आधारित सूची दर्ज करना, कम मात्रा में रसद पूलिंग, और एक बाज़ार जो छोटे निर्माताओं के वास्तविक कामकाज के अनुसार बनाया गया है।",

        # ---- Dashboard ----
        "Cluster Dashboard": "क्लस्टर डैशबोर्ड",
        "Overview of the demo industrial cluster. Explore each layer from the sidebar.": "डेमो औद्योगिक क्लस्टर का अवलोकन। साइडबार से प्रत्येक स्तर देखें।",
        "+ Register a listing": "+ सूची दर्ज करें",
        "Search marketplace": "बाज़ार खोजें",
        "Download impact report": "प्रभाव रिपोर्ट डाउनलोड करें",
        "Units in cluster": "क्लस्टर में इकाइयां",
        "Active listings": "सक्रिय सूचियां",
        "Matches found (incl. chains)": "मिलान मिले (श्रृंखलाओं सहित)",
        "Total estimated savings": "कुल अनुमानित बचत",
        "CO2e avoided (est.)": "CO2e टाला गया (अनु.)",
        "Top matches by estimated saving": "अनुमानित बचत के आधार पर शीर्ष मिलान",
        "Explore each layer": "प्रत्येक स्तर देखें",

        # ---- Sign in via email / registration verification ----
        "Sign in via email": "ईमेल से साइन इन करें",
        "Sign in via email instead": "इसके बजाय ईमेल से साइन इन करें",
        "Email me a sign-in code": "मुझे साइन-इन कोड ईमेल करें",
        "Enter your username or email and we'll email you a one-time sign-in code.": "अपना यूज़रनेम या ईमेल दर्ज करें, हम आपको एक बार का साइन-इन कोड ईमेल करेंगे।",
        "Sign in with a password instead": "इसके बजाय पासवर्ड से साइन इन करें",
        "Enter your code": "अपना कोड दर्ज करें",
        "Sign-in code": "साइन-इन कोड",
        "Verify and sign in": "सत्यापित करें और साइन इन करें",
        "Verify your email": "अपना ईमेल सत्यापित करें",
        "Verification code": "सत्यापन कोड",
        "Verify and create account": "सत्यापित करें और खाता बनाएं",
        "We emailed a 6-digit code to": "हमने 6 अंकों का कोड भेजा है",
        "Send me a new code": "मुझे नया कोड भेजें",
        "Resend code in": "कोड दोबारा भेजें",
        "Back to sign in": "साइन इन पर वापस जाएं",

        # ---- Sidebar user card ----
        "Company": "कंपनी",
        "Log out": "लॉग आउट",

        # ---- AI assistant ----
        "AI Assistant": "एआई सहायक",
    }
}


def current_lang():
    lang = session.get("lang", DEFAULT_LANG)
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


def t(key):
    """Translate `key` (an English string, used as its own lookup key) into
    the current session language. Falls back to the English key itself if
    unset or untranslated -- see module docstring."""
    lang = current_lang()
    if lang == DEFAULT_LANG:
        return key
    return TRANSLATIONS.get(lang, {}).get(key, key)
