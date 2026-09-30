"""Privacy layer.

Everything here runs 100% on-device. It does three jobs:
1. Redact personal data (PII) from any text before it can leave the PC.
2. Score how sensitive the current context is (question + active window title).
3. Decide whether a request may use the cloud, and whether the screenshot may go with it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

# ---------------------------------------------------------------- PII patterns
# Order matters: more specific patterns first.
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("EMAIL", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("API_KEY", re.compile(r"\b(?:sk|pk|rk|AIza|ghp|gho|xox[bpas])[-_A-Za-z0-9]{16,}\b")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("AADHAAR", re.compile(r"\b[2-9]\d{3}[ -]?\d{4}[ -]?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("IFSC", re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b")),
    ("UPI", re.compile(r"\b[\w.-]{2,}@(?:ok\w+|ybl|ibl|axl|paytm|upi|apl|icici|sbi|hdfcbank)\b", re.I)),
    ("PHONE", re.compile(r"(?<!\d)(?:\+91[ -]?|0)?[6-9]\d{4}[ -]?\d{5}(?!\d)")),
    ("IP", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")),
    ("PASSWORD", re.compile(r"(?i)\b(pass(?:word|wd)?|pwd|pin|otp)\b\s*[:=]?\s*\S+")),
]


def _luhn_ok(digits: str) -> bool:
    d = [int(c) for c in digits if c.isdigit()]
    if not 13 <= len(d) <= 19:
        return False
    total = 0
    for i, n in enumerate(reversed(d)):
        if i % 2 == 1:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


@dataclass
class RedactionResult:
    text: str
    found: dict = field(default_factory=dict)  # label -> count

    @property
    def count(self) -> int:
        return sum(self.found.values())


def redact(text: str) -> RedactionResult:
    """Replace PII with typed placeholders like [EMAIL_1]. Deterministic, local, fast."""
    found: dict = {}
    out = text
    for label, pat in _PATTERNS:
        def repl(m: re.Match, label=label) -> str:
            s = m.group(0)
            if label == "CARD" and not _luhn_ok(s):
                return s  # not a real card number, leave it
            found[label] = found.get(label, 0) + 1
            return f"[{label}_{found[label]}]"

        out = pat.sub(repl, out)
    return RedactionResult(out, found)


# ------------------------------------------------------- sensitivity scoring
SENSITIVE_WINDOW_WORDS = [
    "bank", "netbanking", "sbi", "hdfc", "icici", "axis", "kotak", "paytm", "phonepe", "gpay",
    "upi", "wallet", "1password", "bitwarden", "keepass", "lastpass", "password",
    "whatsapp", "telegram", "signal", "outlook", "gmail", "mail", "inbox",
    "hospital", "health", "medical", "prescription", "lab report", "insurance",
    "aadhaar", "digilocker", "income tax", "itr", "salary", "payslip", "invoice",
    "incognito", "inprivate", "private browsing",
]
SENSITIVE_QUERY_WORDS = [
    "my password", "my pin", "otp", "cvv", "bank account", "account number", "salary",
    "medical", "diagnosis", "prescription", "aadhaar", "pan card", "passport",
    "confidential", "private", "personal", "secret", "nda",
]


@dataclass
class PrivacyDecision:
    allow_cloud: bool
    allow_screenshot_to_cloud: bool
    sensitivity: int  # 0-100
    reasons: list = field(default_factory=list)


def assess(question: str, window_title: str = "", redaction: RedactionResult | None = None,
           threshold: int = 40) -> PrivacyDecision:
    q, w = question.lower(), window_title.lower()
    score, reasons = 0, []
    for word in SENSITIVE_WINDOW_WORDS:
        if word in w:
            score += 50
            reasons.append(f"active window looks sensitive ('{word}')")
            break
    for word in SENSITIVE_QUERY_WORDS:
        if word in q:
            score += 40
            reasons.append(f"question mentions '{word}'")
            break
    if redaction and redaction.count:
        score += min(40, 15 * redaction.count)
        reasons.append(f"{redaction.count} personal detail(s) found in text")
    score = min(score, 100)
    return PrivacyDecision(
        allow_cloud=score < threshold,
        # Screens can't be text-redacted, so be stricter with images.
        allow_screenshot_to_cloud=score < threshold // 2,
        sensitivity=score,
        reasons=reasons,
    )
