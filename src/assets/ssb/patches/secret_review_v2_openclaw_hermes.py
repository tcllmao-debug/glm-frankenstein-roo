#!/usr/bin/env python3
"""
SSB V11 Z MARK — SECRET REVIEW V2: AI REVIEW + OPENCLAW + HERMES BEAST CLAW
=============================================================================

Every secret node found gets:
  1. Full AI review (Shannon's entropy with proof, structure verification, context analysis)
  2. Sent to OpenClaw for independent review
  3. Sent to Hermes Beast Claw for adversarial review
  4. All three reviews are combined into a final classification

Detects:
  - AWS keys (AKIA format)
  - GitHub tokens (ghp_ format)
  - SSH private keys (RSA, ECDSA, Ed25519, DSA)
  - Hex keys (32, 40, 64, 128 char hex strings — MD5, SHA1, SHA256, SHA512)
  - JWT tokens
  - Generic API keys
  - Database URLs with credentials
  - Bearer tokens
  - Private keys (PEM blocks)
  - Password assignments

Each review includes:
  - Shannon's entropy with full mathematical computation
  - Character frequency distribution
  - Structural format verification
  - Context analysis (env var refs, placeholders, test files)
  - Confidence score with reasoning

OpenClaw and Hermes Beast Claw are review channels that provide independent
perspectives. In solo mode (no external services), they run as virtual
reviewers with different analysis strategies:
  - OpenClaw: Conservative reviewer — leans toward "real secret" unless strong evidence otherwise
  - Hermes Beast Claw: Aggressive reviewer — actively tries to prove secrets are false positives
"""

from __future__ import annotations
import json, time, threading, hashlib, math, re, os
from collections import Counter, deque, defaultdict
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


# ═══════════════════════════════════════════════════════════════════════════
# ENTROPY ANALYZER — Shannon's entropy with full mathematical proof
# ═══════════════════════════════════════════════════════════════════════════

class EntropyAnalyzer:
    """Computes Shannon's entropy with full mathematical proof for each secret."""

    @staticmethod
    def shannon_entropy(value: str) -> dict:
        """Compute Shannon entropy with full mathematical breakdown.

        H(X) = -Σ p(x) * log2(p(x)) for all x in X

        Returns the entropy value AND the full mathematical proof.
        """
        if not value:
            return {"entropy": 0.0, "proof": "Empty string — no entropy", "distribution": {}}

        n = len(value)
        freq = Counter(value)
        distribution = {}
        entropy = 0.0
        proof_terms = []

        for char, count in sorted(freq.items(), key=lambda x: -x[1]):
            p = count / n
            distribution[char] = {"count": count, "probability": p}

            if p > 0:
                log_term = math.log2(p)
                contribution = -p * log_term
                entropy += contribution
                proof_terms.append(
                    f"  P('{char}') = {count}/{n} = {p:.4f} → "
                    f"-({p:.4f}) × log₂({p:.4f}) = "
                    f"-({p:.4f}) × ({log_term:.4f}) = {contribution:.4f}"
                )

        # Build the mathematical proof
        proof = f"Shannon Entropy Proof for: {value[:40]}{'...' if len(value) > 40 else ''}\n"
        proof += f"  Length: {n} characters\n"
        proof += f"  Unique characters: {len(freq)}\n"
        proof += f"  Formula: H(X) = -Σ p(x) × log₂(p(x))\n\n"
        proof += "  Computation:\n"
        proof += "\n".join(proof_terms[:20])  # Top 20 terms
        if len(proof_terms) > 20:
            proof += f"\n  ... and {len(proof_terms) - 20} more terms\n"
        proof += f"\n\n  H(X) = {entropy:.6f} bits/character\n"
        proof += f"  Maximum possible: {math.log2(len(freq)):.6f} bits/character (uniform distribution)\n"
        proof += f"  Efficiency: {entropy / math.log2(len(freq)) * 100:.1f}% of maximum\n" if len(freq) > 1 else ""
        proof += f"\n  Interpretation: "
        if entropy >= 4.5:
            proof += "Very high entropy — consistent with cryptographic material or random data.\n"
            proof += "  This is the entropy level expected for real API keys, tokens, and encrypted data."
        elif entropy >= 3.5:
            proof += "High entropy — consistent with real secrets, passwords, or encoded data.\n"
            proof += "  Real secrets typically have entropy > 3.0 bits/character."
        elif entropy >= 2.5:
            proof += "Moderate entropy — could be a weak password or structured key.\n"
            proof += "  May be a real secret with low complexity, or a structured identifier."
        else:
            proof += "Low entropy — unlikely to be a real cryptographic secret.\n"
            proof += "  Likely a placeholder, example value, or human-readable string."

        return {
            "entropy": round(entropy, 6),
            "max_possible": round(math.log2(len(freq)), 6) if len(freq) > 1 else 0,
            "efficiency": round(entropy / math.log2(len(freq)) * 100, 1) if len(freq) > 1 else 0,
            "unique_chars": len(freq),
            "length": n,
            "distribution": {k: v for k, v in distribution.items()},
            "proof": proof,
        }

    @staticmethod
    def relative_entropy(value: str) -> dict:
        """Compute Kullback-Leibler divergence from uniform distribution.
        Tells us how far from random the value is."""
        if not value or len(value) < 2:
            return {"kl_divergence": 0.0, "interpretation": "insufficient data"}

        n = len(value)
        freq = Counter(value)
        unique = len(freq)
        uniform_p = 1.0 / unique
        kl = 0.0

        for char, count in freq.items():
            p = count / n
            if p > 0 and uniform_p > 0:
                kl += p * math.log2(p / uniform_p)

        return {
            "kl_divergence": round(kl, 6),
            "interpretation": "uniform random" if kl < 0.1 else "structured" if kl < 0.5 else "highly structured",
        }


# ═══════════════════════════════════════════════════════════════════════════
# SECRET DETECTION PATTERNS — including SSH keys and hex keys
# ═══════════════════════════════════════════════════════════════════════════

class SecretPatterns:
    """All secret detection patterns including SSH keys and hex keys."""

    PATTERNS = {
        'aws_access_key': {
            'pattern': r'AKIA[0-9A-Z]{16}',
            'description': 'AWS Access Key ID',
            'confidence_base': 0.95,
            'min_entropy': 3.5,
        },
        'aws_secret_key': {
            'pattern': r'aws_secret_access_key\s*[=:]\s*["\']?([A-Za-z0-9/+=]{40})["\']?',
            'description': 'AWS Secret Access Key (40 chars base64)',
            'confidence_base': 0.9,
            'min_entropy': 4.0,
        },
        'github_token': {
            'pattern': r'ghp_[A-Za-z0-9]{36}',
            'description': 'GitHub Personal Access Token',
            'confidence_base': 0.95,
            'min_entropy': 4.0,
        },
        'github_oauth': {
            'pattern': r'gho_[A-Za-z0-9]{36}',
            'description': 'GitHub OAuth Token',
            'confidence_base': 0.95,
            'min_entropy': 4.0,
        },
        'github_app': {
            'pattern': r'ghs_[A-Za-z0-9]{36}',
            'description': 'GitHub App Token',
            'confidence_base': 0.95,
            'min_entropy': 4.0,
        },
        'github_refresh': {
            'pattern': r'ghr_[A-Za-z0-9]{36}',
            'description': 'GitHub Refresh Token',
            'confidence_base': 0.95,
            'min_entropy': 4.0,
        },
        'ssh_rsa_private': {
            'pattern': r'-----BEGIN RSA PRIVATE KEY-----',
            'description': 'SSH RSA Private Key',
            'confidence_base': 0.99,
            'min_entropy': 5.0,
        },
        'ssh_ecdsa_private': {
            'pattern': r'-----BEGIN EC PRIVATE KEY-----',
            'description': 'SSH ECDSA Private Key',
            'confidence_base': 0.99,
            'min_entropy': 5.0,
        },
        'ssh_ed25519_private': {
            'pattern': r'-----BEGIN OPENSSH PRIVATE KEY-----',
            'description': 'SSH Ed25519 Private Key (OpenSSH)',
            'confidence_base': 0.99,
            'min_entropy': 5.0,
        },
        'ssh_dsa_private': {
            'pattern': r'-----BEGIN DSA PRIVATE KEY-----',
            'description': 'SSH DSA Private Key',
            'confidence_base': 0.99,
            'min_entropy': 5.0,
        },
        'ssh_private_generic': {
            'pattern': r'-----BEGIN (?:PGP |ENCRYPTED )?PRIVATE KEY-----',
            'description': 'Private Key (generic PEM)',
            'confidence_base': 0.98,
            'min_entropy': 5.0,
        },
        'ssh_public_key': {
            'pattern': r'ssh-(?:rsa|ed25519|ecdsa|dss)\s+[A-Za-z0-9+/=]+',
            'description': 'SSH Public Key',
            'confidence_base': 0.3,  # Public keys aren't secrets
            'min_entropy': 4.0,
        },
        'hex_key_32': {
            'pattern': r'\b[0-9a-fA-F]{32}\b',
            'description': '32-character hex string (MD5 hash or API key)',
            'confidence_base': 0.6,
            'min_entropy': 3.5,
        },
        'hex_key_40': {
            'pattern': r'\b[0-9a-fA-F]{40}\b',
            'description': '40-character hex string (SHA1 hash or Git SHA)',
            'confidence_base': 0.55,
            'min_entropy': 3.5,
        },
        'hex_key_64': {
            'pattern': r'\b[0-9a-fA-F]{64}\b',
            'description': '64-character hex string (SHA256 hash or private key)',
            'confidence_base': 0.7,
            'min_entropy': 4.0,
        },
        'hex_key_128': {
            'pattern': r'\b[0-9a-fA-F]{128}\b',
            'description': '128-character hex string (SHA512 hash or cryptographic key)',
            'confidence_base': 0.8,
            'min_entropy': 4.5,
        },
        'jwt_token': {
            'pattern': r'eyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}',
            'description': 'JSON Web Token',
            'confidence_base': 0.85,
            'min_entropy': 4.0,
        },
        'generic_api_key': {
            'pattern': r'(?i)(?:api[_-]?key|apikey|api[_-]?secret)\s*[=:]\s*["\']?([A-Za-z0-9_-]{20,})["\']?',
            'description': 'Generic API Key',
            'confidence_base': 0.7,
            'min_entropy': 3.5,
        },
        'password_assignment': {
            'pattern': r'(?i)(?:password|passwd|pwd)\s*[=:]\s*["\']([^"\']{6,})["\']',
            'description': 'Password assignment',
            'confidence_base': 0.6,
            'min_entropy': 3.0,
        },
        'database_url': {
            'pattern': r'(?:postgres|mysql|mongodb|redis)://[^\s"\'<>]+:[^\s"\'<>]+@',
            'description': 'Database connection string with credentials',
            'confidence_base': 0.85,
            'min_entropy': 3.5,
        },
        'bearer_token': {
            'pattern': r'(?i)bearer\s+([A-Za-z0-9_-]{20,})',
            'description': 'Bearer authentication token',
            'confidence_base': 0.75,
            'min_entropy': 3.5,
        },
        'slack_token': {
            'pattern': r'xox[baprs]-[A-Za-z0-9-]{10,}',
            'description': 'Slack Token',
            'confidence_base': 0.9,
            'min_entropy': 3.5,
        },
        'stripe_key': {
            'pattern': r'sk_(?:live|test)_[A-Za-z0-9]{24,}',
            'description': 'Stripe API Key',
            'confidence_base': 0.85,
            'min_entropy': 3.5,
        },
        'google_api_key': {
            'pattern': r'AIza[0-9A-Za-z_-]{35}',
            'description': 'Google API Key',
            'confidence_base': 0.9,
            'min_entropy': 4.0,
        },
    }

    FALSE_POSITIVE_VALUES = [
        'your_api_key_here', 'your_api_key', 'xxxxx', 'test', 'example',
        'placeholder', 'your_key', 'your_secret', 'your_token',
        'changeme', 'password123', 'admin', 'default', 'sample',
        'dummy', 'fake', 'mock', 'stub', 'todo', 'fixme',
        '00000000000000000000000000000000',  # 32 zeros
        'ffffffffffffffffffffffffffffffff',  # 32 f's
        'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',  # 32 a's
        '1234567890abcdef1234567890abcdef',  # sequential
    ]

    ENV_VAR_REFERENCES = [
        'os.environ', 'getenv', 'process.env', 'ENV[',
        '${', 'os.getenv', 'config.get',
    ]

    EXAMPLE_INDICATORS = ['example', 'sample', 'demo', 'test', 'mock', 'fixture']


# ═══════════════════════════════════════════════════════════════════════════
# AI SECRET REVIEWER — full review with entropy proof
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class SecretReview:
    """Complete review of a detected secret."""
    secret_id: str
    value_preview: str  # First 20 chars only — never expose the full secret
    file: str
    line: int
    pattern_matched: str
    pattern_description: str

    # AI Review
    ai_classification: str = ""  # REAL_SECRET, FALSE_POSITIVE, SENSITIVE_CONFIG, POTENTIAL_SECRET
    ai_confidence: float = 0.0
    ai_reasoning: str = ""

    # Entropy analysis
    shannon_entropy: float = 0.0
    entropy_proof: str = ""
    entropy_efficiency: float = 0.0
    kl_divergence: float = 0.0
    unique_chars: int = 0

    # Structure analysis
    structural_match: bool = False
    structural_confidence: float = 0.0

    # Context analysis
    is_env_reference: bool = False
    is_placeholder: bool = False
    is_in_test_file: bool = False
    false_positive_reasons: list = field(default_factory=list)

    # OpenClaw review
    openclaw_classification: str = ""
    openclaw_confidence: float = 0.0
    openclaw_reasoning: str = ""

    # Hermes Beast Claw review
    hermes_classification: str = ""
    hermes_confidence: float = 0.0
    hermes_reasoning: str = ""

    # Final combined classification
    final_classification: str = ""
    final_confidence: float = 0.0
    final_reasoning: str = ""
    correct_node_type: str = ""

    reviewed_at: float = field(default_factory=time.time)


class AISecretReviewer:
    """The AI reviewer — performs full analysis of each secret."""

    def review(self, secret_data: dict) -> SecretReview:
        value = secret_data.get('value', '')
        context = secret_data.get('context', '')
        file_path = secret_data.get('file', '')
        line_num = secret_data.get('line', 0)
        pattern_matched = secret_data.get('pattern_matched', '')

        sid = hashlib.sha256(f"{value}{file_path}{line_num}{time.time()}".encode()).hexdigest()[:16]
        review = SecretReview(
            secret_id=sid,
            value_preview=value[:20] + '...' if len(value) > 20 else value,
            file=file_path, line=line_num,
            pattern_matched=pattern_matched,
            pattern_description=SecretPatterns.PATTERNS.get(pattern_matched, {}).get('description', 'Unknown'),
        )

        # Step 1: Shannon's entropy with full mathematical proof
        entropy_result = EntropyAnalyzer.shannon_entropy(value)
        review.shannon_entropy = entropy_result['entropy']
        review.entropy_proof = entropy_result['proof']
        review.entropy_efficiency = entropy_result.get('efficiency', 0)
        review.unique_chars = entropy_result.get('unique_chars', 0)

        kl_result = EntropyAnalyzer.relative_entropy(value)
        review.kl_divergence = kl_result['kl_divergence']

        # Step 2: Structural verification
        spec = SecretPatterns.PATTERNS.get(pattern_matched, {})
        review.structural_match = bool(re.search(spec.get('pattern', ''), value))
        review.structural_confidence = spec.get('confidence_base', 0.3)

        # Step 3: Context analysis
        review.is_env_reference = self._check_env_reference(context)
        review.is_placeholder = self._check_placeholder(value)
        review.is_in_test_file = self._check_test_file(file_path)
        review.false_positive_reasons = self._get_false_positive_reasons(value, context, file_path)

        # Step 4: AI classification
        review.ai_classification = self._classify(review, spec)
        review.ai_confidence = self._compute_confidence(review, spec)
        review.ai_reasoning = self._generate_reasoning(review)

        # Step 5: Correct node type
        review.correct_node_type = self._get_node_type(review.ai_classification)

        return review

    def _check_env_reference(self, context: str) -> bool:
        c = context.lower()
        return any(ref.lower() in c for ref in SecretPatterns.ENV_VAR_REFERENCES)

    def _check_placeholder(self, value: str) -> bool:
        v = value.lower()
        return any(ph in v for ph in SecretPatterns.FALSE_POSITIVE_VALUES)

    def _check_test_file(self, file_path: str) -> bool:
        f = file_path.lower()
        return any(ind in f for ind in SecretPatterns.EXAMPLE_INDICATORS)

    def _get_false_positive_reasons(self, value: str, context: str, file_path: str) -> list:
        reasons = []
        v = value.lower()
        c = context.lower()
        f = file_path.lower()

        for ph in SecretPatterns.FALSE_POSITIVE_VALUES:
            if ph in v:
                reasons.append(f"Matches known placeholder: '{ph}'")

        if self._check_test_file(file_path):
            reasons.append(f"Found in test/example file: '{file_path}'")

        example_domains = ['example.com', 'localhost', '127.0.0.1', '0.0.0.0']
        for domain in example_domains:
            if domain in v or domain in c:
                reasons.append(f"Contains example/localhost domain: '{domain}'")

        if value.isdigit() and len(value) < 6:
            reasons.append("Short numeric value — likely a port or ID")

        if value in ('True', 'False', 'true', 'false', 'None', 'null', '0', '1'):
            reasons.append("Boolean/null value — not a secret")

        # Check for repeated characters (low entropy indicator)
        if len(set(value)) < 4 and len(value) > 10:
            reasons.append(f"Very low character diversity ({len(set(value))} unique chars in {len(value)} length)")

        # Check for sequential patterns
        if value.lower() in ('0123456789abcdef' * 8)[:len(value)]:
            reasons.append("Sequential hex pattern — not a real key")

        return reasons

    def _classify(self, review: SecretReview, spec: dict) -> str:
        # Strong false positive signals
        if len(review.false_positive_reasons) >= 2:
            return 'FALSE_POSITIVE'
        if review.is_placeholder:
            return 'FALSE_POSITIVE'

        # Public keys aren't secrets
        if 'public' in review.pattern_matched.lower():
            return 'SENSITIVE_CONFIG'

        # Env var references
        if review.is_env_reference and review.structural_confidence < 0.85:
            return 'SENSITIVE_CONFIG'

        # High entropy + high structural confidence = real secret
        min_entropy = spec.get('min_entropy', 3.0)
        if review.structural_confidence >= 0.85 and review.shannon_entropy >= min_entropy:
            return 'REAL_SECRET'

        # SSH keys and private keys are always real if structurally matched
        if any(k in review.pattern_matched for k in ['ssh_', 'private', 'pem']):
            if review.structural_match:
                return 'REAL_SECRET'

        # Hex keys need entropy check
        if 'hex_key' in review.pattern_matched:
            if review.shannon_entropy >= 3.5 and not review.is_placeholder:
                # Check it's not all same character
                if len(set(review.value_preview)) > 5:
                    return 'REAL_SECRET'
                else:
                    return 'FALSE_POSITIVE'
            else:
                return 'FALSE_POSITIVE'

        # Medium confidence
        if review.structural_confidence >= 0.5:
            return 'POTENTIAL_SECRET'

        if review.false_positive_reasons and review.structural_confidence < 0.5:
            return 'FALSE_POSITIVE'

        return 'POTENTIAL_SECRET'

    def _compute_confidence(self, review: SecretReview, spec: dict) -> float:
        base = spec.get('confidence_base', 0.3)

        # Adjust based on entropy
        min_entropy = spec.get('min_entropy', 3.0)
        if review.shannon_entropy >= min_entropy:
            base = min(0.99, base + 0.05)
        elif review.shannon_entropy < 2.0:
            base = max(0.1, base - 0.3)

        # Adjust for false positives
        if review.is_placeholder:
            base = max(0.05, base - 0.5)
        if review.is_in_test_file:
            base = max(0.1, base - 0.15)
        if review.is_env_reference:
            base = max(0.2, base - 0.2)

        # Each false positive reason reduces confidence
        base = max(0.05, base - 0.1 * len(review.false_positive_reasons))

        return round(base, 4)

    def _generate_reasoning(self, review: SecretReview) -> str:
        parts = [f"AI Review: Classified as {review.ai_classification}."]

        parts.append(f"Shannon entropy: {review.shannon_entropy:.4f} bits/char "
                    f"(efficiency: {review.entropy_efficiency:.1f}%, {review.unique_chars} unique chars).")

        if review.structural_match:
            parts.append(f"Structural match: YES — matches {review.pattern_description} pattern.")
        else:
            parts.append(f"Structural match: NO — pattern not verified.")

        parts.append(f"Structural confidence: {review.structural_confidence:.0%}.")

        if review.is_env_reference:
            parts.append("Context shows environment variable reference — value loaded at runtime.")

        if review.is_placeholder:
            parts.append("Value matches known placeholder — FALSE POSITIVE.")

        if review.is_in_test_file:
            parts.append("Found in test/example file — reduced confidence.")

        if review.false_positive_reasons:
            parts.append(f"False positive indicators: {'; '.join(review.false_positive_reasons[:3])}")

        parts.append(f"KL divergence from uniform: {review.kl_divergence:.4f} "
                    f"({'uniform random' if review.kl_divergence < 0.1 else 'structured' if review.kl_divergence < 0.5 else 'highly structured'}).")

        return ' '.join(parts)

    def _get_node_type(self, classification: str) -> str:
        return {
            'REAL_SECRET': 'secret',
            'FALSE_POSITIVE': 'config',
            'SENSITIVE_CONFIG': 'sensitive_config',
            'POTENTIAL_SECRET': 'potential_secret',
        }.get(classification, 'unknown')


# ═══════════════════════════════════════════════════════════════════════════
# OPENCLAW — Conservative reviewer
# ═══════════════════════════════════════════════════════════════════════════

class OpenClaw:
    """OpenClaw reviewer — conservative, leans toward 'real secret' unless strong evidence.

    OpenClaw's strategy:
    - If the structural pattern matches AND entropy is above minimum, classify as REAL_SECRET
    - Only classify as FALSE_POSITIVE if there's clear evidence (placeholder, test file)
    - When in doubt, classify as POTENTIAL_SECRET (needs human review)
    """

    def __init__(self):
        self.reviews: deque = deque(maxlen=5000)
        self.name = "OpenClaw"

    def review(self, ai_review: SecretReview) -> dict:
        """Perform OpenClaw's independent review."""

        # OpenClaw's conservative logic
        if ai_review.is_placeholder:
            classification = 'FALSE_POSITIVE'
            confidence = 0.95
            reasoning = f"[OpenClaw] Clear placeholder detected — not a real secret."
        elif ai_review.structural_match and ai_review.shannon_entropy >= 3.0:
            # Conservative: if it looks like a secret and has decent entropy, it's probably real
            classification = 'REAL_SECRET'
            confidence = min(0.95, ai_review.structural_confidence)
            reasoning = (f"[OpenClaw] Structural match confirmed ({ai_review.pattern_description}) "
                        f"with sufficient entropy ({ai_review.shannon_entropy:.2f} bits/char). "
                        f"Conservative assessment: treat as real secret.")
        elif ai_review.is_env_reference:
            classification = 'SENSITIVE_CONFIG'
            confidence = 0.8
            reasoning = "[OpenClaw] Environment variable reference — not a hardcoded secret."
        elif ai_review.is_in_test_file and ai_review.shannon_entropy < 4.0:
            classification = 'FALSE_POSITIVE'
            confidence = 0.7
            reasoning = f"[OpenClaw] Found in test file with moderate entropy ({ai_review.shannon_entropy:.2f}) — likely test data."
        elif ai_review.structural_match:
            # Structural match but low entropy — uncertain
            classification = 'POTENTIAL_SECRET'
            confidence = 0.5
            reasoning = (f"[OpenClaw] Structural match but low entropy ({ai_review.shannon_entropy:.2f}). "
                        f"Cannot confirm or deny — needs human review.")
        else:
            classification = 'POTENTIAL_SECRET'
            confidence = 0.3
            reasoning = "[OpenClaw] Insufficient evidence to classify — needs human review."

        result = {
            'reviewer': self.name,
            'classification': classification,
            'confidence': confidence,
            'reasoning': reasoning,
        }

        self.reviews.append({'ts': time.time(), 'secret_id': ai_review.secret_id, **result})
        return result


# ═══════════════════════════════════════════════════════════════════════════
# HERMES BEAST CLAW — Aggressive adversarial reviewer
# ═══════════════════════════════════════════════════════════════════════════

class HermesBeastClaw:
    """Hermes Beast Claw — aggressive reviewer, actively tries to prove secrets are false positives.

    Hermes's strategy:
    - Actively looks for reasons to reject the secret
    - Checks for all false positive indicators
    - Questions the entropy — is it artificially high?
    - Questions the context — could this be legitimate code?
    - Only confirms as REAL_SECRET if it survives all challenges
    """

    def __init__(self):
        self.reviews: deque = deque(maxlen=5000)
        self.name = "Hermes Beast Claw"

    def review(self, ai_review: SecretReview) -> dict:
        """Perform Hermes Beast Claw's adversarial review."""

        challenges = []

        # Challenge 1: Is the entropy artificially high?
        if ai_review.shannon_entropy > 4.0 and ai_review.kl_divergence < 0.1:
            challenges.append("Entropy is high but distribution is near-uniform — could be randomly generated test data")
        elif ai_review.shannon_entropy < 2.0:
            challenges.append(f"Low entropy ({ai_review.shannon_entropy:.2f}) — unlikely to be cryptographic material")

        # Challenge 2: Could the value be a known test/example value?
        if ai_review.is_placeholder:
            challenges.append("Value matches known placeholder/example — definitely false positive")
        elif 'example' in ai_review.value_preview.lower():
            challenges.append("Value contains 'example' — likely a documentation example")

        # Challenge 3: Is the file context suspicious?
        if ai_review.is_in_test_file:
            challenges.append(f"Found in test file ({ai_review.file}) — likely test fixture data")
        if 'example' in ai_review.file.lower() or 'demo' in ai_review.file.lower():
            challenges.append(f"File name suggests example/demo — reduced confidence")

        # Challenge 4: Could the structural match be coincidental?
        if ai_review.pattern_matched.startswith('hex_key_'):
            challenges.append("Hex key pattern — could be a hash, a Git SHA, or a UUID rather than a secret")
        if ai_review.pattern_matched == 'ssh_public_key':
            challenges.append("SSH public keys are NOT secrets — they're designed to be shared")

        # Challenge 5: Is this an environment variable reference?
        if ai_review.is_env_reference:
            challenges.append("Context shows env var reference — the value is a variable name, not a secret")

        # Challenge 6: Character diversity check
        if ai_review.unique_chars < 5 and len(ai_review.value_preview) > 10:
            challenges.append(f"Very low character diversity ({ai_review.unique_chars} unique) — likely repetitive pattern")

        # Hermes's aggressive classification
        if len(challenges) >= 3:
            classification = 'FALSE_POSITIVE'
            confidence = min(0.9, 0.5 + 0.1 * len(challenges))
            reasoning = f"[Hermes] {len(challenges)} challenges found — classified as FALSE_POSITIVE. " + " | ".join(challenges[:3])
        elif len(challenges) >= 1:
            # Some challenges but not overwhelming
            if ai_review.structural_match and ai_review.shannon_entropy >= 4.0 and not ai_review.is_placeholder:
                classification = 'POTENTIAL_SECRET'
                confidence = 0.4
                reasoning = f"[Hermes] {len(challenges)} challenges found but structural match + high entropy. Needs human review. " + " | ".join(challenges[:2])
            else:
                classification = 'FALSE_POSITIVE'
                confidence = 0.65
                reasoning = f"[Hermes] {len(challenges)} challenges found — leaning FALSE_POSITIVE. " + " | ".join(challenges[:2])
        else:
            # No challenges — Hermes agrees it's real
            if ai_review.structural_match and ai_review.shannon_entropy >= 3.5:
                classification = 'REAL_SECRET'
                confidence = 0.9
                reasoning = f"[Hermes] No challenges found. Structural match + entropy {ai_review.shannon_entropy:.2f} — confirmed REAL_SECRET."
            else:
                classification = 'POTENTIAL_SECRET'
                confidence = 0.5
                reasoning = f"[Hermes] No challenges but insufficient evidence to confirm — POTENTIAL_SECRET."

        result = {
            'reviewer': self.name,
            'classification': classification,
            'confidence': confidence,
            'reasoning': reasoning,
            'challenges_raised': len(challenges),
            'challenges_detail': challenges,
        }

        self.reviews.append({'ts': time.time(), 'secret_id': ai_review.secret_id, **result})
        return result


# ═══════════════════════════════════════════════════════════════════════════
# TRIPLE REVIEW ORCHESTRATOR — combines AI + OpenClaw + Hermes
# ═══════════════════════════════════════════════════════════════════════════

class TripleReviewOrchestrator:
    """Orchestrates the three-way review: AI + OpenClaw + Hermes Beast Claw.

    All three reviews are combined into a final classification:
    - If all three agree → high confidence
    - If two agree, one disagrees → medium confidence, go with majority
    - If all three disagree → low confidence, needs human review
    """

    CLASSIFICATION_RANK = {
        'REAL_SECRET': 3,
        'POTENTIAL_SECRET': 2,
        'SENSITIVE_CONFIG': 1,
        'FALSE_POSITIVE': 0,
    }

    def __init__(self):
        self.ai_reviewer = AISecretReviewer()
        self.openclaw = OpenClaw()
        self.hermes = HermesBeastClaw()
        self.all_reviews: deque = deque(maxlen=5000)
        self._lock = threading.Lock()

    def review_secret(self, secret_data: dict) -> SecretReview:
        """Perform the full three-way review of a secret."""

        # Step 1: AI review (with full entropy proof)
        review = self.ai_reviewer.review(secret_data)

        # Step 2: OpenClaw review
        openclaw_result = self.openclaw.review(review)
        review.openclaw_classification = openclaw_result['classification']
        review.openclaw_confidence = openclaw_result['confidence']
        review.openclaw_reasoning = openclaw_result['reasoning']

        # Step 3: Hermes Beast Claw review
        hermes_result = self.hermes.review(review)
        review.hermes_classification = hermes_result['classification']
        review.hermes_confidence = hermes_result['confidence']
        review.hermes_reasoning = hermes_result['reasoning']

        # Step 4: Combine all three reviews
        self._combine_reviews(review)

        with self._lock:
            self.all_reviews.append(review)

        return review

    def _combine_reviews(self, review: SecretReview):
        """Combine the three reviews into a final classification."""
        classifications = [
            review.ai_classification,
            review.openclaw_classification,
            review.hermes_classification,
        ]
        confidences = [
            review.ai_confidence,
            review.openclaw_confidence,
            review.hermes_confidence,
        ]

        # Check for unanimous agreement
        if len(set(classifications)) == 1:
            review.final_classification = classifications[0]
            review.final_confidence = sum(confidences) / 3
            review.final_reasoning = (
                f"All three reviewers agree: {classifications[0]}. "
                f"AI: {review.ai_confidence:.0%}, OpenClaw: {review.openclaw_confidence:.0%}, "
                f"Hermes: {review.hermes_confidence:.0%}."
            )
            return

        # Check for majority (2 out of 3 agree)
        class_counts = Counter(classifications)
        most_common, most_count = class_counts.most_common(1)[0]

        if most_count >= 2:
            review.final_classification = most_common
            # Confidence is the average of the agreeing reviewers
            agreeing_confidences = [c for c, cl in zip(confidences, classifications) if cl == most_common]
            review.final_confidence = sum(agreeing_confidences) / len(agreeing_confidences)
            dissenting = [cl for cl in classifications if cl != most_common]
            review.final_reasoning = (
                f"Majority vote: {most_common} (2/3 agree). "
                f"Dissenting opinion: {dissenting[0]}. "
                f"AI: {review.ai_classification} ({review.ai_confidence:.0%}), "
                f"OpenClaw: {review.openclaw_classification} ({review.openclaw_confidence:.0%}), "
                f"Hermes: {review.hermes_classification} ({review.hermes_confidence:.0%})."
            )
            return

        # All three disagree — take the middle ground
        sorted_classes = sorted(classifications, key=lambda c: self.CLASSIFICATION_RANK.get(c, 0))
        review.final_classification = sorted_classes[1]  # Middle value
        review.final_confidence = 0.3  # Low confidence — needs human review
        review.final_reasoning = (
            f"All three reviewers disagree. Taking middle ground: {sorted_classes[1]}. "
            f"AI: {review.ai_classification}, OpenClaw: {review.openclaw_classification}, "
            f"Hermes: {review.hermes_classification}. NEEDS HUMAN REVIEW."
        )

        # Set correct node type
        review.correct_node_type = {
            'REAL_SECRET': 'secret',
            'FALSE_POSITIVE': 'config',
            'SENSITIVE_CONFIG': 'sensitive_config',
            'POTENTIAL_SECRET': 'potential_secret',
        }.get(review.final_classification, 'unknown')

    def review_batch(self, secrets: list[dict]) -> list[SecretReview]:
        """Review a batch of secrets."""
        return [self.review_secret(s) for s in secrets]

    def get_state(self) -> dict:
        return {
            'module': 'TripleReviewOrchestrator',
            'total_reviews': len(self.all_reviews),
            'ai_reviews': len(self.ai_reviewer.__class__.__mro__),
            'openclaw_reviews': len(self.openclaw.reviews),
            'hermes_reviews': len(self.hermes.reviews),
            'real_secrets': sum(1 for r in self.all_reviews if r.final_classification == 'REAL_SECRET'),
            'false_positives': sum(1 for r in self.all_reviews if r.final_classification == 'FALSE_POSITIVE'),
            'sensitive_configs': sum(1 for r in self.all_reviews if r.final_classification == 'SENSITIVE_CONFIG'),
            'potential_secrets': sum(1 for r in self.all_reviews if r.final_classification == 'POTENTIAL_SECRET'),
        }


# ═══════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("SSB V11 Z MARK — SECRET REVIEW V2: AI + OPENCLAW + HERMES")
    print("=" * 70)

    orchestrator = TripleReviewOrchestrator()

    # Test secrets — including SSH keys and hex keys
    test_secrets = [
        {
            "value": "AKIAIOSFODNN7EXAMPLE",
            "file": ".env", "line": 5,
            "pattern_matched": "aws_access_key",
            "context": "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        },
        {
            "value": "ghp_1234567890abcdefghijklmnopqrstuvwxyz1234",
            "file": ".env", "line": 3,
            "pattern_matched": "github_token",
            "context": "GITHUB_TOKEN=ghp_1234567890abcdefghijklmnopqrstuvwxyz1234"
        },
        {
            "value": "your_api_key_here",
            "file": "config.py", "line": 12,
            "pattern_matched": "password_assignment",
            "context": 'password = "your_api_key_here"'
        },
        {
            "value": "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA1234567890abcdefghijklmnopqrstuvwxyz\n-----END RSA PRIVATE KEY-----",
            "file": "~/.ssh/id_rsa", "line": 1,
            "pattern_matched": "ssh_rsa_private",
            "context": "SSH RSA Private Key"
        },
        {
            "value": "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXktdjEAaaaa\n-----END OPENSSH PRIVATE KEY-----",
            "file": "~/.ssh/id_ed25519", "line": 1,
            "pattern_matched": "ssh_ed25519_private",
            "context": "SSH Ed25519 Private Key"
        },
        {
            "value": "a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef1234",
            "file": "secrets.bin", "line": 1,
            "pattern_matched": "hex_key_64",
            "context": "private_key_hex=a1b2c3d4e5f6789012345678901234567890abcdef1234567890abcdef1234"
        },
        {
            "value": "0000000000000000000000000000000000000000000000000000000000000000",
            "file": "test_data.py", "line": 5,
            "pattern_matched": "hex_key_64",
            "context": "test_hash = '0000000000000000000000000000000000000000000000000000000000000000'"
        },
        {
            "value": "5d41402abc4b2a76b9719d911017c592",
            "file": "hashes.txt", "line": 10,
            "pattern_matched": "hex_key_32",
            "context": "md5_hash=5d41402abc4b2a76b9719d911017c592"
        },
        {
            "value": "postgres://admin:secretpass@localhost:5432/production",
            "file": "docker-compose.yml", "line": 8,
            "pattern_matched": "database_url",
            "context": "DATABASE_URL=postgres://admin:secretpass@localhost:5432/production"
        },
        {
            "value": "os.environ.get('DATABASE_URL')",
            "file": "app.py", "line": 15,
            "pattern_matched": "database_url",
            "context": "db_url = os.environ.get('DATABASE_URL')"
        },
        {
            "value": "sk_test_1234567890abcdefghijklmnopqrstuv",
            "file": "payment.py", "line": 20,
            "pattern_matched": "stripe_key",
            "context": 'stripe_key = "sk_test_1234567890abcdefghijklmnopqrstuv"'
        },
        {
            "value": "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQDExample1234567890=",
            "file": "~/.ssh/id_rsa.pub", "line": 1,
            "pattern_matched": "ssh_public_key",
            "context": "SSH Public Key"
        },
    ]

    print(f"\nReviewing {len(test_secrets)} secrets through AI + OpenClaw + Hermes Beast Claw...\n")

    for i, secret in enumerate(test_secrets, 1):
        review = orchestrator.review_secret(secret)

        print(f"{'─' * 70}")
        print(f"SECRET #{i}: {review.value_preview}")
        print(f"  File: {review.file}:{review.line}")
        print(f"  Pattern: {review.pattern_matched} ({review.pattern_description})")
        print()
        print(f"  SHANNON ENTROPY: {review.shannon_entropy:.6f} bits/char")
        print(f"  Efficiency: {review.entropy_efficiency:.1f}% | Unique chars: {review.unique_chars}")
        print(f"  KL Divergence: {review.kl_divergence:.6f}")
        print()
        print(f"  AI Review:       {review.ai_classification} ({review.ai_confidence:.0%})")
        print(f"    {review.ai_reasoning[:150]}")
        print()
        print(f"  OpenClaw:        {review.openclaw_classification} ({review.openclaw_confidence:.0%})")
        print(f"    {review.openclaw_reasoning[:150]}")
        print()
        print(f"  Hermes Beast:    {review.hermes_classification} ({review.hermes_confidence:.0%})")
        print(f"    {review.hermes_reasoning[:150]}")
        print()
        print(f"  ═══ FINAL: {review.final_classification} ({review.final_confidence:.0%}) ═══")
        print(f"    Node type: {review.correct_node_type}")
        print(f"    {review.final_reasoning[:200]}")

    # Print entropy proof for one secret
    print(f"\n{'═' * 70}")
    print("ENTROPY PROOF EXAMPLE (for GitHub token):")
    print(f"{'═' * 70}")
    review = orchestrator.all_reviews[1]  # GitHub token
    print(review.entropy_proof)

    # Summary
    print(f"\n{'═' * 70}")
    print("SUMMARY")
    print(f"{'═' * 70}")
    state = orchestrator.get_state()
    print(f"  Total reviews: {state['total_reviews']}")
    print(f"  Real secrets: {state['real_secrets']}")
    print(f"  False positives: {state['false_positives']}")
    print(f"  Sensitive configs: {state['sensitive_configs']}")
    print(f"  Potential secrets: {state['potential_secrets']}")
    print(f"\n  All three reviewers (AI + OpenClaw + Hermes) reviewed every secret.")
    print(f"  Shannon's entropy computed with full mathematical proof for each.")
    print(f"  SSH keys (RSA, Ed25519) and hex keys (32, 64 char) detected and reviewed.")
