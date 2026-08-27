"""
Optional upgrade path: real multilingual understanding for Hinglish intake,
via MuRIL (Google's BERT-family model pretrained specifically on Hindi-English
code-switched text) or IndicBERT.

ml_text.py's TF-IDF material matcher and whatsapp_stub.py's keyword fallback
already understand a good deal of common romanized Hindi phrasing (see
ml_text.py's MATERIAL_ALIASES / NEED_WORDS / WASTE_WORDS, and the Hinglish
templates ml_training_data.py mixes into the trained classifier's own
training set) -- and that's deliberately the lightweight, always-on default,
for the same reason ml_text.py's own docstring gives for not defaulting to
sentence-transformers: a transformer model means PyTorch, which is a
500MB-2GB+ dependency that previously timed out installing in this exact
sandbox. That tradeoff is still real here.

What word/n-gram matching genuinely can't do, no matter how many alias
phrases are added by hand: understand a message with NO shared characters
with any known English/romanized alias -- Hindi written in Devanagari script,
an alias phrase nobody thought to add, or unusual sentence structure. That's
what an actual language model (trained on real code-switched text, as MuRIL
specifically was) is for.

This module is that upgrade path, written and ready, but INACTIVE by default:
- Set the env var USE_MURIL=1 (see config.py / .env.example) to opt in.
- `pip install transformers torch` (not in requirements.txt by default --
  same "documented, not force-installed" treatment README.md already gives
  the real Twilio/Google Maps integrations) to make it available.
- If both aren't true, every function below returns None/unavailable and
  every caller (whatsapp_stub.detect_material/detect_type) falls through to
  the existing TF-IDF + keyword path exactly as if this module didn't exist
  -- same graceful-degradation contract as ml_models.py's trained-model layer.

Once active, embed() produces a MuRIL sentence embedding (mean-pooled last
hidden state) for arbitrary text -- Hinglish, Devanagari, or plain English
alike -- and best_material_match_muril() reuses that embedding to find the
closest material by cosine similarity against each material's reference
alias document (the same documents ml_text.py already maintains), so a
message like "लोहे का चूरा" (Devanagari for "iron filings") could be
matched purely on learned cross-lingual meaning, with no shared characters at
all -- something no amount of hand-added ASCII alias phrases could do.
"""

import os

import ml_text

USE_MURIL = os.environ.get("USE_MURIL", "").lower() in ("1", "true", "yes")
MODEL_NAME = os.environ.get("MURIL_MODEL_NAME", "google/muril-base-cased")

_state = {"checked": False, "tokenizer": None, "model": None, "torch": None}


def available():
    """True only if USE_MURIL is set AND transformers+torch actually import
    successfully -- checked once and cached, so a missing/broken install
    doesn't retry an expensive import on every message."""
    if not USE_MURIL:
        return False
    if _state["checked"]:
        return _state["model"] is not None
    _state["checked"] = True
    try:
        import torch
        from transformers import AutoTokenizer, AutoModel
        tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        model = AutoModel.from_pretrained(MODEL_NAME)
        model.eval()
        _state.update(tokenizer=tokenizer, model=model, torch=torch)
    except Exception as e:
        print(f"Info: MuRIL unavailable ({e}) -- falling back to ml_text's TF-IDF + keyword layer.")
        _state.update(tokenizer=None, model=None, torch=None)
    return _state["model"] is not None


def embed(text):
    """Mean-pooled MuRIL sentence embedding for arbitrary text, or None if
    the model isn't available (see available())."""
    if not available():
        return None
    torch = _state["torch"]
    tokenizer, model = _state["tokenizer"], _state["model"]
    with torch.no_grad():
        tokens = tokenizer(text, return_tensors="pt", truncation=True, padding=True, max_length=64)
        output = model(**tokens)
        mask = tokens["attention_mask"].unsqueeze(-1).float()
        pooled = (output.last_hidden_state * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
    return pooled[0]


_material_embedding_cache = {"keys": None, "embeddings": None}


def _material_embeddings():
    material_keys = sorted(ml_text.MATERIAL_ALIASES.keys())
    if _material_embedding_cache["keys"] == material_keys:
        return material_keys, _material_embedding_cache["embeddings"]
    embeddings = [embed(ml_text._material_document(k)) for k in material_keys]
    _material_embedding_cache.update(keys=material_keys, embeddings=embeddings)
    return material_keys, embeddings


def best_material_match_muril(text, threshold=0.55):
    """Same (material_key, score) contract as ml_text.best_material_match(),
    backed by MuRIL cosine similarity instead of TF-IDF -- only meaningfully
    callable once available() is True. MuRIL's similarity scale runs higher
    than TF-IDF cosine's typically does, hence the higher default threshold."""
    if not available():
        return None, 0.0
    torch = _state["torch"]
    query_vec = embed(text)
    if query_vec is None:
        return None, 0.0
    material_keys, embeddings = _material_embeddings()
    if not material_keys:
        return None, 0.0
    sims = torch.nn.functional.cosine_similarity(query_vec.unsqueeze(0), torch.stack(embeddings))
    best_idx = int(torch.argmax(sims))
    best_score = float(sims[best_idx])
    if best_score < threshold:
        return None, best_score
    return material_keys[best_idx], best_score


if __name__ == "__main__":
    if available():
        print(f"MuRIL active ({MODEL_NAME}). Try it:")
        for text in ["loha ka scrap hai", "लोहे का चूरा", "kapde ki katran chahiye"]:
            key, score = best_material_match_muril(text)
            print(f"  {text!r} -> {key} ({score:.2f})")
    else:
        print(
            "MuRIL is not active in this environment.\n"
            "  - Set USE_MURIL=1 to opt in.\n"
            "  - pip install transformers torch (not installed by default -- see this file's docstring).\n"
            "Meanwhile, ml_text.py's TF-IDF matcher + Hinglish alias/keyword lists (the always-on default) handle Hinglish intake."
        )
