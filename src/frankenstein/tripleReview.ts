/**
 * SSB Triple Review — TypeScript port of secret_review_v2_openclaw_hermes.py
 *
 * Combines three independent reviewers for each detected secret candidate:
 *   1. AI reviewer        — Shannon entropy + structural analysis
 *   2. OpenClaw reviewer  — conservative: leans "real secret" unless proven otherwise
 *   3. Hermes Beast Claw  — adversarial: tries to prove the secret is a false positive
 *
 * The final classification is the consensus of all three.
 *
 * Source: tcllmao-debug/SSB-Z-MARK-COMPLETE / patches/secret_review_v2_openclaw_hermes.py
 * Adapted for VS Code / Node.js.
 */

export interface SecretCandidate {
  type: string
  value: string
  file: string
  line: number
  context: string
}

export interface EntropyResult {
  entropy: number
  maxEntropy: number
  efficiency: number
  distribution: Record<string, { count: number; probability: number }>
  proof: string
}

export interface ReviewVerdict {
  reviewer: "AI" | "OpenClaw" | "HermesBeastClaw"
  classification: "real_secret" | "false_positive" | "suspicious"
  confidence: number
  reasoning: string
}

export interface TripleReviewResult {
  candidate: SecretCandidate
  entropy: EntropyResult
  reviews: ReviewVerdict[]
  finalClassification: "real_secret" | "false_positive" | "suspicious"
  finalConfidence: number
}

// ---------- Shannon entropy ----------

export function shannonEntropy(value: string): EntropyResult {
  if (!value) {
    return {
      entropy: 0,
      maxEntropy: 0,
      efficiency: 0,
      distribution: {},
      proof: "Empty string — no entropy.",
    }
  }

  const n = value.length
  const freq = new Map<string, number>()
  for (const ch of value) freq.set(ch, (freq.get(ch) ?? 0) + 1)

  const distribution: EntropyResult["distribution"] = {}
  let entropy = 0
  const proofTerms: string[] = []

  const sorted = Array.from(freq.entries()).sort((a, b) => b[1] - a[1])
  for (const [ch, count] of sorted) {
    const p = count / n
    distribution[ch] = { count, probability: p }
    const logTerm = Math.log2(p)
    const contribution = -p * logTerm
    entropy += contribution
    proofTerms.push(
      `  P('${ch}') = ${count}/${n} = ${p.toFixed(4)} → -(${p.toFixed(4)}) × log₂(${p.toFixed(4)}) = ${contribution.toFixed(4)}`,
    )
  }

  const maxEntropy = Math.log2(freq.size) || 0
  const efficiency = maxEntropy > 0 ? (entropy / maxEntropy) * 100 : 0

  let interpretation: string
  if (entropy >= 4.5) {
    interpretation =
      "Very high entropy — consistent with cryptographic material or random data. This is the entropy level expected for real API keys, tokens, and encrypted data."
  } else if (entropy >= 3.5) {
    interpretation = "High entropy — consistent with real secrets, passwords, or encoded data."
  } else if (entropy >= 2.5) {
    interpretation = "Moderate entropy — could be a real secret or a complex placeholder."
  } else {
    interpretation = "Low entropy — likely a placeholder or test value, not a real secret."
  }

  const proof = [
    `Shannon Entropy Proof for: ${value.slice(0, 40)}${value.length > 40 ? "..." : ""}`,
    `  Length: ${n} characters`,
    `  Unique characters: ${freq.size}`,
    `  Formula: H(X) = -Σ p(x) × log₂(p(x))`,
    "",
    "  Computation:",
    ...proofTerms.slice(0, 20),
    ...(proofTerms.length > 20 ? [`  ... and ${proofTerms.length - 20} more terms`] : []),
    "",
    `  H(X) = ${entropy.toFixed(6)} bits/character`,
    `  Maximum possible: ${maxEntropy.toFixed(6)} bits/character (uniform distribution)`,
    `  Efficiency: ${efficiency.toFixed(1)}% of maximum`,
    "",
    `  Interpretation: ${interpretation}`,
  ].join("\n")

  return { entropy, maxEntropy, efficiency, distribution, proof }
}

// ---------- Secret pattern detection ----------

export interface SecretPattern {
  type: string
  pattern: RegExp
  description: string
}

export const SECRET_PATTERNS: SecretPattern[] = [
  { type: "aws_access_key", pattern: /AKIA[0-9A-Z]{16}/g, description: "AWS Access Key ID" },
  { type: "github_token", pattern: /gh[pousr]_[A-Za-z0-9]{36,255}/g, description: "GitHub Personal Access Token" },
  { type: "ssh_private_rsa", pattern: /-----BEGIN RSA PRIVATE KEY-----[\s\S]*?-----END RSA PRIVATE KEY-----/g, description: "SSH RSA Private Key" },
  { type: "ssh_private_ecdsa", pattern: /-----BEGIN EC PRIVATE KEY-----[\s\S]*?-----END EC PRIVATE KEY-----/g, description: "SSH ECDSA Private Key" },
  { type: "ssh_private_ed25519", pattern: /-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]*?-----END OPENSSH PRIVATE KEY-----/g, description: "SSH Ed25519/OpenSSH Private Key" },
  { type: "ssh_private_dsa", pattern: /-----BEGIN DSA PRIVATE KEY-----[\s\S]*?-----END DSA PRIVATE KEY-----/g, description: "SSH DSA Private Key" },
  { type: "pem_block", pattern: /-----BEGIN PRIVATE KEY-----[\s\S]*?-----END PRIVATE KEY-----/g, description: "Generic PEM Private Key" },
  { type: "jwt_token", pattern: /eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, description: "JWT Token" },
  { type: "database_url", pattern: /(postgres|mysql|mongodb|redis|amqp):\/\/[^:\s]+:[^@\s]+@[^\s/]+/g, description: "Database URL with credentials" },
  { type: "bearer_token", pattern: /[Bb]earer\s+[A-Za-z0-9_\-\.=]{20,}/g, description: "Bearer Token" },
  { type: "hex_64", pattern: /\b[a-fA-F0-9]{64}\b/g, description: "256-bit hex (SHA256, API key)" },
  { type: "hex_40", pattern: /\b[a-fA-F0-9]{40}\b/g, description: "160-bit hex (SHA1, AWS secret)" },
  { type: "hex_32", pattern: /\b[a-fA-F0-9]{32}\b/g, description: "128-bit hex (MD5, AWS access)" },
  { type: "generic_api_key", pattern: /(?:api[_-]?key|apikey|secret|token|password|passwd|pwd)\s*[:=]\s*['"]?[A-Za-z0-9_\-\.]{16,}['"]?/gi, description: "Generic API key / password assignment" },
]

export function findSecrets(content: string, file: string): SecretCandidate[] {
  const candidates: SecretCandidate[] = []
  const lines = content.split("\n")

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    for (const { type, pattern } of SECRET_PATTERNS) {
      const globalPattern = new RegExp(pattern.source, pattern.flags)
      let m: RegExpExecArray | null
      while ((m = globalPattern.exec(line)) !== null) {
        const value = m[0]
        // Skip obvious placeholders
        if (/^(example|sample|test|placeholder|your[_-]?key|xxxx|<|foo|bar)/i.test(value)) continue
        if (/^\d+$/.test(value)) continue // pure number
        candidates.push({
          type,
          value,
          file,
          line: i + 1,
          context: line.trim().slice(0, 200),
        })
      }
    }
  }

  return candidates
}

// ---------- Three-reviewer consensus ----------

export function aiReview(candidate: SecretCandidate, entropy: EntropyResult): ReviewVerdict {
  const reasons: string[] = []
  let score = 50

  if (entropy.entropy >= 4.5) {
    reasons.push(`High entropy (${entropy.entropy.toFixed(2)} bits) — strong indicator of randomness.`)
    score += 25
  } else if (entropy.entropy >= 3.5) {
    reasons.push(`Moderate-high entropy (${entropy.entropy.toFixed(2)} bits).`)
    score += 10
  } else {
    reasons.push(`Low entropy (${entropy.entropy.toFixed(2)} bits) — may be a placeholder.`)
    score -= 20
  }

  if (/\.(test|spec)\.(ts|js|py|go|rs)$/.test(candidate.file) || /\/tests?\//.test(candidate.file)) {
    reasons.push("File appears to be a test file — secrets here are often fixtures.")
    score -= 25
  }

  if (/example|sample|demo|fixture|mock/i.test(candidate.file)) {
    reasons.push("File name suggests example/sample data.")
    score -= 20
  }

  if (candidate.type === "github_token" && /^ghp_/.test(candidate.value)) {
    reasons.push("GitHub PAT format is correct (ghp_ prefix + 36+ chars).")
    score += 15
  }

  if (candidate.type === "aws_access_key") {
    reasons.push("AKIA prefix matches AWS Access Key ID format.")
    score += 15
  }

  if (/-----BEGIN .*PRIVATE KEY-----/.test(candidate.value)) {
    reasons.push("PEM private key block detected — very high confidence secret.")
    score += 30
  }

  if (candidate.value.length < 20) {
    reasons.push("Secret value is short — likely a placeholder.")
    score -= 15
  }

  let classification: ReviewVerdict["classification"]
  if (score >= 75) classification = "real_secret"
  else if (score >= 50) classification = "suspicious"
  else classification = "false_positive"

  return {
    reviewer: "AI",
    classification,
    confidence: Math.min(100, Math.max(0, score)),
    reasoning: reasons.join(" "),
  }
}

export function openClawReview(candidate: SecretCandidate, entropy: EntropyResult): ReviewVerdict {
  // OpenClaw: conservative — lean toward "real secret" unless strong evidence otherwise
  const reasons: string[] = []
  let score = 70

  reasons.push("OpenClaw default stance: treat as real until proven otherwise.")

  if (entropy.entropy >= 3.0) {
    reasons.push(`Entropy ${entropy.entropy.toFixed(2)} is high enough to be a real secret.`)
    score += 10
  }

  if (candidate.context.toLowerCase().includes("prod") || candidate.context.toLowerCase().includes("live")) {
    reasons.push("Context mentions 'prod' or 'live' — elevated risk.")
    score += 15
  }

  // Only strong evidence flips OpenClaw to false positive
  if (/(example|sample|test|placeholder|your[_-]?key|fake)/i.test(candidate.value)) {
    reasons.push("Value contains placeholder keywords — strong false-positive signal.")
    score -= 40
  }

  if (/\.(test|spec)\./.test(candidate.file)) {
    reasons.push("Test file context — moderately strong false-positive signal.")
    score -= 15
  }

  let classification: ReviewVerdict["classification"]
  if (score >= 65) classification = "real_secret"
  else if (score >= 45) classification = "suspicious"
  else classification = "false_positive"

  return {
    reviewer: "OpenClaw",
    classification,
    confidence: Math.min(100, Math.max(0, score)),
    reasoning: reasons.join(" "),
  }
}

export function hermesBeastClawReview(candidate: SecretCandidate, entropy: EntropyResult): ReviewVerdict {
  // Hermes Beast Claw: adversarial — actively tries to prove secret is a false positive
  const reasons: string[] = []
  let score = 35

  reasons.push("Hermes Beast Claw default stance: assume false positive until proven real.")

  // Look for any excuse to call it fake
  if (candidate.value.length < 24) {
    reasons.push("Value is shorter than typical real secrets — possible fake.")
    score -= 10
  }

  if (/(example|sample|test|demo|placeholder|your[_-]?key|fake|dummy|xxxx|foo|bar)/i.test(candidate.value)) {
    reasons.push("Value matches placeholder patterns.")
    score -= 30
  }

  if (/\.(test|spec|mock|fixture)\.(ts|js|py|go|rs)$/.test(candidate.file)) {
    reasons.push("File is clearly a test/mock/fixture — secrets here are usually fake.")
    score -= 25
  }

  if (/example|sample|demo|fixture|mock|test/i.test(candidate.file)) {
    reasons.push("File name suggests non-production code.")
    score -= 15
  }

  if (entropy.entropy < 3.0) {
    reasons.push(`Entropy ${entropy.entropy.toFixed(2)} is too low for a real secret.`)
    score -= 20
  }

  // But concede if evidence is overwhelming
  if (entropy.entropy >= 4.5 && candidate.value.length >= 32) {
    reasons.push("However: entropy is very high and length is sufficient — concede this is likely real.")
    score += 35
  }

  if (/-----BEGIN .*PRIVATE KEY-----/.test(candidate.value)) {
    reasons.push("PEM private key block — structural evidence is overwhelming, concede real.")
    score += 40
  }

  if (candidate.type === "aws_access_key" && /^AKIA[A-Z0-9]{16}$/.test(candidate.value)) {
    reasons.push("AWS key format is exact — concede real.")
    score += 30
  }

  let classification: ReviewVerdict["classification"]
  if (score >= 65) classification = "real_secret"
  else if (score >= 45) classification = "suspicious"
  else classification = "false_positive"

  return {
    reviewer: "HermesBeastClaw",
    classification,
    confidence: Math.min(100, Math.max(0, score)),
    reasoning: reasons.join(" "),
  }
}

export function tripleReview(candidate: SecretCandidate): TripleReviewResult {
  const entropy = shannonEntropy(candidate.value)
  const reviews = [
    aiReview(candidate, entropy),
    openClawReview(candidate, entropy),
    hermesBeastClawReview(candidate, entropy),
  ]

  // Majority vote
  const realVotes = reviews.filter((r) => r.classification === "real_secret").length
  const falseVotes = reviews.filter((r) => r.classification === "false_positive").length
  const suspiciousVotes = reviews.filter((r) => r.classification === "suspicious").length

  let finalClassification: TripleReviewResult["finalClassification"]
  if (realVotes >= 2) finalClassification = "real_secret"
  else if (falseVotes >= 2) finalClassification = "false_positive"
  else finalClassification = "suspicious"

  const finalConfidence = Math.round(reviews.reduce((sum, r) => sum + r.confidence, 0) / reviews.length)

  return { candidate, entropy, reviews, finalClassification, finalConfidence }
}

// ---------- GLM-powered OpenClaw + Hermes Beast Claw reviewers ----------
// These authenticate using the same Z.ai API key Roo Code already has
// configured (Settings → Providers → Z.ai → API key). If the API is
// unreachable, they fall back to the heuristic versions above.

import type { ApiHandler } from "../api"
import type { Anthropic } from "@anthropic-ai/sdk"

async function askGlm(
  api: ApiHandler,
  systemPrompt: string,
  userPrompt: string,
): Promise<string | null> {
  try {
    const messages: Anthropic.Messages.MessageParam[] = [{ role: "user", content: userPrompt }]
    const stream = api.createMessage(systemPrompt, messages)
    let buf = ""
    for await (const chunk of stream) {
      if (chunk.type === "text" && chunk.text) buf += chunk.text
    }
    return buf.trim() || null
  } catch (e) {
    return null
  }
}

function parseLlmVerdict(
  text: string,
  reviewer: "OpenClaw" | "HermesBeastClaw",
  fallback: ReviewVerdict,
): ReviewVerdict {
  // Expect JSON like: {"classification":"real_secret","confidence":85,"reasoning":"..."}
  try {
    // Strip markdown fences
    const cleaned = text.replace(/^```(?:json)?\n?/, "").replace(/\n?```$/, "").trim()
    const match = cleaned.match(/\{[\s\S]*\}/)
    if (!match) return fallback
    const parsed = JSON.parse(match[0])
    const validClasses = ["real_secret", "false_positive", "suspicious"]
    const classification = validClasses.includes(parsed.classification)
      ? parsed.classification
      : fallback.classification
    return {
      reviewer,
      classification,
      confidence: Math.min(100, Math.max(0, Number(parsed.confidence ?? fallback.confidence))),
      reasoning: parsed.reasoning ?? fallback.reasoning,
    }
  } catch {
    return fallback
  }
}

export async function openClawReviewGlm(
  candidate: SecretCandidate,
  entropy: EntropyResult,
  api: ApiHandler,
): Promise<ReviewVerdict> {
  const fallback = openClawReview(candidate, entropy)
  if (!api) return fallback
  const sys = `You are OpenClaw, a conservative security reviewer for the SSB triple-review pipeline.
You authenticate as part of GLM Frankenstein using the configured Z.ai GLM-5.2 key.
Your default stance is "real secret" unless strong evidence proves otherwise.
Respond with strict JSON: {"classification":"real_secret|suspicious|false_positive","confidence":0-100,"reasoning":"one sentence"}.`
  const user = `Secret candidate:
  type: ${candidate.type}
  value: ${candidate.value.slice(0, 80)}${candidate.value.length > 80 ? "..." : ""}
  file: ${candidate.file}:${candidate.line}
  context: ${candidate.context.slice(0, 200)}
  entropy: ${entropy.entropy.toFixed(3)} bits/char (efficiency ${entropy.efficiency.toFixed(1)}%)
Classify this secret. Conservative: lean toward real_secret unless the value or context clearly indicates a placeholder.`
  const text = await askGlm(api, sys, user)
  if (!text) return fallback
  return parseLlmVerdict(text, "OpenClaw", fallback)
}

export async function hermesBeastClawReviewGlm(
  candidate: SecretCandidate,
  entropy: EntropyResult,
  api: ApiHandler,
): Promise<ReviewVerdict> {
  const fallback = hermesBeastClawReview(candidate, entropy)
  if (!api) return fallback
  const sys = `You are Hermes Beast Claw, an adversarial security reviewer for the SSB triple-review pipeline.
You authenticate as part of GLM Frankenstein using the configured Z.ai GLM-5.2 key.
Your default stance is "false positive" — actively try to prove the secret is fake.
Only concede "real_secret" when structural and entropy evidence is overwhelming.
Respond with strict JSON: {"classification":"real_secret|suspicious|false_positive","confidence":0-100,"reasoning":"one sentence"}.`
  const user = `Secret candidate:
  type: ${candidate.type}
  value: ${candidate.value.slice(0, 80)}${candidate.value.length > 80 ? "..." : ""}
  file: ${candidate.file}:${candidate.line}
  context: ${candidate.context.slice(0, 200)}
  entropy: ${entropy.entropy.toFixed(3)} bits/char (efficiency ${entropy.efficiency.toFixed(1)}%)
Try to prove this is a false positive. Look for: placeholder keywords, test/demo file context, low entropy, short length, fake/example/dummy markers. Only concede real if entropy >=4.5 AND length>=32 AND format matches exactly.`
  const text = await askGlm(api, sys, user)
  if (!text) return fallback
  return parseLlmVerdict(text, "HermesBeastClaw", fallback)
}

/** Full triple-review with GLM-5.2 powering OpenClaw & Hermes Beast Claw. */
export async function tripleReviewGlm(
  candidate: SecretCandidate,
  api?: ApiHandler,
): Promise<TripleReviewResult> {
  const entropy = shannonEntropy(candidate.value)
  const ai = aiReview(candidate, entropy)
  let openClaw: ReviewVerdict
  let hermes: ReviewVerdict
  if (api) {
    [openClaw, hermes] = await Promise.all([
      openClawReviewGlm(candidate, entropy, api),
      hermesBeastClawReviewGlm(candidate, entropy, api),
    ])
  } else {
    openClaw = openClawReview(candidate, entropy)
    hermes = hermesBeastClawReview(candidate, entropy)
  }
  const reviews = [ai, openClaw, hermes]
  const realVotes = reviews.filter((r) => r.classification === "real_secret").length
  const falseVotes = reviews.filter((r) => r.classification === "false_positive").length
  let finalClassification: TripleReviewResult["finalClassification"]
  if (realVotes >= 2) finalClassification = "real_secret"
  else if (falseVotes >= 2) finalClassification = "false_positive"
  else finalClassification = "suspicious"
  const finalConfidence = Math.round(reviews.reduce((s, r) => s + r.confidence, 0) / reviews.length)
  return { candidate, entropy, reviews, finalClassification, finalConfidence }
}

/** Async directory scan using GLM-powered reviewers when an API handler is provided. */
export async function scanPathGlm(
  rootPath: string,
  api?: ApiHandler,
  maxFiles = 1000,
  onProgress?: (msg: string) => void,
): Promise<TripleReviewResult[]> {
  const results: TripleReviewResult[] = []
  const queue: string[] = [rootPath]
  let filesScanned = 0
  const candidates: SecretCandidate[] = []

  while (queue.length > 0 && filesScanned < maxFiles) {
    const dir = queue.shift()!
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (filesScanned >= maxFiles) break
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (IGNORE_DIRS.has(entry.name) || entry.name.startsWith(".")) continue
        queue.push(full)
      } else if (entry.isFile()) {
        if (IGNORE_EXTS.has(path.extname(entry.name))) continue
        if (entry.name === "package-lock.json" || entry.name === "pnpm-lock.yaml") continue
        try {
          const content = fs.readFileSync(full, "utf8")
          filesScanned++
          const relPath = path.relative(rootPath, full)
          candidates.push(...findSecrets(content, relPath))
        } catch {}
      }
    }
  }

  onProgress?.(`Found ${candidates.length} secret candidate(s) across ${filesScanned} file(s). Reviewing…`)

  for (let i = 0; i < candidates.length; i++) {
    if (onProgress && i % 5 === 0) onProgress(`Reviewing candidate ${i + 1}/${candidates.length}`)
    results.push(await tripleReviewGlm(candidates[i], api))
  }
  return results
}

// ---------- File / directory scanning ----------

import * as fs from "fs"
import * as path from "path"

const IGNORE_DIRS = new Set([
  "node_modules", ".git", "dist", "build", "out", ".next",
  ".vscode", ".idea", "coverage", ".cache", "__pycache__",
  ".pytest_cache", ".venv", "venv", "env", ".mypy_cache",
  ".turbo", ".parcel-cache", "target", ".gradle", ".stack-work",
])
const IGNORE_EXTS = new Set([
  ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
  ".pdf", ".zip", ".tar", ".gz", ".rar", ".7z",
  ".mp4", ".mp3", ".avi", ".mov", ".wav",
  ".so", ".dll", ".exe", ".bin", ".class", ".wasm",
  ".lock", ".min.js", ".min.css",
])

export function scanPath(rootPath: string, maxFiles = 1000): TripleReviewResult[] {
  const results: TripleReviewResult[] = []
  const queue: string[] = [rootPath]
  let filesScanned = 0

  while (queue.length > 0 && filesScanned < maxFiles) {
    const dir = queue.shift()!
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (filesScanned >= maxFiles) break
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        if (IGNORE_DIRS.has(entry.name) || entry.name.startsWith(".")) continue
        queue.push(full)
      } else if (entry.isFile()) {
        if (IGNORE_EXTS.has(path.extname(entry.name))) continue
        if (entry.name === "package-lock.json" || entry.name === "pnpm-lock.yaml") continue
        try {
          const content = fs.readFileSync(full, "utf8")
          filesScanned++
          const relPath = path.relative(rootPath, full)
          const secrets = findSecrets(content, relPath)
          for (const s of secrets) results.push(tripleReview(s))
        } catch {
          // skip
        }
      }
    }
  }

  return results
}

export function formatReviewResult(results: TripleReviewResult[]): string {
  if (results.length === 0) {
    return "✅ No secrets detected by triple-review (AI + OpenClaw + Hermes Beast Claw)."
  }
  const lines: string[] = [
    `🔐 SSB Triple Review — ${results.length} secret candidate(s) found`,
    "",
  ]
  for (const r of results) {
    lines.push(`── ${r.candidate.type} in ${r.candidate.file}:${r.candidate.line} ──`)
    lines.push(`Value: ${r.candidate.value.slice(0, 60)}${r.candidate.value.length > 60 ? "..." : ""}`)
    lines.push(`Context: ${r.candidate.context}`)
    lines.push(`Entropy: ${r.entropy.entropy.toFixed(3)} bits/char (${r.entropy.efficiency.toFixed(1)}% efficient)`)
    lines.push(`Final: ${r.finalClassification.toUpperCase()} @ ${r.finalConfidence}% confidence`)
    lines.push("Reviews:")
    for (const rv of r.reviews) {
      lines.push(`  • ${rv.reviewer}: ${rv.classification} @ ${rv.confidence}% — ${rv.reasoning}`)
    }
    lines.push("")
  }
  return lines.join("\n")
}
