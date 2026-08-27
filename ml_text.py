"""
Text-similarity layer for material names -- a small, real, from-data vector-space
model (TF-IDF over character n-grams + cosine similarity), replacing what used to be
two separate hand-coded heuristics:

  1. whatsapp_stub.py's detect_material() -- an exact-substring dict lookup
     (MATERIAL_ALIASES) that only matched a phrase it had literally seen before.
  2. matching.py's material_similarity() -- difflib.SequenceMatcher, which compares
     exactly two strings with no learned structure and no way to improve with data.

Why TF-IDF over character n-grams instead of neural sentence embeddings
(sentence-transformers / all-MiniLM-L6-v2, the more "obvious" choice): that library
pulls in PyTorch as a dependency, which is 500MB-2GB+ depending on platform. In this
sandbox a `pip install sentence-transformers` timed out after 90s just downloading
torch, and shipping that weight into a hackathon project that judges/teammates need
to `pip install -r requirements.txt` and run quickly is a real cost, not a
hypothetical one -- especially right after a separate cleanup pass where the venv
folder was already the single largest thing in the repo. TF-IDF + cosine similarity
is still a genuine vector-space ML technique (this is a standard approach in real
production fuzzy-matching/record-linkage systems, not a toy), it's already available
via scikit-learn (already a dependency here), it fits/predicts in milliseconds with
no model download, and -- because it operates on character n-grams, not whole words --
it's naturally robust to typos, pluralization, and spacing/hyphenation differences
(e.g. "sheet trimings" vs "sheet trimmings") in a way an exact-substring dict lookup
never could be. It does NOT by itself understand true synonyms that share no
characters (e.g. "iron filings" for "metal shavings") -- for that it leans on the
MATERIAL_ALIASES corpus below, same as before, but now every alias phrase is a
*training example* the vectorizer generalizes near, rather than a single hard-coded
string that has to match exactly.

If a genuinely offline-friendly small embedding model becomes available later
(fasttext's compressed vectors are a reasonable next step), swapping it in only
touches this file -- every caller here goes through best_material_match() /
material_similarity(), never the vectorizer directly.

Hinglish / mixed Hindi-English intake: real MSME owners very often don't write
clean English ("220kg shavings hai, chahiye kal tak") -- MATERIAL_ALIASES,
NEED_WORDS, and WASTE_WORDS below each include their common romanized-Hindi
equivalents (e.g. "katran" for fabric offcuts, "chahiye" for need, "gatta" for
cardboard) as ordinary training examples/keywords, so the *same* TF-IDF
vectorizer and keyword fallback this file already had understand code-switched
messages too, with no separate code path. This is the deliberately lightweight
half of that feature; see ml_multilingual.py for the documented (but not
installed-by-default) upgrade path to a real MuRIL/IndicBERT model for
messages this word/n-gram-level approach still can't parse -- e.g. Hindi
written in Devanagari script, or sentence structures with no English words at
all for either of these lists to anchor on.
"""

from __future__ import annotations

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

import data

# Known synonym/slang phrases per canonical material key. This is the actual
# "training corpus" for the vectorizer below -- expanding this list is how you
# teach the matcher a new way of describing a material, exactly like adding
# labeled examples would improve any other text classifier here.
MATERIAL_ALIASES = {
    # Each list mixes English synonyms with romanized Hindi/Hinglish terms an
    # MSME owner might actually type over WhatsApp (e.g. "loha" for iron/metal,
    # "gatta" for cardboard, "katran" -- a real, commonly used Hindi word
    # specifically for fabric offcuts/trimmings) -- see the Hinglish-intake
    # note near the bottom of this file for why these live here rather than
    # in a separate lookup table: every phrase here is a training example the
    # TF-IDF vectorizer generalizes near, English or Hinglish alike, not a
    # second hard-coded dict that would need its own separate matching logic.
    "metal_shavings": ["metal scrap", "scrap metal", "shavings", "steel shavings", "iron filings", "metal filings", "turnings", "metal turnings", "loha", "lohe ka scrap", "lohe ka choora", "dhatu scrap"],
    "cast_offcuts": ["casting scrap", "cast offcuts", "casting offcuts", "mould offcuts", "dhalai scrap"],
    "sheet_trimmings": ["sheet metal trimmings", "trimmings", "sheet scrap", "sheet offcuts", "patti ka scrap"],
    "metal_dust": ["metal powder", "grinding dust", "metal fines", "dhatu ka choora"],
    "bagasse": ["sugarcane waste", "cane bagasse", "bagasse waste", "sugarcane bagasse", "ganne ka chilka"],
    "paper_pulp_reject": ["pulp reject", "paper pulp waste", "pulp rejects", "paper waste pulp", "kagaz ka gooda"],
    "cardboard_offcuts": ["cardboard scrap", "carton offcuts", "corrugated offcuts", "cardboard waste", "gatta", "gatte ka scrap"],
    "fabric_offcuts": ["cloth offcuts", "textile offcuts", "fabric scraps", "cloth scraps", "garment offcuts", "kapda", "kapde ki katran", "katran"],
    "dye_sludge": ["dye waste", "colour sludge", "color sludge", "dyeing sludge", "rang ka kichad"],
    "process_effluent_solids": ["effluent solids", "effluent sludge", "process waste solids", "wastewater solids", "gande paani ka kichad"],
    "plastic_regrind": ["plastic scrap", "regrind plastic", "recycled plastic granules", "plastic granules", "plastic ka scrap"],
    "sawdust": ["wood dust", "saw dust", "wood shavings", "burada", "lakdi ka burada"],
    "rubber_scrap": ["rubber waste", "tyre scrap", "tire scrap", "rubber offcuts", "rubber ka scrap"],
    "leather_scraps": ["leather offcuts", "leather waste", "leather trimmings", "chamda", "chamde ka scrap"],
    "electronic_scrap": ["e-waste", "ewaste", "electronic waste", "circuit board scrap", "e waste", "purana electronic saman"],
    "glass_cullet": ["glass scrap", "broken glass", "glass waste", "cullet", "kaanch", "tuta hua kaanch"],
    "fruit_pulp_waste": ["juice pulp", "phal ka chilka"],
    "press_mud": ["pressmud", "filter mud", "sugar press mud"],
}

# English trigger words/phrases, plus their common romanized Hindi/Hinglish
# equivalents -- an MSME owner texting on WhatsApp very often mixes the two in
# one sentence ("220kg shavings hai, chahiye kal tak"), and a keyword list
# that only recognizes the English half misses the Hindi half's own clear
# signal entirely. "hai" (a generic copula -- "is/are/have") is deliberately
# NOT included here on its own: it shows up in Hindi sentences of every kind
# (need, waste, or neither), so on its own it would false-positive constantly;
# only the more specific waste-leaning phrases below are included. Checked in
# this order (need words first) by whatsapp_stub._detect_type_keyword_fallback,
# so an unambiguous need word like "chahiye" wins even in a sentence that also
# contains a generic possession word.
NEED_WORDS = [
    "need", "want", "require", "looking for", "buy", "in need of", "seeking", "require some", "want to purchase",
    "chahiye", "zaroorat", "zarurat", "kharidna", "kharidna hai", "mangwana", "khatam ho gaya", "khatam ho gayi",
]
WASTE_WORDS = [
    "have", "waste", "giving away", "selling", "byproduct", "surplus", "extra", "generate", "produce", "excess",
    "pada hai", "padi hai", "faltu", "becna hai", "de denge", "de sakte", "nikal raha", "nikal rahi", "khaali karna",
]

# ---------------------------------------------------------------------------
# Lazily-built vectorizers. Rebuilt whenever the set of known materials changes
# (a listing can be posted with a brand-new material name at runtime -- see
# data.add_listing() -- so this can't be a fit-once-at-import singleton).
#
# Two vectorizers, not one, and their similarities are averaged:
#   - char_wb n-grams: robust to typos/spacing/pluralization ("trimings" ~=
#     "trimmings"), but on its own it can be fooled by partial word overlaps
#     across unrelated documents (e.g. "casting" in a query sharing n-grams
#     with "cast_offcuts" purely because of shared substrings).
#   - word-level 1-2 grams: exact-word/short-phrase matching ("e waste" hits
#     electronic_scrap's alias "e waste" precisely), which anchors down the
#     cases the char-level model alone gets loose on.
# Combining both is a standard, unglamorous way to make short-text matching
# more precise without needing a neural embedding model.
# ---------------------------------------------------------------------------
_cache = {"keys": None, "char_vec": None, "word_vec": None, "char_matrix": None, "word_matrix": None, "material_keys": None}


def _material_document(material_key):
    phrases = [material_key.replace("_", " ")] + MATERIAL_ALIASES.get(material_key, [])
    return " ".join(phrases)


def _ensure_fitted():
    current_keys = frozenset(data.PRICE_TABLE_RS_PER_KG.keys())
    if _cache["keys"] == current_keys:
        return
    material_keys = sorted(current_keys)
    documents = [_material_document(k) for k in material_keys]
    char_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5), min_df=1)
    # token_pattern widened from sklearn's default (which requires 2+ word chars,
    # silently dropping single-letter tokens) so short tokens like the "e" in
    # "e waste" still register as a word instead of vanishing before matching.
    word_vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), min_df=1, token_pattern=r"(?u)\b\w+\b")
    char_matrix = char_vec.fit_transform(documents)
    word_matrix = word_vec.fit_transform(documents)
    _cache.update(
        keys=current_keys, char_vec=char_vec, word_vec=word_vec,
        char_matrix=char_matrix, word_matrix=word_matrix, material_keys=material_keys,
    )



# Word-level similarity is weighted higher than char-level: word n-grams match
# whole meaningful terms, while char n-grams can be fooled by a long shared
# root (e.g. "casting" overlapping heavily with "cast_offcuts" purely on
# substrings) into outranking the semantically correct word-level match. Char
# similarity still pulls its weight for typos/spacing variants the word-level
# model can't see at all (a misspelled word shares zero word-tokens with the
# correct one, but still shares plenty of 3-5 character n-grams).
CHAR_WEIGHT = 0.35
WORD_WEIGHT = 0.65


def _combined_sims(text):
    char_sims = cosine_similarity(_cache["char_vec"].transform([text]), _cache["char_matrix"])[0]
    word_sims = cosine_similarity(_cache["word_vec"].transform([text]), _cache["word_matrix"])[0]
    return CHAR_WEIGHT * char_sims + WORD_WEIGHT * word_sims


def best_material_match(text, threshold=0.25):
    """Return (material_key, score) for the material whose reference document is
    closest to `text` by combined char+word TF-IDF cosine similarity, or
    (None, best_score) if nothing clears `threshold`. Typo/spacing-robust by
    construction (character n-grams); synonym-robust to the extent MATERIAL_ALIASES
    has seen the phrase before (word n-grams)."""
    _ensure_fitted()
    material_keys = _cache["material_keys"]
    if not material_keys:
        return None, 0.0
    sims = _combined_sims(text.lower())
    best_idx = int(sims.argmax())
    best_score = float(sims[best_idx])
    if best_score < threshold:
        return None, best_score
    return material_keys[best_idx], best_score


def material_similarity(material_a, material_b):
    """Combined char+word TF-IDF cosine similarity (0..1) between two materials'
    reference documents -- drop-in replacement for the old
    difflib.SequenceMatcher ratio in matching.py, now benefiting from the same
    alias corpus best_material_match() uses (so "metal_dust" vs
    "metal_shavings" score higher than raw string overlap alone would suggest,
    because both documents include closely related alias phrases)."""
    _ensure_fitted()
    material_keys = _cache["material_keys"]
    if material_a not in material_keys or material_b not in material_keys:
        # One of these is a material key not yet in PRICE_TABLE_RS_PER_KG at fit
        # time (shouldn't normally happen -- both come from real listings) --
        # fit fresh single-shot vectors as a safe fallback rather than erroring.
        doc_a, doc_b = _material_document(material_a), _material_document(material_b)
        char_vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 5))
        word_vec = TfidfVectorizer(analyzer="word", ngram_range=(1, 2), token_pattern=r"(?u)\b\w+\b")
        try:
            cm = char_vec.fit_transform([doc_a, doc_b])
            wm = word_vec.fit_transform([doc_a, doc_b])
            return float(CHAR_WEIGHT * cosine_similarity(cm[0], cm[1])[0][0] + WORD_WEIGHT * cosine_similarity(wm[0], wm[1])[0][0])
        except ValueError:
            return 0.0
    idx_a = material_keys.index(material_a)
    idx_b = material_keys.index(material_b)
    char_sim = cosine_similarity(_cache["char_matrix"][idx_a], _cache["char_matrix"][idx_b])[0][0]
    word_sim = cosine_similarity(_cache["word_matrix"][idx_a], _cache["word_matrix"][idx_b])[0][0]
    combined = CHAR_WEIGHT * char_sim + WORD_WEIGHT * word_sim
    # Floating-point cosine similarity of a vector with itself can land at
    # 1.0000000000000009 instead of exactly 1.0 (sklearn normalizes then dots,
    # so tiny rounding error survives) -- clamp to the mathematically valid
    # [0, 1] range so identical materials always compare as exactly equal.
    return float(min(1.0, max(0.0, combined)))
