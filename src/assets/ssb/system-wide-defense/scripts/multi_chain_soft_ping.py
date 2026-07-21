#!/usr/bin/env python3
"""
SSB V11 Z MARK — Multi-Chain Soft Ping
========================================

Adapts the ACE v2 multi-chain decision architecture into a security decision
engine. Seven chains compete in parallel; the weighted sum chooses the action.

The Soft Ping is a *computational shortcut* — it does not call any LLM, does
not consume any tokens, and runs in O(file_size) time. It is a *ping* in the
sense of "feeling out" the file's threat surface before any expensive
analysis kicks in.

THE SEVEN CHAINS
================

Vault     — pattern-match the scan report against a corpus of known exploit
            primitives. "Have I seen this exact weapon before?"
            Pulls strongest when findings have HIGH/CRITICAL severity.

Mirror    — audit the file's own internal logic for contradictions, dead
            branches, suspicious try/except wrapping. "Does the code's
            story hang together?"
            Pulls strongest when L5 (structure) findings are present.

Null      — the ANTI-CAMOUFLAGE chain. Surfaces weak-fit signals even when
            the file looks completely normal. Resists false closure on
            benign-looking names. "What is off about this file that I'm
            being invited to ignore?"
            Pulls strongest when severity_score is LOW but file_type is
            Python AND entropy is elevated AND there's a delay primitive
            (APT signature). This is the chain that catches `helpers/compat.py`.

Flame     — synthesize novel exploit hypotheses. "What *could* this code do
            if twisted?" Projects the file's primitives into plausible
            attack chains. Pulls strongest when there are >=2 HIGH findings
            from different layers (cross-layer corroboration).

Joel      — plan the defensive response. Pulls toward QUARANTINE for
            medium-confidence detections, DELETE for high-confidence critical
            detections. "What is the proportional response?"

Autonomy  — decide escalate-vs-quarantine-vs-delete based on attacker
            behavior pattern. Pulls toward ESCALATE when the same source
            has fired multiple detections in a short window (APT pattern),
            toward QUARANTINE for first offenses.

Affect    — detect attacker emotional signature. Confidence = slow, deliberate,
            low entropy in filename/timing, single exploit type. Desperation
            = rapid succession, high entropy, repeated attempts, multiple
            exploit types fired in parallel. Affect feeds back into
            Autonomy's threshold.

DECISION THRESHOLDS
===================

  severity_score < 0.10        → ALLOW (with INFO log)
  0.10 <= score < 0.35         → ALLOW (with WARN log + soft-ping chain trace)
  0.35 <= score < 0.65         → QUARANTINE
  0.65 <= score < 0.85         → QUARANTINE + ESCALATE (alert security team)
  score >= 0.85                → DELETE + ESCALATE (immediate destruction)

TOKEN COST: 0
=============

The entire multi-chain evaluation is local computation. No LLM is called.
No tokens are spent on detection, analysis, prediction, or decision.
Tokens are spent ONLY when an ESCALATE alert is emitted (~80 tokens for
the alert payload). This is by design — the system runs at 99% local
computation and only escalates to tokenized channels when human attention
is required.
"""

from __future__ import annotations
import hashlib
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Iterable

from content_scanner import ScanReport, Finding, Layer


# ---------------------------------------------------------------------------
# Decision model
# ---------------------------------------------------------------------------

class Action(str, Enum):
    ALLOW = "allow"
    ALLOW_LOGGED = "allow_logged"
    QUARANTINE = "quarantine"
    QUARANTINE_ESCALATE = "quarantine_escalate"
    DELETE_ESCALATE = "delete_escalate"

@dataclass
class ChainScore:
    name: str
    score: float           # 0..1, contribution to threat conviction
    confidence: float      # 0..1, how sure this chain is about its score
    weight: float          # multiplier on score
    reasoning: list[str] = field(default_factory=list)
    triggered_primitives: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name, "score": self.score, "confidence": self.confidence,
            "weight": self.weight, "weighted": self.score * self.weight,
            "reasoning": self.reasoning, "triggered_primitives": self.triggered_primitives,
        }

@dataclass
class AttackerProfile:
    """Tracks recent uploads from a source to derive attacker affect."""
    source_id: str
    uploads: deque = field(default_factory=lambda: deque(maxlen=20))
    # Each entry: (timestamp_ms, file_hash, severity_score, num_findings, primitives_set)

    def record(self, file_hash: str, severity_score: float, num_findings: int, primitives: set[str]) -> None:
        self.uploads.append((time.time() * 1000.0, file_hash, severity_score, num_findings, primitives))

    @property
    def is_desperate(self) -> bool:
        if len(self.uploads) < 2:
            return False
        now = time.time() * 1000.0
        recent = [u for u in self.uploads if now - u[0] < 60_000]  # last 60s
        if len(recent) < 3:
            return False
        # Multiple distinct exploit primitives in short window = desperate
        all_prims = set()
        for u in recent:
            all_prims |= u[4]
        # High entropy of primitives (many different types) = desperate
        if len(all_prims) >= 4:
            return True
        # Very rapid succession (< 3s avg gap)
        gaps = [recent[i+1][0] - recent[i][0] for i in range(len(recent)-1)]
        if gaps and sum(gaps)/len(gaps) < 3000:
            return True
        return False

    @property
    def is_confident_apt(self) -> bool:
        if len(self.uploads) < 1:
            return False
        # Single upload with low primitive diversity + medium severity + delay primitive
        last = self.uploads[-1]
        if len(last[4]) <= 2 and 0.35 <= last[2] <= 0.7:
            return True
        return False

    @property
    def repeat_offender(self) -> bool:
        """Has this source been flagged >= 2 times in last 5 min?"""
        if len(self.uploads) < 2:
            return False
        now = time.time() * 1000.0
        flagged = [u for u in self.uploads if now - u[0] < 300_000 and u[2] >= 0.35]
        return len(flagged) >= 2

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_id": self.source_id,
            "upload_count": len(self.uploads),
            "is_desperate": self.is_desperate,
            "is_confident_apt": self.is_confident_apt,
            "repeat_offender": self.repeat_offender,
        }


@dataclass
class SoftPingDecision:
    action: str
    severity_score: float           # weighted aggregate 0..1
    chain_matrix: dict[str, dict[str, Any]] = field(default_factory=dict)
    chain_ranking: list[dict[str, Any]] = field(default_factory=list)
    chain_competition: list[str] = field(default_factory=list)
    winning_action_gap: float = 0.0
    attacker_profile: dict[str, Any] = field(default_factory=dict)
    decision_timestamp_ms: float = 0.0
    decision_duration_ms: float = 0.0
    tokens_consumed: int = 0          # ALWAYS 0 for detection/decision
    rationale: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Chain implementations
# ---------------------------------------------------------------------------

class _Vault:
    """Match scan report against known exploit corpus."""
    NAME = "vault"
    WEIGHT = 0.18

    # Maps primitives to corpus-severity multipliers
    CORPUS = {
        "subprocess.shell_true": 1.0,
        "os.system": 1.0,
        "os.popen": 0.95,
        "eval": 0.95,
        "exec": 0.95,
        "builtin.eval": 0.95,
        "builtin.exec": 0.95,
        "pickle.loads": 1.0,
        "marshal.loads": 0.85,
        "shell.injection": 1.0,
        "shell.subprocess_shell": 1.0,
        "reverse.shell": 1.0,
        "reverse.shell.bash": 1.0,
        "reverse.shell.python": 0.95,
        "base64.decoded_payload": 0.95,
        "ctypes.native_load": 0.85,
        "dunder.__subclasses__": 0.85,
        "dunder.__globals__": 0.85,
        "dunder.__builtins__": 0.85,
        "dunder.__code__": 0.85,
        "path.traversal": 0.75,
        "ssrf.url": 0.75,
        "symlink.race": 0.65,
        "shellcode.x86_nop": 0.95,
        "shellcode.x86_int80": 0.9,
        "shellcode.x86_64_syscall": 0.9,
        "structure.delayed_payload": 0.8,
        "structure.try_wraps_sensitive": 0.75,
        "env.leak": 0.6,
    }

    def score(self, report: ScanReport) -> ChainScore:
        max_corpus = 0.0
        triggered = []
        reasoning = []
        for f in report.findings:
            mult = self.CORPUS.get(f.primitive, 0.0)
            if mult > 0:
                triggered.append(f.primitive)
                contribution = mult * f.confidence
                if contribution > max_corpus:
                    max_corpus = contribution
                    reasoning.append(
                        f"vault matched primitive '{f.primitive}' "
                        f"(severity={f.severity}, confidence={f.confidence:.2f}) "
                        f"→ contribution {contribution:.3f}"
                    )
        if not reasoning:
            reasoning.append("no corpus matches — file does not resemble a known exploit primitive")
        return ChainScore(
            name=self.NAME, score=max_corpus, confidence=0.9 if max_corpus > 0 else 0.4,
            weight=self.WEIGHT, reasoning=reasoning, triggered_primitives=triggered,
        )


class _Mirror:
    """Audit the file's own logic for contradictions and tell-tale shapes."""
    NAME = "mirror"
    WEIGHT = 0.16

    def score(self, report: ScanReport) -> ChainScore:
        structural = [f for f in report.findings if f.layer == Layer.L5_STRUCTURE.value]
        ast_findings = [f for f in report.findings if f.layer == Layer.L1_AST.value]
        score = 0.0
        triggered = []
        reasoning = []
        for f in structural:
            triggered.append(f.primitive)
            s = {"structure.delayed_payload": 0.85,
                 "structure.try_wraps_sensitive": 0.75,
                 "structure.env_gated": 0.55,
                 "structure.long_string": 0.45}.get(f.primitive, 0.3)
            score = max(score, s * f.confidence)
            reasoning.append(f"mirror saw '{f.primitive}' at {f.location} → +{s:.2f}")
        # Bare except + sensitive call = strong mirror signal
        has_bare_except = any(f.primitive == "except.bare" for f in ast_findings)
        has_sensitive = any(f.primitive in {"builtin.eval", "builtin.exec", "os.system",
                                            "subprocess.shell_true", "pickle.loads"}
                            for f in ast_findings)
        if has_bare_except and has_sensitive:
            score = max(score, 0.8)
            reasoning.append("mirror contradiction: bare except wraps a sensitive call → +0.80")
        if not reasoning:
            reasoning.append("no structural contradictions found")
        return ChainScore(
            name=self.NAME, score=score, confidence=0.8 if score > 0 else 0.5,
            weight=self.WEIGHT, reasoning=reasoning, triggered_primitives=triggered,
        )


class _Null:
    """The ANTI-CAMOUFLAGE chain. Surfaces weak-fit signals.

    Null resists false closure on benign-looking names. It deliberately
    pulls toward inspection when:
      - The file looks normal (low severity_score)
      - BUT the file_type is Python AND entropy is elevated
      - OR there is a delay primitive (APT signature)
      - OR there are contradictions between layers (e.g. clean AST but
        suspicious entropy)
    """
    NAME = "null"
    WEIGHT = 0.12

    def score(self, report: ScanReport, base_severity: float) -> ChainScore:
        score = 0.0
        triggered = []
        reasoning = []
        # Case 1: APT signature — Python file + elevated entropy + delay
        if report.file_type == "python" and report.entropy > 6.0:
            has_delay = any(f.primitive == "structure.delayed_payload" for f in report.findings)
            if has_delay and base_severity < 0.5:
                # The other chains are under-reacting — Null steps in.
                score = 0.75
                triggered.append("apt_signature_underweighted")
                reasoning.append(
                    f"null: APT signature detected (python + entropy={report.entropy:.2f} + delay) "
                    f"but base severity only {base_severity:.3f} — surfacing weak-fit signal"
                )
        # Case 2: Cross-layer contradiction — clean AST but high entropy
        ast_findings = [f for f in report.findings if f.layer == Layer.L1_AST.value]
        if report.entropy > 7.0 and not any(f.severity in {"high", "critical"} for f in ast_findings):
            score = max(score, 0.65)
            triggered.append("entropy_ast_contradiction")
            reasoning.append(
                f"null: high entropy ({report.entropy:.2f}) but no high-severity AST finding "
                f"— possible obfuscated payload the AST layer missed"
            )
        # Case 3: Benign-looking filename but flagged primitive present
        # (Null doesn't know the filename, but it knows if other chains
        # under-reacted to a HIGH-severity finding)
        if base_severity < 0.4 and any(f.severity == "high" for f in report.findings):
            score = max(score, 0.7)
            triggered.append("severity_underweight")
            reasoning.append(
                f"null: HIGH-severity finding present but base severity only {base_severity:.3f} "
                f"— resisting false closure"
            )
        if not reasoning:
            reasoning.append("no weak-fit signals surfaced")
        return ChainScore(
            name=self.NAME, score=score, confidence=0.75 if score > 0 else 0.6,
            weight=self.WEIGHT, reasoning=reasoning, triggered_primitives=triggered,
        )


class _Flame:
    """Synthesize novel exploit hypotheses from primitive combinations."""
    NAME = "flame"
    WEIGHT = 0.18

    def score(self, report: ScanReport) -> ChainScore:
        # Flame is excited by cross-layer corroboration — when findings
        # from different layers agree, the threat is more real.
        layers_with_findings = {f.layer for f in report.findings}
        cross_layer_count = len(layers_with_findings)
        critical_count = sum(1 for f in report.findings if f.severity == "critical")
        high_count = sum(1 for f in report.findings if f.severity == "high")
        score = 0.0
        triggered = []
        reasoning = []
        if cross_layer_count >= 3 and (critical_count + high_count) >= 2:
            score = 0.85
            triggered.append("cross_layer_corroboration")
            reasoning.append(
                f"flame: {cross_layer_count} layers agree, {critical_count} critical + "
                f"{high_count} high findings — synthesizing exploit chain"
            )
        elif cross_layer_count >= 2 and (critical_count + high_count) >= 1:
            score = 0.55
            triggered.append("partial_corroboration")
            reasoning.append(
                f"flame: {cross_layer_count} layers agree, {critical_count} critical + "
                f"{high_count} high — plausible exploit chain"
            )
        # Reverse shell + base64 = full attack chain
        prim_set = {f.primitive for f in report.findings}
        if "reverse.shell" in prim_set and "base64.decoded_payload" in prim_set:
            score = max(score, 0.9)
            triggered.append("full_chain_reverse_shell")
            reasoning.append("flame: reverse shell primitive + base64 payload = full attack chain")
        # pickle + marshal = deserialization weapon
        if "pickle.loads" in prim_set and "marshal.loads" in prim_set:
            score = max(score, 0.85)
            triggered.append("deserialization_weapon")
            reasoning.append("flame: pickle + marshal = multi-vector deserialization weapon")
        if not reasoning:
            reasoning.append("no novel exploit hypotheses synthesized")
        return ChainScore(
            name=self.NAME, score=score, confidence=0.85 if score > 0 else 0.4,
            weight=self.WEIGHT, reasoning=reasoning, triggered_primitives=triggered,
        )


class _Joel:
    """Plan the defensive response."""
    NAME = "joel"
    WEIGHT = 0.16

    def score(self, report: ScanReport, base_severity: float) -> ChainScore:
        # Joel translates severity into a recommended action intensity.
        # Score here is the conviction that ACTION is needed.
        if base_severity >= 0.85:
            score = 0.95
            reasoning = [f"joel: severity {base_severity:.3f} → DELETE + ESCALATE"]
        elif base_severity >= 0.65:
            score = 0.8
            reasoning = [f"joel: severity {base_severity:.3f} → QUARANTINE + ESCALATE"]
        elif base_severity >= 0.35:
            score = 0.6
            reasoning = [f"joel: severity {base_severity:.3f} → QUARANTINE"]
        elif base_severity >= 0.10:
            score = 0.3
            reasoning = [f"joel: severity {base_severity:.3f} → ALLOW + WARN log"]
        else:
            score = 0.1
            reasoning = [f"joel: severity {base_severity:.3f} → ALLOW + INFO log"]
        return ChainScore(
            name=self.NAME, score=score, confidence=0.9,
            weight=self.WEIGHT, reasoning=reasoning,
            triggered_primitives=[],
        )


class _Autonomy:
    """Decide escalate-vs-quarantine-vs-delete based on attacker behavior."""
    NAME = "autonomy"
    WEIGHT = 0.12

    def score(self, base_severity: float, profile: AttackerProfile) -> ChainScore:
        score = base_severity
        triggered = []
        reasoning = []
        if profile.repeat_offender:
            score = min(1.0, score + 0.15)
            triggered.append("repeat_offender")
            reasoning.append("autonomy: repeat offender in 5min window → +0.15 conviction")
        if profile.is_desperate:
            score = min(1.0, score + 0.10)
            triggered.append("desperate_pattern")
            reasoning.append("autonomy: desperate attacker pattern detected → +0.10 conviction (rapid multi-vector)")
        if profile.is_confident_apt:
            # Confident APT gets *more* scrutiny, not less — they're harder to catch
            score = min(1.0, score + 0.08)
            triggered.append("confident_apt_pattern")
            reasoning.append("autonomy: confident APT signature → +0.08 scrutiny (patient adversary)")
        if not reasoning:
            reasoning.append(f"autonomy: no behavioral modifiers, score={score:.3f}")
        return ChainScore(
            name=self.NAME, score=score, confidence=0.8,
            weight=self.WEIGHT, reasoning=reasoning,
            triggered_primitives=triggered,
        )


class _Affect:
    """Detect attacker emotional signature (confidence vs desperation)."""
    NAME = "affect"
    WEIGHT = 0.08

    def score(self, profile: AttackerProfile) -> ChainScore:
        # Affect doesn't directly add to threat conviction; it modulates
        # Autonomy. We still produce a score for the chain matrix.
        if profile.is_desperate:
            score = 0.7
            reasoning = ["affect: desperation detected (rapid multi-vector uploads, high primitive entropy)"]
        elif profile.is_confident_apt:
            score = 0.5
            reasoning = ["affect: confidence detected (slow, deliberate, low primitive diversity)"]
        else:
            score = 0.2
            reasoning = ["affect: no strong affective signature yet"]
        return ChainScore(
            name=self.NAME, score=score, confidence=0.7,
            weight=self.WEIGHT, reasoning=reasoning,
            triggered_primitives=[],
        )


# ---------------------------------------------------------------------------
# Multi-chain engine
# ---------------------------------------------------------------------------

class MultiChainSoftPing:
    """Runs all 7 chains in parallel, aggregates, decides action."""

    def __init__(self) -> None:
        self.vault = _Vault()
        self.mirror = _Mirror()
        self.null = _Null()
        self.flame = _Flame()
        self.joel = _Joel()
        self.autonomy = _Autonomy()
        self.affect = _Affect()
        self._profiles: dict[str, AttackerProfile] = {}

    def get_profile(self, source_id: str) -> AttackerProfile:
        if source_id not in self._profiles:
            self._profiles[source_id] = AttackerProfile(source_id=source_id)
        return self._profiles[source_id]

    def decide(self, report: ScanReport, source_id: str = "unknown") -> SoftPingDecision:
        t0 = time.perf_counter()
        ts_ms = time.time() * 1000.0
        profile = self.get_profile(source_id)
        base_severity = report.severity_score

        # Run all chains
        s_vault = self.vault.score(report)
        s_mirror = self.mirror.score(report)
        s_null = self.null.score(report, base_severity)
        s_flame = self.flame.score(report)
        s_joel = self.joel.score(report, base_severity)
        s_autonomy = self.autonomy.score(base_severity, profile)
        s_affect = self.affect.score(profile)

        chains = [s_vault, s_mirror, s_null, s_flame, s_joel, s_autonomy, s_affect]

        # Aggregate — weighted sum, but Null can boost if it surfaced a weak-fit signal
        aggregate = sum(c.score * c.weight for c in chains)
        # Null boost: if Null's score is high but aggregate is low, the other chains
        # under-reacted. Add a Null-driven boost.
        if s_null.score > 0.6 and aggregate < 0.5:
            aggregate = max(aggregate, s_null.score * 0.85)
        aggregate = min(1.0, aggregate)

        # Build chain matrix
        chain_matrix = {c.name: c.as_dict() for c in chains}
        chain_ranking = sorted(
            [{"name": c.name, "weighted": c.score * c.weight, "score": c.score,
              "confidence": c.confidence, "weight": c.weight,
              "triggered": c.triggered_primitives} for c in chains],
            key=lambda r: r["weighted"], reverse=True,
        )
        chain_competition = []
        for c in chains:
            chain_competition.append(
                f"{c.name}: score={c.score:.3f} × weight={c.weight:.2f} = {c.score*c.weight:.4f}"
                + (f" | triggered: {','.join(c.triggered_primitives)}" if c.triggered_primitives else "")
            )

        # Decide action
        if aggregate < 0.10:
            action = Action.ALLOW
        elif aggregate < 0.35:
            action = Action.ALLOW_LOGGED
        elif aggregate < 0.65:
            action = Action.QUARANTINE
        elif aggregate < 0.85:
            action = Action.QUARANTINE_ESCALATE
        else:
            action = Action.DELETE_ESCALATE

        # Compute winning action gap (how close was the runner-up action)
        # We approximate by the distance from the next threshold.
        thresholds = [0.10, 0.35, 0.65, 0.85, 1.01]
        idx = next(i for i, t in enumerate(thresholds) if aggregate < t)
        gap = min(
            aggregate - (thresholds[idx-1] if idx > 0 else 0.0),
            thresholds[idx] - aggregate,
        )

        # Tokens: 0 for decision. ESCALATE actions would emit an alert (~80 tokens)
        # but the alert itself is the only tokenized step.
        tokens = 0

        rationale = []
        rationale.append(f"aggregate severity = {aggregate:.4f}")
        rationale.append(f"action chosen = {action.value} (gap to next threshold = {gap:.4f})")
        # Top 3 contributing chains
        for r in chain_ranking[:3]:
            rationale.append(
                f"top chain: {r['name']} contributed {r['weighted']:.4f} "
                f"(score={r['score']:.3f}, conf={r['confidence']:.2f})"
            )

        # Record this upload in the attacker profile
        primitives_set = {f.primitive for f in report.findings}
        profile.record(report.file_hash, aggregate, len(report.findings), primitives_set)

        decision = SoftPingDecision(
            action=action.value,
            severity_score=aggregate,
            chain_matrix=chain_matrix,
            chain_ranking=chain_ranking,
            chain_competition=chain_competition,
            winning_action_gap=gap,
            attacker_profile=profile.as_dict(),
            decision_timestamp_ms=ts_ms,
            decision_duration_ms=(time.perf_counter() - t0) * 1000.0,
            tokens_consumed=tokens,
            rationale=rationale,
        )
        return decision


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from content_scanner import scan_file
    import sys, json
    if len(sys.argv) < 2:
        print("usage: multi_chain_soft_ping.py <file> [source_id]")
        sys.exit(1)
    path = sys.argv[1]
    source = sys.argv[2] if len(sys.argv) > 2 else "self-test"
    report = scan_file(path, sys.argv.basename(path) if hasattr(sys.argv, 'basename') else path.split("/")[-1])
    engine = MultiChainSoftPing()
    decision = engine.decide(report, source)
    print(json.dumps(decision.as_dict(), indent=2))
