"""Configurable, meaning-preserving text normalization.

Rules (see docs/ARCHITECTURE.md#normalization):
  * Unicode NFKC, but only after protecting characters whose compatibility mapping can change meaning
    (we keep digits, signs and letters; ligatures/nbsp/fullwidth forms are folded).
  * Whitespace collapsed; typographic quotes/dashes folded to ASCII.
  * Line-break hyphenation removed only when "xxx-\\nyyy" has a lowercase continuation and the prefix is
    not a common compound prefix (non-, self-, ...), so "non-\\ncompliance" keeps its hyphen.
  * Numbers, dates, %, currency, negations and legal references are NEVER altered by normalization.
  * Case and punctuation are preserved in `normalize`; they are only dropped in `match_key`, which is used
    to *find* candidates, never to declare two passages equal (unless ignore_punctuation_case=True).
"""
import re
import unicodedata
from dataclasses import dataclass

COMPOUND_PREFIXES = {"non", "self", "anti", "cross", "semi", "multi", "well", "inter", "co", "pre", "sub", "re"}
_QUOTES = {"‘": "'", "’": "'", "‚": "'", "“": '"', "”": '"', "„": '"'}
_DASHES = {"‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "−": "-"}
_NUMBERING = re.compile(
    r"""^\s*(
        (?:section|article|clause|chapter|annex|annexure|appendix|schedule|part|rule|regulation)\s+[0-9IVXLCivxlc]+(?:\.[0-9]+)*[.):]?
      | \(?[0-9]{1,3}(?:\.[0-9]{1,3}){0,5}[.)]?
      | \(?[A-Za-z]{1,2}[.)]
      | \((?:[ivxlc]{1,6}|[a-z]|[0-9]{1,3})\)
    )\s+""",
    re.IGNORECASE | re.VERBOSE,
)


@dataclass(frozen=True)
class NormalizationConfig:
    dehyphenate: bool = True
    fold_quotes_dashes: bool = True
    ignore_punctuation_case: bool = False  # if True, case/punctuation-only edits count as UNCHANGED
    renumbering_is_change: bool = False  # if True, number-only edits are MODIFIED instead of UNCHANGED


DEFAULT = NormalizationConfig()


def dehyphenate(text: str) -> str:
    def repl(m: re.Match) -> str:
        left, right = m.group(1), m.group(2)
        word = re.search(r"([A-Za-z]+)$", left)
        if word and word.group(1).lower() in COMPOUND_PREFIXES:
            return f"{left}-{right}"
        return f"{left}{right}"

    return re.sub(r"([A-Za-z]{2,})-\s*\n\s*([a-z]+)", repl, text)


def normalize(text: str, cfg: NormalizationConfig = DEFAULT) -> str:
    if cfg.dehyphenate:
        text = dehyphenate(text)
    text = unicodedata.normalize("NFKC", text)
    if cfg.fold_quotes_dashes:
        text = text.translate(str.maketrans({**_QUOTES, **_DASHES}))
    text = re.sub(r"[­​-‍﻿]", "", text)  # soft hyphen, zero-width chars
    text = re.sub(r"\s+", " ", text).strip()
    return text


def split_numbering(text: str) -> tuple[str | None, str]:
    """Split a leading clause number ("4.2", "(a)", "Section 5.") from the body."""
    m = _NUMBERING.match(text)
    if m and len(text[m.end():].strip()) > 0:
        return m.group(1).strip(), text[m.end():].strip()
    return None, text.strip()


def match_key(text: str) -> str:
    """Aggressive key for candidate search/ignore-punctuation mode. Digits and negations survive."""
    t = normalize(text).casefold()
    t = re.sub(r"[^\w\s%.$€£₹/-]", " ", t)  # drop punctuation but keep numeric punctuation
    t = re.sub(r"(?<!\d)[.\-/$€£₹%](?!\d)", " ", t)
    return re.sub(r"\s+", " ", t).strip()


# --- critical token extraction (numbers, dates, money, percentages, negations, legal refs) -----------------
_MONTH = r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*"
CRITICAL_PATTERNS = {
    "date": re.compile(
        rf"\b\d{{1,2}}[/-]\d{{1,2}}[/-]\d{{2,4}}\b|\b\d{{4}}-\d{{2}}-\d{{2}}\b|\b\d{{1,2}}(?:st|nd|rd|th)?\s+{_MONTH}\.?,?\s+\d{{4}}\b|\b{_MONTH}\.?\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b",
        re.I,
    ),
    "percentage": re.compile(r"\b\d+(?:[.,]\d+)?\s?%|\b\d+(?:\.\d+)?\s?per\s?cent\b", re.I),
    "amount": re.compile(r"(?:[$€£₹]|\b(?:rs\.?|inr|usd|eur)\s?)\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:lakh|crore|million|billion|thousand|k|m|bn))?", re.I),
    "number": re.compile(
        r"(?<![\w.])\d[\d,]*(?:\.\d+)?(?![\w])|\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|sixty|ninety|hundred|thousand|lakh|crore|million|billion)\b",
        re.I,
    ),
    "identifier": re.compile(r"\b[A-Za-z]{2,}[-/]?\d+[A-Za-z0-9-]*\b"),  # ABC123, KYC-7, FATF40
    "legal_ref": re.compile(r"\b(?:section|article|clause|rule|regulation|para(?:graph)?|annex(?:ure)?|schedule)\s+\d+(?:\.\d+)*(?:\([a-z0-9]+\))*", re.I),
}
NEGATION = re.compile(r"\b(?:not|no|never|neither|nor|without|cannot|can't|shall not|must not|may not|prohibited|exempt(?:ed)?|except|unless)\b", re.I)
MODALS = re.compile(r"\b(?:shall|must|should|may|might|can|will|is required to|are required to|need(?:s)? to)\b", re.I)


def critical_tokens(text: str) -> dict[str, list[str]]:
    t = normalize(text)
    out: dict[str, list[str]] = {}
    covered = []
    for name in ("date", "percentage", "amount", "legal_ref", "identifier", "number"):
        found = []
        for m in CRITICAL_PATTERNS[name].finditer(t):
            if any(s <= m.start() < e for s, e in covered) and name == "number":
                continue
            covered.append((m.start(), m.end()))
            found.append(re.sub(r"\s+", " ", m.group(0).strip().lower()))
        out[name] = found
    out["negation"] = [m.group(0).lower() for m in NEGATION.finditer(t)]
    out["modal"] = [m.group(0).lower() for m in MODALS.finditer(t)]
    return out


def critical_flags(old: str, new: str) -> list[str]:
    """Flags describing high-risk differences between two passages. Purely lexical."""
    a, b = critical_tokens(old), critical_tokens(new)
    flags = []
    for kind, flag in (
        ("date", "date_changed"),
        ("percentage", "percentage_changed"),
        ("amount", "amount_changed"),
        ("number", "number_changed"),
        ("legal_ref", "legal_reference_changed"),
        ("identifier", "identifier_changed"),
        ("negation", "negation_changed"),
        ("modal", "obligation_strength_changed"),
    ):
        if sorted(a[kind]) != sorted(b[kind]):
            flags.append(flag)
    return flags
