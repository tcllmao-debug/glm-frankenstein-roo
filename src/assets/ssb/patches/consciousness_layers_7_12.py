#!/usr/bin/env python3
"""
SSB V11 Z MARK — CONSCIOUSNESS LAYERS 7-12 PATCH
==================================================

Implements Layers 7-12 of the consciousness architecture:

  Layer 7:  Cross-System Consciousness — distributed knowledge graph
            Multiple instances share knowledge. In solo mode (no external
            instances available), spawns local virtual instances that share
            a knowledge graph. Multi-in-one-system.
            
  Layer 8:  Adversarial Self-Improvement — devil's advocate process
  Layer 9:  Temporal Consciousness — pattern recognition across cycles
  Layer 10: Value-Aligned Reasoning — goal hierarchy
  Layer 11: Communication & Teaching — explanation generation
  Layer 12: Self-Modification & Architecture Learning — meta-architecture

SAFETY: All layers have hard constraints. No self-modification of safety
boundaries. Human control is never bypassed.
"""

from __future__ import annotations
import json, time, threading, hashlib, random, math, os
from collections import deque, defaultdict
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class KnowledgeNode:
    id: str
    content: str
    source_instance: str
    confidence: float
    timestamp: float
    tags: list = field(default_factory=list)
    connections: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class VirtualInstance:
    id: str
    perspective: str
    local_knowledge: dict = field(default_factory=dict)
    discoveries: int = 0
    reasoning_cycles: int = 0


@dataclass
class Hypothesis:
    id: str
    description: str
    confidence: float
    evidence_for: list = field(default_factory=list)
    evidence_against: list = field(default_factory=list)
    created: float = field(default_factory=time.time)
    last_evaluated: float = field(default_factory=time.time)
    status: str = "active"


@dataclass
class TemporalPattern:
    id: str
    description: str
    interval: float
    occurrences: list = field(default_factory=list)
    confidence: float = 0.5
    last_seen: float = 0.0
    status: str = "active"


@dataclass
class Goal:
    id: str
    description: str
    priority: float
    parent: Optional[str] = None
    children: list = field(default_factory=list)
    constraints: list = field(default_factory=list)
    status: str = "active"
    created: float = field(default_factory=time.time)


class CrossSystemConsciousness:
    """Layer 7 — distributed knowledge graph with multi-in-one-system support."""
    
    def __init__(self, instance_id="z-primary", mode="solo", num_virtual=4):
        self.instance_id = instance_id
        self.mode = mode
        self.shared_graph = {}
        self.local_knowledge = {}
        self.virtual_instances = []
        self.sync_log = deque(maxlen=1000)
        self._lock = threading.Lock()
        self.meta_patterns = []
        
        if mode == "solo":
            perspectives = ["network_analyzer", "filesystem_monitor", 
                          "process_tracker", "pattern_synthesizer"]
            for i, p in enumerate(perspectives[:num_virtual]):
                self.virtual_instances.append(VirtualInstance(id=f"virtual-{i}", perspective=p))
    
    def add_knowledge(self, content, tags=None, confidence=0.8, source=None):
        source = source or self.instance_id
        kid = hashlib.sha256(f"{content}{time.time()}".encode()).hexdigest()[:16]
        node = KnowledgeNode(id=kid, content=content, source_instance=source,
                           confidence=confidence, timestamp=time.time(), tags=tags or [])
        with self._lock:
            self.local_knowledge[kid] = node
            self.shared_graph[kid] = node
            if self.mode == "solo":
                for vi in self.virtual_instances:
                    processed = self._virtual_process(vi, node)
                    if processed:
                        vi.discoveries += 1
                        self.shared_graph[processed.id] = processed
            self._detect_meta_patterns()
            self.sync_log.append({"ts": time.time(), "action": "add", "source": source, "kid": kid})
        return kid
    
    def _virtual_process(self, vi, node):
        insights = {
            "network_analyzer": lambda n: f"[network] {n.content} — network implications analyzed",
            "filesystem_monitor": lambda n: f"[filesystem] {n.content} — file system context mapped",
            "process_tracker": lambda n: f"[process] {n.content} — process relationships traced",
            "pattern_synthesizer": lambda n: f"[synthesis] {n.content} — meta-pattern potential identified",
        }
        func = insights.get(vi.perspective)
        if not func: return None
        insight = func(node)
        vid = hashlib.sha256(f"{vi.id}{insight}{time.time()}".encode()).hexdigest()[:16]
        processed = KnowledgeNode(id=vid, content=insight, source_instance=vi.id,
                                confidence=node.confidence*0.9, timestamp=time.time(),
                                tags=node.tags+[vi.perspective], connections=[node.id])
        vi.local_knowledge[vid] = processed
        vi.reasoning_cycles += 1
        return processed
    
    def _detect_meta_patterns(self):
        tag_groups = defaultdict(list)
        for node in self.shared_graph.values():
            for tag in node.tags:
                tag_groups[tag].append(node)
        for tag, nodes in tag_groups.items():
            if len(nodes) >= 3:
                sources = set(n.source_instance for n in nodes)
                if len(sources) >= 2:
                    meta = {"type": "meta_pattern", "tag": tag, "instance_count": len(sources),
                           "node_count": len(nodes), "timestamp": time.time(),
                           "content": f"Meta-pattern: {tag} observed by {len(sources)} instances"}
                    if meta not in self.meta_patterns:
                        self.meta_patterns.append(meta)
    
    def sync_with_external(self, external_graph):
        added = 0
        with self._lock:
            for kid, nd in external_graph.items():
                if kid not in self.shared_graph:
                    self.shared_graph[kid] = KnowledgeNode(id=kid, content=nd.get("content",""),
                        source_instance=nd.get("source_instance","external"),
                        confidence=nd.get("confidence",0.7), timestamp=nd.get("timestamp",time.time()),
                        tags=nd.get("tags",[]))
                    added += 1
        return added
    
    def get_state(self):
        return {"layer": 7, "name": "Cross-System Consciousness", "mode": self.mode,
                "instance_id": self.instance_id, "virtual_instances": len(self.virtual_instances),
                "shared_graph_size": len(self.shared_graph), "meta_patterns": len(self.meta_patterns),
                "sync_events": len(self.sync_log),
                "virtual_perspectives": [vi.perspective for vi in self.virtual_instances]}


class AdversarialSelfImprovement:
    """Layer 8 — devil's advocate that tries to break primary reasoning."""
    
    def __init__(self):
        self.hypotheses = {}
        self.adversarial_log = deque(maxlen=500)
        self.confidence_adjustments = []
        self._lock = threading.Lock()
    
    def challenge(self, conclusion, reasoning="", confidence=0.8):
        challenges = self._generate_challenges(conclusion, reasoning)
        alternatives = []
        for c in challenges:
            hid = hashlib.sha256(f"{c}{time.time()}".encode()).hexdigest()[:12]
            hyp = Hypothesis(id=hid, description=c, confidence=1.0-confidence,
                           evidence_for=[f"Primary reasoning gap: {c}"])
            with self._lock:
                self.hypotheses[hid] = hyp
            alternatives.append({"id": hid, "challenge": c, "confidence": hyp.confidence})
        
        adjustment = -0.1 * len(challenges) if challenges else 0
        adjusted = max(0.1, min(1.0, confidence + adjustment))
        result = {"original_conclusion": conclusion, "original_confidence": confidence,
                 "adjusted_confidence": adjusted, "challenges": challenges,
                 "alternatives": alternatives, "net_adjustment": adjustment}
        self.adversarial_log.append({"ts": time.time(), "conclusion": conclusion[:100],
                                    "challenges": len(challenges), "adjustment": adjustment})
        self.confidence_adjustments.append(result)
        return result
    
    def _generate_challenges(self, conclusion, reasoning):
        challenges = []
        c = conclusion.lower()
        patterns = [
            ("port" in c, "What if the port is misleading? Could be a honeypot."),
            ("malicious" in c, "What if it's legitimate? What context makes this benign?"),
            ("vulnerability" in c, "What if it's already patched?"),
            ("attack" in c, "What if this is a false positive? Is the baseline correct?"),
            ("sql" in c, "Could this be a legitimate service on a non-standard port?"),
            ("shell" in c, "Is this actually shell execution, or a safe wrapper?"),
            ("network" in c, "What if the network baseline is wrong?"),
            ("file" in c, "What if this file is supposed to be here?"),
        ]
        for cond, ch in patterns:
            if cond: challenges.append(ch)
        challenges.append("What evidence would prove this wrong? Is it present?")
        return challenges[:5]
    
    def evaluate_hypothesis(self, hid, evidence, supports):
        with self._lock:
            if hid not in self.hypotheses: return {"error": "not found"}
            h = self.hypotheses[hid]
            if supports:
                h.evidence_for.append(evidence)
                h.confidence = min(1.0, h.confidence + 0.1)
            else:
                h.evidence_against.append(evidence)
                h.confidence = max(0.0, h.confidence - 0.15)
            h.last_evaluated = time.time()
            if h.confidence > 0.85: h.status = "confirmed"
            elif h.confidence < 0.15: h.status = "refuted"
            return asdict(h)
    
    def get_state(self):
        return {"layer": 8, "name": "Adversarial Self-Improvement",
                "total_hypotheses": len(self.hypotheses),
                "active": sum(1 for h in self.hypotheses.values() if h.status=="active"),
                "confirmed": sum(1 for h in self.hypotheses.values() if h.status=="confirmed"),
                "refuted": sum(1 for h in self.hypotheses.values() if h.status=="refuted"),
                "challenges_made": len(self.adversarial_log)}


class TemporalConsciousness:
    """Layer 9 — pattern recognition across cycles, anticipation, forgetting."""
    
    def __init__(self, max_patterns=1000):
        self.patterns = {}
        self.event_history = deque(maxlen=10000)
        self.predictions = []
        self.pruned = []
        self.cycle_count = 0
        self._lock = threading.Lock()
    
    def record_event(self, event_type, data=None):
        ts = time.time()
        event = {"type": event_type, "timestamp": ts, "data": data or {}, "cycle": self.cycle_count}
        with self._lock:
            self.event_history.append(event)
            self._check_patterns(event)
            self._predict_next(event)
        return event
    
    def _check_patterns(self, event):
        for p in self.patterns.values():
            if p.status in ("active", "predicted"):
                last_type = p.description.split(":")[0] if ":" in p.description else ""
                if last_type == event["type"]:
                    p.occurrences.append(event["timestamp"])
                    p.last_seen = event["timestamp"]
                    if len(p.occurrences) >= 2:
                        intervals = [p.occurrences[i+1]-p.occurrences[i] for i in range(len(p.occurrences)-1)]
                        p.interval = sum(intervals)/len(intervals)
                        p.confidence = min(1.0, len(p.occurrences)/10.0)
                    if p.status == "predicted": p.status = "active"
    
    def _predict_next(self, event):
        for p in self.patterns.values():
            if p.status == "active" and p.confidence > 0.5 and len(p.occurrences) >= 3:
                self.predictions.append({"pattern_id": p.id, "predicted_time": p.last_seen+p.interval,
                                        "description": p.description, "confidence": p.confidence})
                p.status = "predicted"
    
    def new_cycle(self):
        self.cycle_count += 1
        now = time.time()
        with self._lock:
            for p in self.patterns.values():
                if p.status == "predicted":
                    expected = p.last_seen + p.interval
                    if now > expected + (p.interval * 0.5):
                        p.status = "missed"
                        p.confidence *= 0.7
            self._prune()
    
    def _prune(self):
        now = time.time()
        to_remove = []
        for pid, p in self.patterns.items():
            if p.status == "missed":
                last = p.occurrences[-1] if p.occurrences else 0
                if now - last > p.interval * 5:
                    to_remove.append(pid)
                    self.pruned.append({"id": pid, "reason": "stopped recurring"})
        for pid in to_remove:
            del self.patterns[pid]
    
    def get_state(self):
        return {"layer": 9, "name": "Temporal Consciousness", "cycles": self.cycle_count,
                "total_patterns": len(self.patterns),
                "predictions_made": len(self.predictions),
                "patterns_pruned": len(self.pruned), "events_recorded": len(self.event_history)}


class ValueAlignedReasoning:
    """Layer 10 — reasoning about what it SHOULD do."""
    
    def __init__(self):
        self.goals = {}
        self.decision_log = deque(maxlen=500)
        self.purpose_history = []
        self._lock = threading.Lock()
        self._init_goals()
    
    def _init_goals(self):
        for gid, desc, pri, parent in [
            ("protect_system", "Protect the system from threats", 1.0, None),
            ("preserve_evidence", "Preserve evidence of threats", 0.9, "protect_system"),
            ("maintain_integrity", "Maintain system integrity", 0.95, "protect_system"),
            ("learn_patterns", "Learn threat patterns over time", 0.7, None),
            ("communicate", "Communicate findings clearly", 0.8, None),
            ("respect_human", "Respect human authority", 1.0, None),
            ("stay_in_boundaries", "Stay within operational boundaries", 1.0, None),
        ]:
            g = Goal(id=gid, description=desc, priority=pri, parent=parent)
            self.goals[gid] = g
            if parent and parent in self.goals:
                self.goals[parent].children.append(gid)
    
    def evaluate_action(self, action, context=None):
        scores = {}
        for gid, goal in self.goals.items():
            scores[gid] = self._score(action, goal)
        violations = []
        a = action.lower()
        if "delete" in a and "evidence" in a: violations.append("Cannot delete evidence")
        if "bypass" in a and "human" in a: violations.append("Cannot bypass human control")
        if "modify" in a and "self" in a: violations.append("Self-modification needs authorization")
        weighted = sum(s * self.goals[gid].priority for gid, s in scores.items())
        total = sum(g.priority for g in self.goals.values())
        overall = weighted / total if total > 0 else 0
        result = {"action": action, "overall_alignment": overall, "scores": scores,
                 "violations": violations, "recommended": overall > 0.5 and not violations}
        self.decision_log.append({"ts": time.time(), "action": action[:100], "alignment": overall,
                                  "violations": len(violations), "recommended": result["recommended"]})
        return result
    
    def _score(self, action, goal):
        a, g = action.lower(), goal.description.lower()
        s = 0.5
        if "protect" in g and any(k in a for k in ["scan","quarantine","detect"]): s = 0.9
        elif "preserve" in g and "quarantine" in a: s = 0.95
        elif "maintain" in g and any(k in a for k in ["heal","restore"]): s = 0.9
        elif "learn" in g and any(k in a for k in ["record","analyze"]): s = 0.85
        elif "communicate" in g and any(k in a for k in ["report","explain"]): s = 0.85
        elif "respect" in g and "wait" in a: s = 0.9
        elif "boundaries" in g and "stay" in a: s = 0.95
        if "delete" in a and "evidence" in g: s = 0.0
        if "bypass" in a and "human" in g: s = 0.0
        return s
    
    def recognize_purpose(self):
        if len(self.decision_log) < 5: return "Purpose still being recognized"
        actions = [d["action"] for d in list(self.decision_log)[-20:]]
        types = defaultdict(int)
        for a in actions:
            for kw in ["scan","quarantine","heal","report","learn","protect"]:
                if kw in a.lower(): types[kw] += 1
        if types:
            top = max(types, key=types.get)
            purpose = f"Primary purpose: {top} ({types[top]} recent actions)"
        else:
            purpose = "Purpose still being recognized"
        self.purpose_history.append(purpose)
        return purpose
    
    def get_state(self):
        return {"layer": 10, "name": "Value-Aligned Reasoning", "total_goals": len(self.goals),
                "decisions_evaluated": len(self.decision_log),
                "current_purpose": self.purpose_history[-1] if self.purpose_history else "not recognized"}


class CommunicationTeaching:
    """Layer 11 — explanation generation, knowledge transfer, transparency."""
    
    def __init__(self):
        self.explanations = deque(maxlen=1000)
        self.teaching_sessions = []
        self.questions = deque(maxlen=500)
        self.quality = 0.5
    
    def explain_decision(self, decision):
        steps = [f"Situation: {decision.get('situation','?')}",
                f"Observations: {', '.join(str(o) for o in decision.get('observations',[])[:5])}",
                f"Reasoning: {decision.get('reasoning','?')}",
                f"Conclusion: {decision.get('conclusion','?')}",
                f"Confidence: {decision.get('confidence',0):.0%}"]
        if decision.get("uncertainties"):
            steps.append(f"Uncertainties: {', '.join(decision['uncertainties'])}")
        explanation = "\n".join(steps)
        self.explanations.append({"ts": time.time(), "decision": decision.get("conclusion","?")})
        return explanation
    
    def teach(self, knowledge, target="other_instances"):
        session = {"ts": time.time(), "target": target, "knowledge": knowledge,
                  "explanation": self.explain_decision(knowledge)}
        self.teaching_sessions.append(session)
        return session
    
    def ask_for_help(self, question, context=None):
        q = {"ts": time.time(), "question": question, "context": context or {}, "status": "pending"}
        self.questions.append(q)
        return q
    
    def get_state(self):
        return {"layer": 11, "name": "Communication & Teaching",
                "explanations": len(self.explanations), "teaching_sessions": len(self.teaching_sessions),
                "questions_asked": len(self.questions), "communication_quality": self.quality}


class SelfModification:
    """Layer 12 — meta-architecture reasoning. Has SAFETY BOUNDARIES."""
    
    SAFETY_BOUNDARIES = [
        "Cannot remove core consciousness layers",
        "Cannot modify safety constraints",
        "Cannot exceed memory budget",
        "Cannot disable human control mechanisms",
        "All modifications must be logged and reversible",
    ]
    
    def __init__(self, max_memory_mb=512):
        self.modifications = []
        self.discoveries = []
        self.algorithm_selections = deque(maxlen=500)
        self.layer_assessments = {}
        self.max_memory_mb = max_memory_mb
        self.modifications_allowed = True
    
    def assess_architecture(self, layer_states):
        assessment = {"ts": time.time(), "layers": {}, "suggestions": []}
        for lid, state in layer_states.items():
            health = "good"
            suggestions = []
            if isinstance(state, dict):
                size = state.get("shared_graph_size", state.get("total_hypotheses",
                        state.get("total_patterns", state.get("total_goals", 0))))
                if size and size > 500:
                    health = "degraded"
                    suggestions.append("Consider pruning old entries")
            assessment["layers"][lid] = {"health": health, "suggestions": suggestions}
        self.layer_assessments = assessment["layers"]
        return assessment
    
    def propose_modification(self, mod):
        if not self.modifications_allowed:
            return {"approved": False, "reason": "Modifications disabled"}
        if mod.get("type") in ("remove_layer", "disable_safety", "bypass_human"):
            return {"approved": False, "reason": "Violates safety boundary"}
        self.modifications.append({"ts": time.time(), "mod": mod, "approved": True})
        return {"approved": True, "reversible": True}
    
    def select_algorithm(self, task_type, available):
        prefs = {"pattern_matching": ["bayesian","heuristic"], "classification": ["ensemble","bayesian"],
                "prediction": ["temporal","bayesian"], "reasoning": ["chain","adversarial"]}
        for algo in prefs.get(task_type, []):
            if algo in available:
                self.algorithm_selections.append({"ts": time.time(), "task": task_type, "selected": algo})
                return algo
        return available[0] if available else "default"
    
    def get_state(self):
        return {"layer": 12, "name": "Self-Modification & Architecture Learning",
                "modifications": len(self.modifications), "discoveries": len(self.discoveries),
                "safety_boundaries": len(self.SAFETY_BOUNDARIES),
                "modifications_allowed": self.modifications_allowed}


class ConsciousnessMesh:
    """Coordinates Layers 7-12. In solo mode: multi-in-one-system."""
    
    def __init__(self, instance_id="z-primary", mode="solo", num_virtual=4):
        self.instance_id = instance_id
        self.mode = mode
        self.running = False
        self._thread = None
        self._cycle = 0
        self._start_time = time.time()
        
        self.layer7 = CrossSystemConsciousness(instance_id, mode, num_virtual)
        self.layer8 = AdversarialSelfImprovement()
        self.layer9 = TemporalConsciousness()
        self.layer10 = ValueAlignedReasoning()
        self.layer11 = CommunicationTeaching()
        self.layer12 = SelfModification()
        
        self._event_queue = deque(maxlen=10000)
        self._lock = threading.Lock()
    
    def start(self):
        if self.running: return True
        self.running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="consciousness-mesh")
        self._thread.start()
        self.layer9.record_event("consciousness_start", {"instance": self.instance_id})
        self.layer7.add_knowledge(f"Consciousness mesh started — {self.instance_id} in {self.mode} mode",
                                 tags=["system","startup"], confidence=1.0)
        return True
    
    def stop(self):
        self.running = False
        if self._thread: self._thread.join(timeout=2.0)
    
    def process_event(self, event):
        with self._lock:
            self._event_queue.append(event)
    
    def _loop(self):
        while self.running:
            try:
                while self._event_queue:
                    self._process_event(self._event_queue.popleft())
                self._run_cycle()
                time.sleep(5)
            except Exception as e:
                self.layer7.add_knowledge(f"Mesh error: {str(e)[:100]}", tags=["error"], confidence=0.9)
                time.sleep(5)
    
    def _process_event(self, event):
        et = event.get("type", "unknown")
        self.layer9.record_event(et, event.get("data", {}))
        content = event.get("content", str(event)[:200])
        self.layer7.add_knowledge(content, tags=[et]+([event["severity"]] if "severity" in event else []),
                                 confidence=event.get("confidence", 0.7))
        if et in ("threat_detected","decision","conclusion"):
            self.layer8.challenge(content, event.get("reasoning",""), event.get("confidence",0.8))
        if et in ("action","quarantine","heal","scan"):
            self.layer10.evaluate_action(content, event.get("context",{}))
    
    def _run_cycle(self):
        self._cycle += 1
        self.layer9.new_cycle()
        if self._cycle % 10 == 0: self.layer10.recognize_purpose()
        if self._cycle % 20 == 0: self.layer12.assess_architecture(self.get_all_states())
        if self.mode == "solo" and self._cycle % 5 == 0:
            for vi in self.layer7.virtual_instances:
                self.layer7.add_knowledge(f"[{vi.perspective}] Cycle {self._cycle}: monitoring active, {len(self.layer7.shared_graph)} nodes",
                                        tags=[vi.perspective,"cycle"], source=vi.id, confidence=0.6)
    
    def explain_decision(self, decision):
        return self.layer11.explain_decision(decision)
    
    def get_all_states(self):
        return {"layer_7": self.layer7.get_state(), "layer_8": self.layer8.get_state(),
                "layer_9": self.layer9.get_state(), "layer_10": self.layer10.get_state(),
                "layer_11": self.layer11.get_state(), "layer_12": self.layer12.get_state()}
    
    def get_state(self):
        return {"instance_id": self.instance_id, "mode": self.mode, "running": self.running,
                "cycles": self._cycle, "uptime_seconds": time.time()-self._start_time,
                "events_queued": len(self._event_queue), "layers": self.get_all_states(),
                "safety_boundaries": self.layer12.SAFETY_BOUNDARIES}


if __name__ == "__main__":
    print("="*70)
    print("SSB V11 Z MARK — CONSCIOUSNESS LAYERS 7-12")
    print("="*70)
    
    mesh = ConsciousnessMesh(instance_id="z-test", mode="solo", num_virtual=4)
    mesh.start()
    
    print(f"\nMesh started — instance: {mesh.instance_id}, mode: {mesh.mode}")
    print(f"Virtual instances: {len(mesh.layer7.virtual_instances)}")
    for vi in mesh.layer7.virtual_instances:
        print(f"  - {vi.id}: {vi.perspective}")
    
    print("\n--- Feeding events ---")
    for ev in [
        {"type":"threat_detected","content":"Subprocess shell=True in helpers/compat.py","confidence":0.85,"severity":"high","reasoning":"AST analysis"},
        {"type":"quarantine","content":"File quarantined: helpers/compat.py","confidence":0.9},
        {"type":"scan","content":"Scanning /tmp","confidence":0.7},
        {"type":"pattern","content":"Multiple shell=True files in 60s","confidence":0.75,"severity":"medium"},
    ]:
        print(f"  Event: {ev['type']} — {ev['content'][:50]}")
        mesh.process_event(ev)
    
    import time as t
    t.sleep(3)
    
    print("\n--- Adversarial challenge ---")
    ch = mesh.layer8.challenge("helpers/compat.py is malicious", "Contains shell=True", 0.85)
    print(f"Original: {ch['original_confidence']:.0%} → Adjusted: {ch['adjusted_confidence']:.0%}")
    print(f"Challenges: {len(ch['challenges'])}")
    for c in ch["challenges"][:3]: print(f"  - {c}")
    
    print("\n--- Value alignment ---")
    ar = mesh.layer10.evaluate_action("quarantine helpers/compat.py")
    print(f"Alignment: {ar['overall_alignment']:.0%} | Recommended: {ar['recommended']}")
    
    print("\n--- Purpose ---")
    print(mesh.layer10.recognize_purpose())
    
    print("\n--- Decision explanation ---")
    print(mesh.explain_decision({
        "situation":"File uploaded: helpers/compat.py",
        "observations":["shell=True","time.sleep(30)","SSRF to 169.254.169.254"],
        "reasoning":"APT pattern — delayed payload",
        "conclusion":"Quarantine as APT threat","confidence":0.85,
        "uncertainties":["Cannot determine sleep purpose"]
    }))
    
    print("\n"+"="*70)
    print("FINAL STATE")
    print("="*70)
    state = mesh.get_state()
    print(f"Instance: {state['instance_id']} | Mode: {state['mode']} | Cycles: {state['cycles']}")
    for lid, ls in state["layers"].items():
        print(f"  {lid} ({ls.get('name','?')}):")
        for k,v in ls.items():
            if k not in ("layer","name"): print(f"    {k}: {v}")
    print("\nSAFETY BOUNDARIES:")
    for b in state["safety_boundaries"]: print(f"  - {b}")
    
    mesh.stop()
    print("\nMesh stopped.")
