"""Language detection for fallback messages.

Heuristic over the user's original query. Used by the guardrail to pick
a fallback in the user's language when blocking. Tradeoff: deterministic,
no extra dependency, ~95% accurate on short questions in es/en. For
broader corpora a library like `langdetect` would be more robust.
"""

# Substring markers — any occurrence anywhere in the (lowercased) query
# is treated as evidence of Spanish.
SPANISH_CHAR_MARKERS = (
    "¿", "¡",
    "á", "é", "í", "ó", "ú", "ñ", "ü",
)

# Word-boundary markers — matched as whole tokens after splitting on
# whitespace and stripping common Spanish punctuation. Includes both
# accented and unaccented forms (`qué`/`que`, `cómo`/`como`, …) because
# users often skip diacritics when typing.
SPANISH_WORD_MARKERS = frozenset({
    "qué", "cuál", "cuáles", "quién", "quiénes",
    "cómo", "dónde", "cuándo", "porqué",
    "que", "cual", "cuales", "quien", "quienes",
    "como", "donde", "cuando", "porque",
    "fue", "son", "hizo", "hicieron", "era", "eran",
    "hola", "gracias", "adiós", "adios",
})

_PUNCT_STRIP = ".,;:!?¿¡()[]{}\"'`"


def detect_language(query: str) -> str:
    """Return 'es' if query looks Spanish, 'en' otherwise."""
    q = (query or "").lower()
    if any(m in q for m in SPANISH_CHAR_MARKERS):
        return "es"
    for token in q.split():
        if token.strip(_PUNCT_STRIP) in SPANISH_WORD_MARKERS:
            return "es"
    return "en"
