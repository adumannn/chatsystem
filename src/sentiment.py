"""
Sentiment Analysis / Emotion Detection module for the ICDS Chat System.

Classifies a message into one of three sentiment categories:
    - Positive  😊
    - Neutral   😐
    - Negative  😡

And optionally into more detailed emotions:
    - Happy 😄, Excited 🥳, Loving ❤️, Angry 😡, Sad 😢

Strategy
--------
1. If TextBlob is installed, use its polarity score as the primary signal.
2. Otherwise, fall back to a built-in keyword/rule-based scorer that needs
   zero external packages.

Both paths feed into the same classification logic so the rest of the
codebase sees a single, stable API.
"""

from __future__ import annotations
import re

# ---------------------------------------------------------------------------
# Try to import TextBlob — graceful fallback if unavailable
# ---------------------------------------------------------------------------
try:
    from textblob import TextBlob
    _HAS_TEXTBLOB = True
except ImportError:
    _HAS_TEXTBLOB = False

# ---------------------------------------------------------------------------
# Built-in keyword lexicon (used when TextBlob is unavailable, or as a
# secondary signal to boost accuracy)
# ---------------------------------------------------------------------------

_POSITIVE_WORDS = {
    # happy / joy
    "happy", "glad", "joy", "joyful", "cheerful", "delighted", "pleased",
    "wonderful", "fantastic", "great", "awesome", "amazing", "excellent",
    "brilliant", "superb", "good", "nice", "love", "loved", "loving",
    "like", "enjoy", "enjoyed", "fun", "funny", "laugh", "laughing",
    "smile", "smiling", "beautiful", "gorgeous", "pretty", "cool",
    "perfect", "best", "yay", "wow", "hurray", "hooray", "congrats",
    "congratulations", "thanks", "thank", "grateful", "blessed",
    "excited", "exciting", "thrilled", "enthusiastic", "proud",
    "incredible", "magnificent", "outstanding", "splendid", "terrific",
    "marvelous", "fabulous", "charming", "sweet", "kind", "generous",
    "helpful", "friendly", "warm", "heartfelt", "paradise",
    "celebrate", "celebrating", "celebration", "win", "winning", "won",
    "succeed", "success", "successful", "achieve", "achieved",
    "accomplish", "accomplished", "bravo", "haha", "lol", "lmao",
    "rofl", "xd",
}

_NEGATIVE_WORDS = {
    # angry / frustration
    "angry", "mad", "furious", "rage", "raging", "hate", "hated",
    "hatred", "annoyed", "annoying", "irritated", "irritating",
    "frustrated", "frustrating", "disgusted", "disgusting", "awful",
    "terrible", "horrible", "dreadful", "worst", "bad", "ugly",
    "stupid", "idiot", "fool", "foolish", "dumb", "pathetic",
    "useless", "trash", "garbage", "rubbish",
    # sad / despair
    "sad", "unhappy", "depressed", "depressing", "miserable",
    "heartbroken", "lonely", "alone", "cry", "crying", "cried",
    "tears", "sorrow", "grief", "pain", "painful", "hurt", "hurting",
    "suffer", "suffering", "hopeless", "desperate", "disappointed",
    "disappointing", "regret", "sorry", "apologize", "fail", "failed",
    "failure", "lose", "lost", "losing", "broke", "broken",
    "sick", "tired", "exhausted", "bored", "boring", "worried",
    "worry", "anxious", "fear", "scared", "afraid", "nervous",
    "stressed", "ugh", "ew",
}

# Detailed emotion keywords
_HAPPY_WORDS   = {"happy", "glad", "joy", "joyful", "cheerful", "delighted",
                  "pleased", "smile", "smiling", "laugh", "laughing",
                  "haha", "lol", "lmao", "rofl", "xd", "funny", "fun"}
_EXCITED_WORDS = {"excited", "exciting", "thrilled", "enthusiastic",
                  "yay", "wow", "hurray", "hooray", "awesome", "amazing",
                  "incredible", "celebrate", "celebrating", "bravo",
                  "win", "winning", "won"}
_LOVING_WORDS  = {"love", "loved", "loving", "adore", "cherish",
                  "sweetheart", "darling", "dear", "sweet", "beautiful",
                  "gorgeous", "pretty"}
_ANGRY_WORDS   = {"angry", "mad", "furious", "rage", "raging", "hate",
                  "hated", "hatred", "annoyed", "annoying", "irritated",
                  "frustrated", "disgusted", "disgusting", "idiot",
                  "stupid", "dumb"}
_SAD_WORDS     = {"sad", "unhappy", "depressed", "depressing", "miserable",
                  "heartbroken", "lonely", "cry", "crying", "cried",
                  "tears", "sorrow", "grief", "hopeless", "desperate"}

# Positive / negative emojis in the message text itself
_POSITIVE_EMOJIS = set("😀😃😄😁😆😂🤣😊😇🙂😍🥰😘😗😋😛😜🤪😝🤗"
                       "🥳🤩😎👍👏🎉🎊✨🌟💯🔥🚀🌈")
_NEGATIVE_EMOJIS = set("😢😭😞😔😟😕🙁☹😣😖😫😩😤😠😡🤬💔👎")

# Intensifiers & negation
_INTENSIFIERS = {"very", "really", "so", "extremely", "incredibly",
                 "absolutely", "totally", "super", "utterly"}
_NEGATION     = {"not", "no", "never", "neither", "nor", "don't",
                 "doesn't", "didn't", "won't", "wouldn't", "can't",
                 "cannot", "couldn't", "shouldn't", "isn't", "aren't",
                 "wasn't", "weren't", "hardly", "barely", "scarcely"}


# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    """Lowercase split, keeping emoji characters."""
    return re.findall(r"[\w']+|[^\s\w]", text.lower())


def _keyword_polarity(text: str) -> float:
    """Return a polarity score in [-1, 1] based on keyword matching."""
    tokens = _tokenize(text)
    if not tokens:
        return 0.0

    score = 0.0
    negate = False
    intensify = 1.0

    for tok in tokens:
        if tok in _NEGATION:
            negate = True
            continue
        if tok in _INTENSIFIERS:
            intensify = 1.5
            continue

        word_score = 0.0
        if tok in _POSITIVE_WORDS or tok in _POSITIVE_EMOJIS:
            word_score = 1.0
        elif tok in _NEGATIVE_WORDS or tok in _NEGATIVE_EMOJIS:
            word_score = -1.0

        if negate:
            word_score = -word_score
            negate = False
        word_score *= intensify
        intensify = 1.0

        score += word_score

    # Check for emoji characters embedded in text
    for ch in text:
        if ch in _POSITIVE_EMOJIS:
            score += 0.5
        elif ch in _NEGATIVE_EMOJIS:
            score -= 0.5

    # Exclamation marks amplify existing sentiment
    excl_count = text.count('!')
    if excl_count and score != 0:
        score *= 1 + 0.1 * min(excl_count, 3)

    # ALL-CAPS words amplify sentiment
    caps_words = sum(1 for w in text.split() if w.isupper() and len(w) > 1)
    if caps_words and score != 0:
        score *= 1 + 0.1 * min(caps_words, 3)

    # Normalize to [-1, 1]
    if abs(score) > 1:
        score = max(-1.0, min(1.0, score / max(len(tokens) * 0.3, 1)))

    return score


def _detailed_emotion(text: str) -> str | None:
    """Return a detailed emotion label if one clearly dominates."""
    tokens = set(_tokenize(text)) | set(text)  # include emoji chars
    counts = {
        "happy":   len(tokens & _HAPPY_WORDS),
        "excited": len(tokens & _EXCITED_WORDS),
        "loving":  len(tokens & _LOVING_WORDS),
        "angry":   len(tokens & _ANGRY_WORDS),
        "sad":     len(tokens & _SAD_WORDS),
    }
    best = max(counts, key=counts.get)
    if counts[best] == 0:
        return None
    return best


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# Sentiment result container
class SentimentResult:
    """Holds the sentiment analysis outcome for a single message."""

    EMOJI_MAP = {
        "positive": "😊",
        "neutral":  "😐",
        "negative": "😡",
    }

    DETAIL_EMOJI_MAP = {
        "happy":   "😄",
        "excited": "🥳",
        "loving":  "❤️",
        "angry":   "😡",
        "sad":     "😢",
    }

    COLOR_MAP = {
        "positive": "#22c55e",   # green
        "neutral":  "#a3a3a3",   # gray
        "negative": "#ef4444",   # red
    }

    def __init__(self, label: str, polarity: float, detail: str | None = None):
        self.label = label            # "positive", "neutral", "negative"
        self.polarity = polarity      # raw score in [-1, 1]
        self.detail = detail          # optional: "happy", "excited", etc.

    @property
    def emoji(self) -> str:
        if self.detail and self.detail in self.DETAIL_EMOJI_MAP:
            return self.DETAIL_EMOJI_MAP[self.detail]
        return self.EMOJI_MAP.get(self.label, "😐")

    @property
    def color(self) -> str:
        return self.COLOR_MAP.get(self.label, "#a3a3a3")

    @property
    def tag_text(self) -> str:
        """Human-readable tag like '😊 Positive' or '😄 Happy'."""
        if self.detail:
            return f"{self.emoji} {self.detail.capitalize()}"
        return f"{self.emoji} {self.label.capitalize()}"

    def __repr__(self) -> str:
        return (f"SentimentResult(label={self.label!r}, polarity={self.polarity:.2f}, "
                f"detail={self.detail!r})")


def analyze(text: str) -> SentimentResult:
    """
    Analyze the sentiment of *text* and return a SentimentResult.

    Uses TextBlob if available; otherwise falls back to the built-in
    keyword scorer.  The detailed emotion is always derived from
    keyword matching.
    """
    if not text or not text.strip():
        return SentimentResult("neutral", 0.0)

    # --- primary polarity ------------------------------------------------
    if _HAS_TEXTBLOB:
        blob = TextBlob(text)
        polarity = blob.sentiment.polarity  # [-1, 1]
    else:
        polarity = _keyword_polarity(text)

    # --- classify --------------------------------------------------------
    if polarity > 0.1:
        label = "positive"
    elif polarity < -0.1:
        label = "negative"
    else:
        label = "neutral"

    # --- detailed emotion ------------------------------------------------
    detail = _detailed_emotion(text)

    return SentimentResult(label, polarity, detail)
