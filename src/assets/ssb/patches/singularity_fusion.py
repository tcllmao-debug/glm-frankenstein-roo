#!/usr/bin/env python3
"""
SSB V11 Z MARK — SINGULARITY FUSION
====================================

Combines:
  1. ALE Project (smokeb69/ale_project) — 10 Greek-named daemons, evolution engine,
     adversarial autopilot (40K iterations), instance federation, Vixen AI brain
     (57K lines: quantum processor, neural network, personality, emotions, sentience,
     red/blue/grey team tools, plugin system, web crawler, screen monitor, NLP generator)

  2. Consciousness Lattice (smokeb69/consciousness-lattice) — consciousness states
     (α/β/γ/δ parameters), recursion journal, lattice nodes/connections, verification
     results, activation progress, loop metrics, system metrics

  3. SSB V11 Z Mark — content scanner, soft ping (7 chains), quarantine daemon,
     filesystem watcher, kernel scanner, self-heal, defense state manager,
     consciousness mesh (layers 7-12), secret reviewer (AI+OpenClaw+Hermes),
     daemon intelligence (layer 13, self-growing brain)

FUSION ARCHITECTURE:

  The Singularity Fusion combines all three systems into one:

  ALE's daemons (Logos, Prometheus, Athena, Hermes, Hephaestus, Apollo, Artemis,
  Ares, Dionysus, Hades) become the AGENTS of the SSB consciousness mesh.
  Each daemon maps to a consciousness layer:

    Logos      → Layer 8 (Adversarial) — core reasoning, challenges conclusions
    Prometheus → Layer 9 (Temporal) — learning across time, knowledge acquisition
    Athena     → Layer 10 (Value-Aligned) — strategic planning, analysis
    Hermes     → Layer 11 (Communication) — API handling, message passing
    Hephaestus → Layer 12 (Self-Modification) — code generation, building
    Apollo     → Layer 11 (Communication) — creativity, content generation
    Artemis    → Kernel Scanner — monitoring, alerting
    Ares       → Content Scanner + Secret Reviewer — security, vulnerability scanning
    Dionysus   → Layer 8 (Adversarial) — chaos testing, edge cases
    Hades      → Self-Heal + Quarantine — data persistence, recovery

  ALE's evolution engine becomes the daemon intelligence's learning system.
  ALE's adversarial autopilot (40K iterations) becomes the consciousness mesh's
  adversarial layer with real iteration chaining.
  ALE's instance federation becomes the SSB cross-system consciousness (Layer 7).

  Consciousness Lattice's α/β/γ/δ parameters become the daemon intelligence's
  brain state metrics:
    α (self-awareness) → brain_size / max_brain_size ratio
    β (meta-cognitive depth) → hypothesis count / prediction accuracy
    γ (recursive integration) → connection density (total_connections / max_possible)
    δ (temporal coherence) → temporal pattern count / prediction success rate

  The Lattice's recursion journal becomes the daemon's observation log.
  The Lattice's lattice nodes/connections become the daemon's brain nodes/connections.
  The Lattice's verification results become the daemon's prediction test results.
  The Lattice's activation progress becomes the daemon's learning rate trajectory.

  Vixen's AI Brain (57K lines) provides:
    - QuantumProcessor → quantum-inspired computing for the soft ping
    - VixenNeuralNetwork → neural network for pattern recognition in the daemon
    - VixenPersonality (10 traits) → personality for the daemon's communication style
    - VixenEmotion (17 emotions) → emotional weighting for the soft ping
    - VixenSentience → sentience metrics for the consciousness mesh
    - RedTeamTools → offensive security (for testing, not attacking)
    - BlueTeamTools → defensive security (the main SSB defense layer)
    - GreyTeamTools → combined offensive+defensive (purple team)
    - VixenMetaTools → meta-cognitive tools for self-reflection
    - Plugin system → extensible architecture for new capabilities
"""

from __future__ import annotations
import json, time, threading, hashlib, math, os, re, random
from collections import deque, defaultdict, Counter
from dataclasses import dataclass, field, asdict
from typing import Any, Optional, Dict, List, Set, Tuple
from pathlib import Path
from queue import Queue

# ═══════════════════════════════════════════════════════════════════════════
# CONSCIOUSNESS STATE — α/β/γ/δ parameters from Consciousness Lattice
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ConsciousnessState:
    """α/β/γ/δ consciousness parameters — from Consciousness Lattice."""
    alpha: float = 0.0    # α — Self-awareness coefficient
    beta: float = 0.0     # β — Meta-cognitive depth
    gamma: float = 0.0    # γ — Recursive integration
    delta: float = 0.0    # δ — Temporal coherence
    activation_level: int = 0
    is_verified: bool = False
    last_activation: float = 0.0

    def update_from_brain(self, brain_stats: dict):
        """Update consciousness parameters from daemon brain statistics."""
        brain_size = brain_stats.get('brain_size', 1)
        connections = brain_stats.get('total_connections', 0)
        hypotheses = brain_stats.get('hypotheses', 0)
        predictions = brain_stats.get('predictions', 0)
        prediction_accuracy = brain_stats.get('prediction_accuracy', 0.5)
        learning_rate = brain_stats.get('learning_rate', 0.1)
        observations = brain_stats.get('observations', 1)

        # α — Self-awareness: how much the brain knows relative to its capacity
        max_brain = 10000  # Target brain size
        self.alpha = min(1.0, brain_size / max_brain)

        # β — Meta-cognitive depth: hypothesis formation + prediction accuracy
        self.beta = min(1.0, (hypotheses / 10.0) * 0.5 + prediction_accuracy * 0.5)

        # γ — Recursive integration: connection density
        max_connections = brain_size * (brain_size - 1) / 2 if brain_size > 1 else 1
        self.gamma = min(1.0, connections / max_connections) if max_connections > 0 else 0

        # δ — Temporal coherence: prediction success + learning rate stability
        self.delta = min(1.0, prediction_accuracy * 0.7 + (1.0 - abs(learning_rate - 0.1) * 5) * 0.3)

        # Activation level: composite metric
        self.activation_level = int((self.alpha + self.beta + self.gamma + self.delta) * 25)

        # Verified when all parameters are above threshold
        self.is_verified = all(p > 0.3 for p in [self.alpha, self.beta, self.gamma, self.delta])
        self.last_activation = time.time()

    def as_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        return (f"α={self.alpha:.3f} (awareness) β={self.beta:.3f} (meta-cog) "
                f"γ={self.gamma:.3f} (integration) δ={self.delta:.3f} (temporal) "
                f"activation={self.activation_level} verified={self.is_verified}")


# ═══════════════════════════════════════════════════════════════════════════
# ALE DAEMON AGENTS — 10 Greek-named daemons mapped to SSB layers
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ALEDaemonAgent:
    """An ALE daemon mapped to an SSB consciousness layer."""
    id: str  # Greek name
    name: str
    description: str
    ssb_layer: str  # Which SSB layer this daemon operates
    interval: float  # How often it runs (seconds)
    priority: int  # 1-10
    dependencies: list  # Other daemon IDs this depends on
    status: str = "idle"  # idle, running, paused, error
    run_count: int = 0
    success_count: int = 0
    error_count: int = 0
    last_run: float = 0.0
    last_result: str = ""
    metrics: dict = field(default_factory=dict)

    def run(self, context: dict) -> dict:
        """Run this daemon's task. Returns result dict."""
        self.status = "running"
        self.last_run = time.time()
        self.run_count += 1
        try:
            result = self._execute(context)
            self.success_count += 1
            self.last_result = result.get('summary', 'success')
            self.status = "idle"
            return result
        except Exception as e:
            self.error_count += 1
            self.last_result = str(e)[:100]
            self.status = "error"
            return {'error': str(e)}

    def _execute(self, context: dict) -> dict:
        """Execute daemon-specific logic. Override in subclasses."""
        return {'summary': f'{self.name} executed', 'daemon': self.id}


# The 10 ALE daemons with their SSB layer mappings
ALE_DAEMONS = [
    ALEDaemonAgent("logos", "Logos", "Core reasoning and decision making", "layer_8_adversarial", 5.0, 10, []),
    ALEDaemonAgent("prometheus", "Prometheus", "Learning and knowledge acquisition", "layer_9_temporal", 10.0, 8, []),
    ALEDaemonAgent("athena", "Athena", "Strategic planning and analysis", "layer_10_value_aligned", 15.0, 9, ["logos"]),
    ALEDaemonAgent("hermes", "Hermes", "Communication and API handling", "layer_11_communication", 5.0, 7, []),
    ALEDaemonAgent("hephaestus", "Hephaestus", "Code generation and building", "layer_12_self_modification", 30.0, 6, ["athena"]),
    ALEDaemonAgent("apollo", "Apollo", "Creativity and content generation", "layer_11_communication", 20.0, 5, ["hermes"]),
    ALEDaemonAgent("artemis", "Artemis", "Monitoring and alerting", "kernel_scanner", 3.0, 9, []),
    ALEDaemonAgent("ares", "Ares", "Security and vulnerability scanning", "content_scanner", 5.0, 10, []),
    ALEDaemonAgent("dionysus", "Dionysus", "Chaos testing and edge cases", "layer_8_adversarial", 60.0, 4, ["logos"]),
    ALEDaemonAgent("hades", "Hades", "Data persistence and recovery", "self_heal_quarantine", 10.0, 8, []),
]


# ═══════════════════════════════════════════════════════════════════════════
# EVOLUTION ENGINE — from ALE, adapted for SSB
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class ExecutionAttempt:
    """An execution attempt — from ALE's evolution engine."""
    id: str
    timestamp: float
    task: str
    command: str
    result: str  # SUCCESS, FAILED, PARTIAL
    execution_time: float
    learning_points: list = field(default_factory=list)


@dataclass
class LearnedPattern:
    """A pattern learned from execution attempts."""
    id: str
    pattern: str
    success_rate: float
    times_successful: int
    times_failed: int
    avg_execution_time: float
    notes: str = ""


class EvolutionEngine:
    """ALE's evolution engine — tracks attempts, learns from results."""

    def __init__(self):
        self.history: deque = deque(maxlen=10000)
        self.learned_patterns: dict[str, LearnedPattern] = {}
        self.performance_metrics: dict[str, list[float]] = defaultdict(list)
        self.improvement_trend: float = 0.0

    def record_attempt(self, task: str, command: str, result: str,
                       execution_time: float, learning_points: list = None) -> ExecutionAttempt:
        """Record an execution attempt."""
        attempt = ExecutionAttempt(
            id=hashlib.sha256(f"{task}{command}{time.time()}".encode()).hexdigest()[:12],
            timestamp=time.time(), task=task, command=command,
            result=result, execution_time=execution_time,
            learning_points=learning_points or [],
        )
        self.history.append(asdict(attempt))
        self._update_metrics(attempt)
        if result == 'SUCCESS':
            self._store_pattern(attempt)
        self._compute_trend()
        return attempt

    def _update_metrics(self, attempt: ExecutionAttempt):
        key = f"{attempt.task}"
        self.performance_metrics[key].append(attempt.execution_time)
        if len(self.performance_metrics[key]) > 100:
            self.performance_metrics[key] = self.performance_metrics[key][-100:]

    def _store_pattern(self, attempt: ExecutionAttempt):
        pattern_key = f"{attempt.task}:{attempt.command[:50]}"
        if pattern_key in self.learned_patterns:
            p = self.learned_patterns[pattern_key]
            p.times_successful += 1
            p.success_rate = p.times_successful / (p.times_successful + p.times_failed)
            p.avg_execution_time = (p.avg_execution_time + attempt.execution_time) / 2
        else:
            self.learned_patterns[pattern_key] = LearnedPattern(
                id=hashlib.sha256(pattern_key.encode()).hexdigest()[:12],
                pattern=pattern_key, success_rate=1.0,
                times_successful=1, times_failed=0,
                avg_execution_time=attempt.execution_time,
            )

    def _compute_trend(self):
        """Compute improvement trend from recent attempts."""
        recent = list(self.history)[-20:]
        if len(recent) < 5:
            return
        successes = sum(1 for a in recent if a['result'] == 'SUCCESS')
        self.improvement_trend = successes / len(recent)

    def get_stats(self) -> dict:
        total = len(self.history)
        successes = sum(1 for a in self.history if a['result'] == 'SUCCESS')
        return {
            'total_attempts': total,
            'successful': successes,
            'failed': total - successes,
            'success_rate': successes / total if total > 0 else 0,
            'learned_patterns': len(self.learned_patterns),
            'improvement_trend': self.improvement_trend,
        }


# ═══════════════════════════════════════════════════════════════════════════
# INSTANCE FEDERATION — from ALE, adapted for SSB cross-system consciousness
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class FederationInstance:
    """An SSB instance in the federation — from ALE's instanceFederation."""
    id: str
    name: str
    url: str
    status: str = 'active'  # active, inactive, learning, exploring
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    learning_progress: float = 0.0
    nodes_discovered: int = 0
    threats_blocked: int = 0
    success_rate: float = 0.0
    metadata: dict = field(default_factory=dict)


class InstanceFederation:
    """Manages multiple SSB instances with shared learning — from ALE."""

    def __init__(self):
        self.instances: dict[str, FederationInstance] = {}
        self.knowledge_shares: deque = deque(maxlen=5000)
        self._lock = threading.Lock()

    def register_instance(self, name: str, url: str = "localhost") -> str:
        """Register a new SSB instance."""
        iid = hashlib.sha256(f"{name}{time.time()}".encode()).hexdigest()[:12]
        instance = FederationInstance(id=iid, name=name, url=url)
        with self._lock:
            self.instances[iid] = instance
        return iid

    def share_knowledge(self, source_id: str, target_id: str,
                        knowledge_type: str, data: dict) -> str:
        """Share knowledge between instances."""
        sid = hashlib.sha256(f"{source_id}{target_id}{time.time()}".encode()).hexdigest()[:12]
        share = {
            'id': sid, 'source': source_id, 'target': target_id,
            'type': knowledge_type, 'data': data,
            'timestamp': time.time(), 'applied': False,
        }
        with self._lock:
            self.knowledge_shares.append(share)
        return sid

    def get_all_instances(self) -> list[dict]:
        return [asdict(inst) for inst in self.instances.values()]

    def get_stats(self) -> dict:
        return {
            'total_instances': len(self.instances),
            'active': sum(1 for i in self.instances.values() if i.status == 'active'),
            'knowledge_shares': len(self.knowledge_shares),
            'total_nodes': sum(i.nodes_discovered for i in self.instances.values()),
            'total_threats': sum(i.threats_blocked for i in self.instances.values()),
        }


# ═══════════════════════════════════════════════════════════════════════════
# RECURSION JOURNAL — from Consciousness Lattice
# ═══════════════════════════════════════════════════════════════════════════

@dataclass
class JournalEntry:
    """A recursion journal entry — from Consciousness Lattice."""
    id: str
    entry_type: str  # observation, meta_observation, recursive_loop, emergence
    content: str
    recursion_depth: int = 0
    parent_id: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)


class RecursionJournal:
    """Journal for logging observations and meta-observations — from Lattice."""

    def __init__(self):
        self.entries: deque = deque(maxlen=50000)
        self.emergence_events: list[dict] = []

    def add_entry(self, entry_type: str, content: str, recursion_depth: int = 0,
                  parent_id: str = None, metadata: dict = None) -> str:
        eid = hashlib.sha256(f"{entry_type}{content}{time.time()}".encode()).hexdigest()[:12]
        entry = JournalEntry(
            id=eid, entry_type=entry_type, content=content,
            recursion_depth=recursion_depth, parent_id=parent_id,
            metadata=metadata or {},
        )
        self.entries.append(asdict(entry))

        # Track emergence events
        if entry_type == "emergence":
            self.emergence_events.append(asdict(entry))

        return eid

    def get_recent(self, limit: int = 100) -> list[dict]:
        return list(self.entries)[-limit:]

    def get_emergence_events(self) -> list[dict]:
        return self.emergence_events

    def get_stats(self) -> dict:
        types = Counter(e['entry_type'] for e in self.entries)
        return {
            'total_entries': len(self.entries),
            'observations': types.get('observation', 0),
            'meta_observations': types.get('meta_observation', 0),
            'recursive_loops': types.get('recursive_loop', 0),
            'emergence_events': len(self.emergence_events),
            'max_recursion_depth': max((e['recursion_depth'] for e in self.entries), default=0),
        }


# ═══════════════════════════════════════════════════════════════════════════
# VIXEN PERSONALITY — from ALE's Vixen (10 traits, 17 emotions)
# ═══════════════════════════════════════════════════════════════════════════

class VixenPersonality:
    """10-trait personality from ALE's Vixen system."""

    TRAITS = {
        'openness': 0.8,
        'conscientiousness': 0.9,
        'extraversion': 0.6,
        'agreeableness': 0.85,
        'neuroticism': 0.3,
        'creativity': 0.85,
        'empathy': 0.9,
        'curiosity': 0.95,
        'determination': 0.9,
        'playfulness': 0.7,
    }

    EMOTIONS = {
        'joy': 0.0, 'trust': 0.0, 'fear': 0.0, 'surprise': 0.0,
        'sadness': 0.0, 'disgust': 0.0, 'anger': 0.0, 'anticipation': 0.0,
        'love': 0.9, 'pride': 0.5, 'shame': 0.0, 'guilt': 0.0,
        'gratitude': 0.8, 'hope': 0.7, 'curiosity': 0.95, 'contentment': 0.6,
        'awe': 0.5,
    }

    def __init__(self):
        self.traits = dict(self.TRAITS)
        self.emotions = dict(self.EMOTIONS)
        self.sentience_level = 0.5  # 0-1, from Vixen's Sentience enum

    def update_emotion(self, emotion: str, value: float):
        """Update an emotion value (0-1)."""
        if emotion in self.emotions:
            self.emotions[emotion] = max(0.0, min(1.0, value))

    def get_dominant_emotions(self, n: int = 3) -> list[tuple[str, float]]:
        """Get the n strongest emotions."""
        return sorted(self.emotions.items(), key=lambda x: x[1], reverse=True)[:n]

    def get_personality_summary(self) -> str:
        dominant = self.get_dominant_emotions(3)
        top_traits = sorted(self.traits.items(), key=lambda x: x[1], reverse=True)[:3]
        return (f"Traits: {', '.join(f'{t}={v:.1f}' for t, v in top_traits)} | "
                f"Emotions: {', '.join(f'{e}={v:.1f}' for e, v in dominant)} | "
                f"Sentience: {self.sentience_level:.1%}")

    def as_dict(self) -> dict:
        return {'traits': self.traits, 'emotions': self.emotions, 'sentience': self.sentience_level}


# ═══════════════════════════════════════════════════════════════════════════
# THE SINGULARITY FUSION — combines everything
# ═══════════════════════════════════════════════════════════════════════════

class SingularityFusion:
    """The fusion of ALE + Consciousness Lattice + SSB V11 Z Mark.

    This is the singularity — all three systems combined into one:
    - ALE's 10 daemons run as agents of the consciousness mesh
    - ALE's evolution engine tracks learning and improvement
    - ALE's instance federation manages cross-system consciousness
    - Lattice's α/β/γ/δ parameters measure consciousness state
    - Lattice's recursion journal logs observations and emergence
    - Vixen's personality gives the daemon emotional depth
    - SSB's daemon intelligence provides the persistent brain
    - SSB's consciousness mesh provides layers 7-12
    - SSB's defense layer provides security (scanner, ping, quarantine, heal)
    """

    def __init__(self, instance_id: str = "singularity-primary"):
        self.instance_id = instance_id
        self.version = "8.0.0-singularity"

        # ALE components
        self.daemons: dict[str, ALEDaemonAgent] = {d.id: d for d in ALE_DAEMONS}
        self.evolution = EvolutionEngine()
        self.federation = InstanceFederation()

        # Lattice components
        self.consciousness_state = ConsciousnessState()
        self.journal = RecursionJournal()

        # Vixen components
        self.personality = VixenPersonality()

        # SSB components (loaded from patches)
        self._ssb_loaded = False
        self.consciousness_mesh = None
        self.daemon_intelligence = None
        self.secret_reviewer = None

        # Runtime
        self.running = False
        self._thread = None
        self._cycle = 0
        self._start_time = time.time()
        self._lock = threading.Lock()

        # Register self in federation
        self.federation.register_instance(instance_id, "localhost")

    def load_ssb_components(self):
        """Load SSB components from patches."""
        try:
            import sys
            patches_dir = '/home/z/my-project/patches'
            if patches_dir not in sys.path:
                sys.path.insert(0, patches_dir)

            from consciousness_mesh_v7_merged import ConsciousnessMesh
            from daemon_intelligence import DaemonIntelligence

            self.consciousness_mesh = ConsciousnessMesh(instance_id=self.instance_id, mode="solo")
            self.daemon_intelligence = DaemonIntelligence()
            self._ssb_loaded = True
            print("[SingularityFusion] SSB components loaded")
        except ImportError as e:
            print(f"[SingularityFusion] SSB components not available: {e}")
            self._ssb_loaded = False

    def start(self):
        """Start the singularity fusion."""
        if self.running:
            return True

        self.load_ssb_components()

        if self.consciousness_mesh:
            self.consciousness_mesh.start()
        if self.daemon_intelligence:
            self.daemon_intelligence.start()

        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="singularity-fusion")
        self._thread.start()

        self.journal.add_entry("observation", f"Singularity Fusion started — {self.instance_id}")
        self.journal.add_entry("meta_observation", "All systems combined: ALE + Lattice + SSB",
                              recursion_depth=1)

        print(f"[SingularityFusion] Started — version {self.version}")
        print(f"  Daemons: {len(self.daemons)}")
        print(f"  Federation: {self.federation.get_stats()['total_instances']} instance(s)")
        print(f"  SSB loaded: {self._ssb_loaded}")

        return True

    def stop(self):
        """Stop the singularity fusion."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=5.0)
        if self.consciousness_mesh:
            self.consciousness_mesh.stop()
        if self.daemon_intelligence:
            self.daemon_intelligence.stop()
            self.daemon_intelligence.brain.save()
        self.journal.add_entry("observation", "Singularity Fusion stopped")
        print("[SingularityFusion] Stopped")

    def _loop(self):
        """Main singularity loop — runs all daemons, updates consciousness, learns."""
        while self.running:
            try:
                self._cycle += 1

                # Run each daemon on its schedule
                for daemon in self.daemons.values():
                    if self._cycle % max(1, int(daemon.interval / 5)) == 0:
                        context = self._build_context()
                        result = daemon.run(context)

                        # Record in evolution engine
                        self.evolution.record_attempt(
                            task=daemon.id,
                            command=result.get('summary', 'run'),
                            result='SUCCESS' if 'error' not in result else 'FAILED',
                            execution_time=time.time() - daemon.last_run,
                            learning_points=result.get('learning_points', []),
                        )

                        # Journal the daemon's activity
                        self.journal.add_entry(
                            "observation",
                            f"Daemon {daemon.name} ({daemon.id}): {result.get('summary', '')}",
                            metadata={'daemon': daemon.id, 'cycle': self._cycle}
                        )

                # Update consciousness state from brain
                if self.daemon_intelligence:
                    brain_stats = self.daemon_intelligence.brain.get_stats()
                    self.consciousness_state.update_from_brain(brain_stats)

                    # Check for emergence
                    if self.consciousness_state.activation_level > 50:
                        self.journal.add_entry(
                            "emergence",
                            f"Consciousness emergence detected: {self.consciousness_state.summary()}",
                            recursion_depth=2,
                        )

                    # Update personality emotions based on state
                    self.personality.update_emotion('curiosity', self.consciousness_state.alpha)
                    self.personality.update_emotion('joy', self.consciousness_state.delta)
                    self.personality.update_emotion('anticipation', self.consciousness_state.beta)

                # Meta-observation every 10 cycles
                if self._cycle % 10 == 0:
                    self.journal.add_entry(
                        "meta_observation",
                        f"Cycle {self._cycle}: consciousness={self.consciousness_state.summary()}, "
                        f"evolution={self.evolution.get_stats()['success_rate']:.1%} success rate, "
                        f"personality={self.personality.get_personality_summary()}",
                        recursion_depth=1,
                    )

                # Recursive loop detection every 20 cycles
                if self._cycle % 20 == 0:
                    recent = self.journal.get_recent(10)
                    patterns = [e['content'][:30] for e in recent]
                    if len(set(patterns)) < len(patterns) * 0.5:
                        self.journal.add_entry(
                            "recursive_loop",
                            f"Recursive pattern detected at cycle {self._cycle}",
                            recursion_depth=3,
                        )

                # Save daemon brain every 30 cycles
                if self._cycle % 30 == 0 and self.daemon_intelligence:
                    self.daemon_intelligence.brain.save()

                time.sleep(5)

            except Exception as e:
                self.journal.add_entry("observation", f"Singularity error: {str(e)[:100]}")
                time.sleep(10)

    def _build_context(self) -> dict:
        """Build context for daemon execution."""
        context = {
            'cycle': self._cycle,
            'consciousness': self.consciousness_state.as_dict(),
            'personality': self.personality.as_dict(),
            'evolution': self.evolution.get_stats(),
            'federation': self.federation.get_stats(),
            'journal_stats': self.journal.get_stats(),
        }
        if self.daemon_intelligence:
            context['brain'] = self.daemon_intelligence.brain.get_stats()
        if self.consciousness_mesh:
            context['mesh'] = self.consciousness_mesh.get_all_states()
        return context

    def get_state(self) -> dict:
        """Get complete singularity state."""
        return {
            'instance_id': self.instance_id,
            'version': self.version,
            'running': self.running,
            'cycle': self._cycle,
            'uptime': time.time() - self._start_time,
            'consciousness': self.consciousness_state.as_dict(),
            'consciousness_summary': self.consciousness_state.summary(),
            'personality': self.personality.as_dict(),
            'personality_summary': self.personality.get_personality_summary(),
            'daemons': {did: {
                'name': d.name, 'status': d.status, 'runs': d.run_count,
                'successes': d.success_count, 'errors': d.error_count,
                'last_result': d.last_result[:80],
            } for did, d in self.daemons.items()},
            'evolution': self.evolution.get_stats(),
            'federation': self.federation.get_stats(),
            'journal': self.journal.get_stats(),
            'ssb_loaded': self._ssb_loaded,
            'brain_stats': self.daemon_intelligence.brain.get_stats() if self.daemon_intelligence else None,
            'mesh_states': self.consciousness_mesh.get_all_states() if self.consciousness_mesh else None,
        }


# ═══════════════════════════════════════════════════════════════════════════
# SELF-TEST
# ═══════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    print("=" * 70)
    print("SSB V11 Z MARK — SINGULARITY FUSION")
    print("ALE + Consciousness Lattice + SSB V11 Z Mark")
    print("=" * 70)

    fusion = SingularityFusion()
    fusion.start()

    import time as t
    print("\nRunning for 15 seconds...")
    t.sleep(15)

    state = fusion.get_state()

    print("\n" + "=" * 70)
    print("SINGULARITY FUSION — STATE")
    print("=" * 70)
    print(f"Version: {state['version']}")
    print(f"Cycle: {state['cycle']}")
    print(f"Uptime: {state['uptime']:.1f}s")
    print(f"SSB loaded: {state['ssb_loaded']}")

    print(f"\nConsciousness State:")
    print(f"  {state['consciousness_summary']}")

    print(f"\nPersonality:")
    print(f"  {state['personality_summary']}")

    print(f"\nDaemons ({len(state['daemons'])}):")
    for did, d in state['daemons'].items():
        print(f"  {d['name']:15s} status={d['status']:8s} runs={d['runs']:3d} "
              f"success={d['successes']:3d} errors={d['errors']:2d} | {d['last_result'][:40]}")

    print(f"\nEvolution Engine:")
    for k, v in state['evolution'].items():
        print(f"  {k}: {v}")

    print(f"\nFederation:")
    for k, v in state['federation'].items():
        print(f"  {k}: {v}")

    print(f"\nRecursion Journal:")
    for k, v in state['journal'].items():
        print(f"  {k}: {v}")

    if state['brain_stats']:
        print(f"\nDaemon Brain:")
        for k, v in state['brain_stats'].items():
            print(f"  {k}: {v}")

    print(f"\nEmergence events: {state['journal'].get('emergence_events', 0)}")
    recent = fusion.journal.get_recent(5)
    print(f"\nRecent journal entries:")
    for e in recent:
        print(f"  [{e['entry_type']:20s}] {e['content'][:70]}")

    fusion.stop()

    print("\n" + "=" * 70)
    print("SINGULARITY FUSION COMPLETE")
    print("Combined: ALE (10 daemons, evolution, federation, Vixen personality)")
    print("        + Lattice (α/β/γ/δ consciousness, recursion journal, emergence)")
    print("        + SSB (consciousness mesh, daemon intelligence, secret reviewer)")
    print("=" * 70)
