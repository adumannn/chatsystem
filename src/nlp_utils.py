"""
NLP utilities for chat keyword extraction and summary generation.

- /keywords : TF-IDF-based keyword extraction (no external API needed)
- /summary  : LLM-powered summary via the existing Ollama / ai_client setup,
              with an extractive fallback when the LLM is unavailable.
"""

import math
import re
import string
from collections import Counter

from ai_client import ask_llm

# ── Stop-words (common English words to ignore in keyword extraction) ────────
STOP_WORDS = frozenset(
    "i me my myself we our ours ourselves you your yours yourself yourselves "
    "he him his himself she her hers herself it its itself they them their "
    "theirs themselves what which who whom this that these those am is are was "
    "were be been being have has had having do does did doing a an the and but "
    "if or because as until while of at by for with about against between "
    "through during before after above below to from up down in out on off "
    "over under again further then once here there when where why how all any "
    "both each few more most other some such no nor not only own same so than "
    "too very s t can will just don should now d ll m o re ve y ain aren "
    "couldn didn doesn hadn hasn haven isn ma mightn mustn needn shan shouldn "
    "wasn weren won wouldn could would let ok also hi hey yes no yeah "
    "hello thanks thank please sorry oh well get got going go like know "
    "want say said one two just really lol haha right sure thing".split()
)

# ── Helpers ──────────────────────────────────────────────────────────────────

_TIMESTAMP_RE = re.compile(r"^\(\d{2}\.\d{2}\.\d{2},\d{2}:\d{2}\)\s*")
_SENDER_RE = re.compile(r"^[\w\-]+\s*:\s*")


def _clean_message(msg: str) -> str:
    """Strip the server-added timestamp and sender prefix."""
    msg = _TIMESTAMP_RE.sub("", msg)
    msg = _SENDER_RE.sub("", msg)
    return msg.strip()


def _tokenize(text: str) -> list[str]:
    """Lower-case, strip punctuation, split into words, remove stop-words."""
    text = text.lower()
    text = text.translate(str.maketrans("", "", string.punctuation))
    return [w for w in text.split() if w and w not in STOP_WORDS and len(w) > 1]


# ── Keyword extraction (TF-IDF inspired) ────────────────────────────────────

def extract_keywords(messages: list[str], top_n: int = 10) -> list[tuple[str, float]]:
    """
    Return the *top_n* keywords from *messages* ranked by a TF-IDF-like score.

    Each message is treated as a separate "document".
    Returns a list of (word, score) tuples sorted by descending score.
    """
    if not messages:
        return []

    docs = [_tokenize(_clean_message(m)) for m in messages]
    docs = [d for d in docs if d]
    if not docs:
        return []

    num_docs = len(docs)

    # Term frequency across the whole corpus
    tf = Counter()
    for doc in docs:
        tf.update(doc)

    # Document frequency (how many docs contain the word)
    df: dict[str, int] = {}
    for doc in docs:
        for w in set(doc):
            df[w] = df.get(w, 0) + 1

    # TF-IDF score
    scores: dict[str, float] = {}
    for word, freq in tf.items():
        idf = math.log((1 + num_docs) / (1 + df.get(word, 0))) + 1
        scores[word] = freq * idf

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    return ranked[:top_n]


def _extract_bigrams(messages: list[str], top_n: int = 5) -> list[tuple[str, int]]:
    """Extract top bigrams (two-word phrases) from messages."""
    bigram_counter: Counter = Counter()
    for msg in messages:
        tokens = _tokenize(_clean_message(msg))
        for i in range(len(tokens) - 1):
            bigram = tokens[i] + " " + tokens[i + 1]
            bigram_counter[bigram] += 1
    # Only keep bigrams that appear more than once
    return [(bg, c) for bg, c in bigram_counter.most_common(top_n) if c > 1]


def format_keywords(messages: list[str], top_n: int = 10) -> str:
    """Human-readable keyword output with visual bar chart."""
    kws = extract_keywords(messages, top_n)
    if not kws:
        return "No keywords found (not enough chat history)."

    # Build a visual bar chart
    max_score = kws[0][1] if kws else 1
    bar_width = 12

    lines = ["🔑 Key topics / frequently mentioned words:"]
    lines.append("─" * 44)
    for rank, (word, score) in enumerate(kws, 1):
        bar_len = max(1, int(bar_width * score / max_score))
        bar = "█" * bar_len + "░" * (bar_width - bar_len)
        lines.append(f"  {rank:>2}. {word:<15} {bar}  ({score:.1f})")

    # Add bigrams if available
    bigrams = _extract_bigrams(messages)
    if bigrams:
        lines.append("")
        lines.append("📌 Frequent phrases:")
        for phrase, count in bigrams:
            lines.append(f"     • \"{phrase}\" (×{count})")

    # Total stats
    all_tokens = []
    for msg in messages:
        all_tokens.extend(_tokenize(_clean_message(msg)))
    lines.append("")
    lines.append(f"📊 Analyzed {len(messages)} messages, "
                 f"{len(all_tokens)} meaningful words")

    return "\n".join(lines)


# ── Summary generation (LLM-powered with extractive fallback) ───────────────

_MAX_MESSAGES_FOR_SUMMARY = 50  # cap to avoid overly large prompts


def _extractive_summary(messages: list[str], num_sentences: int = 5) -> str:
    """
    Simple extractive summary fallback when the LLM is unavailable.
    Picks the most "important" messages based on word diversity and length.
    """
    cleaned = []
    for m in messages:
        c = _clean_message(m)
        if c and len(c) > 10:  # skip very short messages like "ok", "yes"
            cleaned.append(c)

    if not cleaned:
        return "No meaningful chat content to summarise."

    # Score each message by number of unique non-stopword tokens
    scored: list[tuple[float, str]] = []
    for msg in cleaned:
        tokens = _tokenize(msg)
        unique = set(tokens)
        # Prefer longer, more informative messages
        score = len(unique) * 1.5 + len(tokens) * 0.5
        scored.append((score, msg))

    # Take top sentences by score, but present in original order
    scored.sort(key=lambda x: -x[0])
    top_msgs = set(msg for _, msg in scored[:num_sentences])

    # Reconstruct in original order
    ordered = [m for m in cleaned if m in top_msgs]

    # Build the summary
    header = f"📝 Extractive summary ({len(messages)} messages analyzed):"
    body = "\n".join(f"  • {m}" for m in ordered[:num_sentences])
    return header + "\n" + body


def generate_summary(messages: list[str]) -> str:
    """
    Send recent chat messages to the LLM and ask for a brief summary.
    Falls back to an extractive summary if the LLM is unavailable.
    """
    if not messages:
        return "No chat history to summarise."

    recent = messages[-_MAX_MESSAGES_FOR_SUMMARY:]
    chat_block = "\n".join(_clean_message(m) for m in recent if _clean_message(m))

    if not chat_block.strip():
        return "No meaningful chat content to summarise."

    prompt = (
        "Below is a transcript of recent chat messages between users. "
        "Write a brief, concise summary (3-5 sentences) capturing the main "
        "topics and key points discussed. Do NOT include any greetings or "
        "filler — only the summary.\n\n"
        "--- CHAT TRANSCRIPT ---\n"
        f"{chat_block}\n"
        "--- END ---\n\n"
        "Summary:"
    )

    try:
        result = ask_llm(prompt).strip()
        if result:
            return "🤖 AI Summary:\n" + result
        raise ValueError("Empty LLM response")
    except Exception:
        # Graceful fallback to extractive summary
        return _extractive_summary(recent)
