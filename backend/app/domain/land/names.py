"""Matching people's names across scripts and spellings.

The same man is ``முத்துசாமி`` on the patta, ``Muthusamy`` on the sale deed, ``Muthuswamy K.``
on the bank passbook and ``K. Muthuchami s/o Ramasamy`` on the EC. Plain string comparison
calls those four people; this module calls them one, and says why.

1. **Script to Latin.** All the major Indic scripts (Devanagari, Bengali, Gurmukhi, Gujarati,
   Odia, Tamil, Telugu, Kannada, Malayalam) share one Unicode layout: the same letter sits at
   the same offset inside each block. One offset table transliterates all of them.
2. **Phonetic key.** Romanisations that differ only in aspiration (``th``/``t``), vowel length
   (``aa``/``a``), the ``sw``/``s``/``ch`` alternation of Tamil names, ``zh``/``l``, ``w``/``v``,
   ``x``/``ks`` and doubled letters reduce to one key.
3. **Token logic.** Honorifics and relation markers (``s/o``, ``w/o``, ``late``, ``thiru``) are
   dropped; single-letter initials must be compatible but never prove a match on their own.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Offsets inside an Indic Unicode block → Latin. Unassigned offsets in a given script are simply absent there.
_VOWELS = {0x05: "a", 0x06: "aa", 0x07: "i", 0x08: "ii", 0x09: "u", 0x0A: "uu", 0x0B: "ri", 0x0E: "e", 0x0F: "e", 0x10: "ai", 0x12: "o", 0x13: "o", 0x14: "au"}
_CONSONANTS = {
    0x15: "k", 0x16: "kh", 0x17: "g", 0x18: "gh", 0x19: "ng", 0x1A: "ch", 0x1B: "chh", 0x1C: "j", 0x1D: "jh", 0x1E: "nj",
    0x1F: "t", 0x20: "th", 0x21: "d", 0x22: "dh", 0x23: "n", 0x24: "t", 0x25: "th", 0x26: "d", 0x27: "dh", 0x28: "n",
    0x29: "n", 0x2A: "p", 0x2B: "ph", 0x2C: "b", 0x2D: "bh", 0x2E: "m", 0x2F: "y", 0x30: "r", 0x31: "r", 0x32: "l",
    0x33: "l", 0x34: "zh", 0x35: "v", 0x36: "sh", 0x37: "sh", 0x38: "s", 0x39: "h",
}
_SIGNS = {0x3E: "aa", 0x3F: "i", 0x40: "ii", 0x41: "u", 0x42: "uu", 0x43: "ri", 0x46: "e", 0x47: "e", 0x48: "ai", 0x4A: "o", 0x4B: "o", 0x4C: "au"}
_VIRAMA, _ANUSVARA, _VISARGA, _NUKTA = 0x4D, 0x02, 0x03, 0x3C
# Block starts; scripts that delete the final inherent vowel when spoken (schwa deletion).
_BLOCKS = {0x0900: True, 0x0980: True, 0x0A00: True, 0x0A80: True, 0x0B00: False, 0x0B80: False, 0x0C00: False, 0x0C80: False, 0x0D00: False}


def _block(ch: str) -> int | None:
    cp = ord(ch)
    base = cp & ~0x7F
    return base if base in _BLOCKS else None


def transliterate(text: str) -> str:
    """Indic script → plain Latin (lossy by design; good enough to compare names)."""
    out: list[str] = []
    pending = False  # a consonant waiting for its vowel
    schwa_drop = False
    for ch in text:
        base = _block(ch)
        if base is None:
            if pending:
                if not schwa_drop:
                    out.append("a")
                pending = False
            out.append(ch)
            continue
        off = ord(ch) - base
        schwa_drop = _BLOCKS[base]
        if off in _CONSONANTS:
            if pending:
                out.append("a")
            out.append(_CONSONANTS[off])
            pending = True
        elif off in _SIGNS:
            out.append(_SIGNS[off])
            pending = False
        elif off == _VIRAMA:
            pending = False
        elif off in _VOWELS:
            if pending:
                out.append("a")
                pending = False
            out.append(_VOWELS[off])
        elif off == _ANUSVARA:
            if pending:
                out.append("a")
                pending = False
            out.append("m")
        elif off == _VISARGA:
            out.append("h")
        elif off == _NUKTA:
            continue
    if pending and not schwa_drop:
        out.append("a")
    return "".join(out)


_DROP = {
    "sri", "shri", "shree", "smt", "srimathi", "srimati", "thiru", "thiruvalar", "tmt", "thirumathi",
    "mr", "mrs", "ms", "dr", "late", "lt", "alias", "urf", "deceased", "minor", "guardian",
    "s/o", "d/o", "w/o", "h/o", "c/o", "son", "daughter", "wife", "husband", "of",
}
_RELATION = re.compile(r"\b(?:s|d|w|h|c)\s*/\s*o\b|\bson of\b|\bdaughter of\b|\bwife of\b|\bhusband of\b|@|\balias\b|\burf\b", re.I)


def own_name(text: str) -> str:
    """The person's own name, without ``s/o …`` and the father's or husband's name after it."""
    return _RELATION.split(text or "", maxsplit=1)[0].strip(" ,.;-")


_RULES: list[tuple[str, str]] = [
    (r"zh", "l"), (r"x", "ks"), (r"q", "k"), (r"w", "v"), (r"ph", "p"), (r"f", "p"),
    (r"ksh", "ks"), (r"tch", "ks"), (r"sw(?=a)", "s"), (r"chh", "s"), (r"ch", "s"), (r"sh", "s"),
    (r"([kgtdpbj])h", r"\1"),
    # Tamil does not distinguish voiced and voiceless stops in writing (க = k/g, ட = t/d, ப = p/b).
    (r"g", "k"), (r"d", "t"), (r"b", "p"), (r"j", "s"), (r"aa+", "a"), (r"ee+", "i"), (r"ii+", "i"), (r"oo+", "u"), (r"uu+", "u"),
    (r"ou", "u"), (r"y$", "i"), (r"([a-z])\1+", r"\1"), (r"(?<=[a-z])h$", ""),
]


def phonetic(token: str) -> str:
    t = unicodedata.normalize("NFKD", token.lower())
    t = "".join(c for c in t if c.isascii() and c.isalpha())
    for pattern, repl in _RULES:
        t = re.sub(pattern, repl, t)
    return t


def tokens(name: str) -> tuple[list[str], list[str]]:
    """(full tokens as phonetic keys, initials)."""
    latin = transliterate(own_name(name))
    raw = [t for t in re.split(r"[\s.,;:'\-()]+", latin.lower()) if t]
    full, initials = [], []
    for t in raw:
        if t in _DROP:
            continue
        if len(t) == 1:
            initials.append(t[0])
        else:
            key = phonetic(t)
            if key:
                full.append(key)
    return full, initials


def name_key(name: str) -> str:
    full, _ = tokens(name)
    return " ".join(sorted(full))


def similarity(a: str, b: str) -> tuple[float, str]:
    """A 0..1 score and the reason for it."""
    fa, ia = tokens(a)
    fb, ib = tokens(b)
    if not fa or not fb:
        return 0.0, "no comparable name"
    if sorted(fa) == sorted(fb):
        return 1.0, "same name after transliteration and spelling normalisation"
    # Best one-to-one token alignment.
    short, long_ = (fa, fb) if len(fa) <= len(fb) else (fb, fa)
    used: set[int] = set()
    scores = []
    for t in short:
        best, best_j = 0.0, -1
        for j, u in enumerate(long_):
            if j in used:
                continue
            r = SequenceMatcher(None, t, u).ratio()
            if r > best:
                best, best_j = r, j
        if best_j >= 0:
            used.add(best_j)
        scores.append(best)
    core = sum(scores) / len(scores)
    # Unmatched long tokens may be initials spelled out ("Kandasamy Muthusamy" vs "K. Muthusamy").
    leftovers = [long_[j] for j in range(len(long_)) if j not in used]
    initials_short = ia if short is fa else ib
    initials_long = ib if short is fa else ia
    explained = all(any(rest.startswith(i) for i in initials_short) for rest in leftovers) if leftovers else True
    if initials_short and initials_long and not set(initials_short) & set(initials_long) and not leftovers:
        core *= 0.85  # different initials (different fathers/villages) weaken the match
    if leftovers and not explained:
        core *= 0.9
    reason = "spellings differ only in transliteration" if core >= 0.88 else "partly similar names" if core >= 0.7 else "different names"
    return round(core, 3), reason


def same_person(a: str, b: str, threshold: float = 0.86) -> bool:
    return similarity(a, b)[0] >= threshold
