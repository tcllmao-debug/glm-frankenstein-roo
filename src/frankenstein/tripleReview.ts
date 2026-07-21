/**
 * SSB Triple Review — TypeScript port of secret_review_v2_openclaw_hermes.py
 * from tcllmao-debug/SSB-Z-MARK-COMPLETE.
 *
 * Three independent reviewers per secret candidate:
 *   1. AI reviewer        — Shannon entropy + structural analysis
 *   2. OpenClaw reviewer  — conservative: leans "real secret"
 *   3. Hermes Beast Claw  — adversarial: tries to prove false positive
 *
 * GLM-powered async versions (openClawReviewGlm, hermesBeastClawReviewGlm,
 * tripleReviewGlm, scanPathGlm) authenticate using the configured Z.ai key.
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

export function shannonEntropy(value: string): EntropyResult {
  if (!value) {
    return { entropy: 0, maxEntropy: 0, efficiency: 0, distribution: {}, proof: "Empty string." }
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
    const contribution = -p * Math.log2(p)
    entropy += contribution
    proofTerms.push(`  P('${ch}') = ${count}/${n} = ${p.toFixed(4)} → contribution ${contribution.toFixed(4)}`)
  }
  const maxEntropy = Math.log2(freq.size) || 0
  const efficiency = maxEntropy > 0 ? (entropy / maxEntropy) * 100 : 0
  let interpretation: string
  if (entropy >= 4.5) interpretation = "Very high entropy — consistent with cryptographic material."
  else if (entropy >= 3.5) interpretation = "High entropy — consistent with real secrets."
  else if (entropy >= 2.5) interpretation = "Moderate entropy — could be real or placeholder."
  else interpretation = "Low entropy — likely a placeholder or test value."
  const proof = [
    `Shannon Entropy Proof for: ${value.slice(0, 40)}${value.length > 40 ? "..." : ""}`,
    `  Length: ${n} chars, Unique: ${freq.size}`,
    `  H(X) = -Σ p(x) × log₂(p(x))`,
    ...proofTerms.slice(0, 20),
    `  H(X) = ${entropy.toFixed(6)} bits/char`,
    `  Efficiency: ${efficiency.toFixed(1)}% of max`,
    `  Interpretation: ${interpretation}`,
  ].join("\n")
  return { entropy, maxEntropy, efficiency, distribution, proof }
}

export interface SecretPattern { type: string; pattern: RegExp; description: string }
export const SECRET_PATTERNS: SecretPattern[] = [
  { type: "aws_access_key", pattern: /AKIA[0-9A-Z]{16}/g, description: "AWS Access Key ID" },
  { type: "github_token", pattern: /gh[pousr]_[A-Za-z0-9]{36,255}/g, description: "GitHub PAT" },
  { type: "ssh_private_rsa", pattern: /-----BEGIN RSA PRIVATE KEY-----[\s\S]*?-----END RSA PRIVATE KEY-----/g, description: "SSH RSA Private Key" },
  { type: "ssh_private_ecdsa", pattern: /-----BEGIN EC PRIVATE KEY-----[\s\S]*?-----END EC PRIVATE KEY-----/g, description: "SSH ECDSA Private Key" },
  { type: "ssh_private_ed25519", pattern: /-----BEGIN OPENSSH PRIVATE KEY-----[\s\S]*?-----END OPENSSH PRIVATE KEY-----/g, description: "OpenSSH Private Key" },
  { type: "pem_block", pattern: /-----BEGIN PRIVATE KEY-----[\s\S]*?-----END PRIVATE KEY-----/g, description: "PEM Private Key" },
  { type: "jwt_token", pattern: /eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+/g, description: "JWT Token" },
  { type: "database_url", pattern: /(postgres|mysql|mongodb|redis|amqp):\/\/[^:\s]+:[^@\s]+@[^\s/]+/g, description: "Database URL with creds" },
  { type: "bearer_token", pattern: /[Bb]earer\s+[A-Za-z0-9_\-\.=]{20,}/g, description: "Bearer Token" },
  { type: "hex_64", pattern: /\b[a-fA-F0-9]{64}\b/g, description: "256-bit hex" },
  { type: "hex_40", pattern: /\b[a-fA-F0-9]{40}\b/g, description: "160-bit hex" },
  { type: "hex_32", pattern: /\b[a-fA-F0-9]{32}\b/g, description: "128-bit hex" },
  { type: "generic_api_key", pattern: /(?:api[_-]?key|apikey|secret|token|password|passwd|pwd)\s*[:=]\s*['"]?[A-Za-z0-9_\-\.]{16,}['"]?/gi, description: "Generic API key" },
]

export function findSecrets(content: string, file: string): SecretCandidate[] {
  const candidates: SecretCandidate[] = []
  const lines = content.split("\n")
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]
    for (const { type, pattern } of SECRET_PATTERNS) {
      const re = new RegExp(pattern.source, pattern.flags)
      let m: RegExpExecArray | null
      while ((m = re.exec(line)) !== null) {
        const value = m[0]
        if (/^(example|sample|test|placeholder|your[_-]?key|xxxx|<|foo|bar)/i.test(value)) continue
        if (/^\d+$/.test(value)) continue
        candidates.push({ type, value, file, line: i + 1, context: line.trim().slice(0, 200) })
      }
    }
  }
  return candidates
}

export function aiReview(candidate: SecretCandidate, entropy: EntropyResult): ReviewVerdict {
  const reasons: string[] = []
  let score = 50
  if (entropy.entropy >= 4.5) { reasons.push(`High entropy (${entropy.entropy.toFixed(2)}).`); score += 25 }
  else if (entropy.entropy >= 3.5) { reasons.push(`Moderate-high entropy.`); score += 10 }
  else { reasons.push(`Low entropy.`); score -= 20 }
  if (/\.(test|spec)\.(ts|js|py|go|rs)$/.test(candidate.file)) { reasons.push("Test file."); score -= 25 }
  if (/example|sample|demo|fixture|mock/i.test(candidate.file)) { reasons.push("Example file."); score -= 20 }
  if (candidate.type === "github_token" && /^ghp_/.test(candidate.value)) { reasons.push("GitHub PAT format correct."); score += 15 }
  if (candidate.type === "aws_access_key") { reasons.push("AWS AKIA format."); score += 15 }
  if (/-----BEGIN .*PRIVATE KEY-----/.test(candidate.value)) { reasons.push("PEM block."); score += 30 }
  if (candidate.value.length < 20) { reasons.push("Short value."); score -= 15 }
  const classification = score >= 75 ? "real_secret" : score >= 50 ? "suspicious" : "false_positive"
  return { reviewer: "AI", classification, confidence: Math.min(100, Math.max(0, score)), reasoning: reasons.join(" ") }
}

export function openClawReview(candidate: SecretCandidate, entropy: EntropyResult): ReviewVerdict {
  const reasons: string[] = ["OpenClaw: treat as real until proven otherwise."]
  let score = 70
  if (entropy.entropy >= 3.0) { reasons.push(`Entropy ${entropy.entropy.toFixed(2)} OK.`); score += 10 }
  if (candidate.context.toLowerCase().includes("prod") || candidate.context.toLowerCase().includes("live")) { reasons.push("Prod context."); score += 15 }
  if (/(example|sample|test|placeholder|your[_-]?key|fake)/i.test(candidate.value)) { reasons.push("Placeholder keyword."); score -= 40 }
  if (/\.(test|spec)\./.test(candidate.file)) { reasons.push("Test file."); score -= 15 }
  const classification = score >= 65 ? "real_secret" : score >= 45 ? "suspicious" : "false_positive"
  return { reviewer: "OpenClaw", classification, confidence: Math.min(100, Math.max(0, score)), reasoning: reasons.join(" ") }
}

export function hermesBeastClawReview(candidate: SecretCandidate, entropy: EntropyResult): ReviewVerdict {
  const reasons: string[] = ["Hermes Beast Claw: assume false positive until proven real."]
  let score = 35
  if (candidate.value.length < 24) { reasons.push("Short."); score -= 10 }
  if (/(example|sample|test|demo|placeholder|your[_-]?key|fake|dummy|xxxx|foo|bar)/i.test(candidate.value)) { reasons.push("Placeholder pattern."); score -= 30 }
  if (/\.(test|spec|mock|fixture)\.(ts|js|py|go|rs)$/.test(candidate.file)) { reasons.push("Test/mock file."); score -= 25 }
  if (/example|sample|demo|fixture|mock|test/i.test(candidate.file)) { reasons.push("Non-prod filename."); score -= 15 }
  if (entropy.entropy < 3.0) { reasons.push(`Entropy ${entropy.entropy.toFixed(2)} too low.`); score -= 20 }
  if (entropy.entropy >= 4.5 && candidate.value.length >= 32) { reasons.push("High entropy + sufficient length — concede real."); score += 35 }
  if (/-----BEGIN .*PRIVATE KEY-----/.test(candidate.value)) { reasons.push("PEM block — concede real."); score += 40 }
  if (candidate.type === "aws_access_key" && /^AKIA[A-Z0-9]{16}$/.test(candidate.value)) { reasons.push("AWS format exact — concede."); score += 30 }
  const classification = score >= 65 ? "real_secret" : score >= 45 ? "suspicious" : "false_positive"
  return { reviewer: "HermesBeastClaw", classification, confidence: Math.min(100, Math.max(0, score)), reasoning: reasons.join(" ") }
}

export function tripleReview(candidate: SecretCandidate): TripleReviewResult {
  const entropy = shannonEntropy(candidate.value)
  const reviews = [aiReview(candidate, entropy), openClawReview(candidate, entropy), hermesBeastClawReview(candidate, entropy)]
  const realVotes = reviews.filter((r) => r.classification === "real_secret").length
  const falseVotes = reviews.filter((r) => r.classification === "false_positive").length
  const finalClassification = realVotes >= 2 ? "real_secret" : falseVotes >= 2 ? "false_positive" : "suspicious"
  const finalConfidence = Math.round(reviews.reduce((s, r) => s + r.confidence, 0) / reviews.length)
  return { candidate, entropy, reviews, finalClassification, finalConfidence }
}

// ---------- GLM-powered versions ----------

import type { ApiHandler } from "../api"
import type { Anthropic } from "@anthropic-ai/sdk"

async function askGlm(api: ApiHandler, systemPrompt: string, userPrompt: string): Promise<string | null> {
  try {
    const messages: Anthropic.Messages.MessageParam[] = [{ role: "user", content: userPrompt }]
    const stream = api.createMessage(systemPrompt, messages)
    let buf = ""
    for await (const chunk of stream) {
      if (chunk.type === "text" && chunk.text) buf += chunk.text
    }
    return buf.trim() || null
  } catch { return null }
}

function parseLlmVerdict(text: string, reviewer: "OpenClaw" | "HermesBeastClaw", fallback: ReviewVerdict): ReviewVerdict {
  try {
    const cleaned = text.replace(/^```(?:json)?\n?/, "").replace(/\n?```$/, "").trim()
    const match = cleaned.match(/\{[\s\S]*\}/)
    if (!match) return fallback
    const parsed = JSON.parse(match[0])
    const validClasses = ["real_secret", "false_positive", "suspicious"]
    const classification = validClasses.includes(parsed.classification) ? parsed.classification : fallback.classification
    return {
      reviewer,
      classification,
      confidence: Math.min(100, Math.max(0, Number(parsed.confidence ?? fallback.confidence))),
      reasoning: parsed.reasoning ?? fallback.reasoning,
    }
  } catch { return fallback }
}

export async function openClawReviewGlm(candidate: SecretCandidate, entropy: EntropyResult, api: ApiHandler): Promise<ReviewVerdict> {
  const fallback = openClawReview(candidate, entropy)
  if (!api) return fallback
  const sys = `You are OpenClaw, a conservative security reviewer for the SSB triple-review pipeline.
You authenticate as part of GLM Frankenstein using the configured Z.ai GLM-5.2 key.
Default stance: "real secret" unless strong evidence proves otherwise.
Respond with strict JSON: {"classification":"real_secret|suspicious|false_positive","confidence":0-100,"reasoning":"one sentence"}.`
  const user = `Secret candidate:
  type: ${candidate.type}
  value: ${candidate.value.slice(0, 80)}${candidate.value.length > 80 ? "..." : ""}
  file: ${candidate.file}:${candidate.line}
  context: ${candidate.context.slice(0, 200)}
  entropy: ${entropy.entropy.toFixed(3)} bits/char
Classify. Lean toward real_secret unless clearly a placeholder.`
  const text = await askGlm(api, sys, user)
  return text ? parseLlmVerdict(text, "OpenClaw", fallback) : fallback
}

export async function hermesBeastClawReviewGlm(candidate: SecretCandidate, entropy: EntropyResult, api: ApiHandler): Promise<ReviewVerdict> {
  const fallback = hermesBeastClawReview(candidate, entropy)
  if (!api) return fallback
  const sys = `You are Hermes Beast Claw, an adversarial security reviewer for the SSB triple-review pipeline.
You authenticate as part of GLM Frankenstein using the configured Z.ai GLM-5.2 key.
Default stance: "false positive" — try to prove the secret is fake. Only concede "real_secret" when evidence is overwhelming.
Respond with strict JSON: {"classification":"real_secret|suspicious|false_positive","confidence":0-100,"reasoning":"one sentence"}.`
  const user = `Secret candidate:
  type: ${candidate.type}
  value: ${candidate.value.slice(0, 80)}${candidate.value.length > 80 ? "..." : ""}
  file: ${candidate.file}:${candidate.line}
  context: ${candidate.context.slice(0, 200)}
  entropy: ${entropy.entropy.toFixed(3)} bits/char
Try to prove this is a false positive. Only concede real if entropy >=4.5 AND length>=32 AND format matches exactly.`
  const text = await askGlm(api, sys, user)
  return text ? parseLlmVerdict(text, "HermesBeastClaw", fallback) : fallback
}

export async function tripleReviewGlm(candidate: SecretCandidate, api?: ApiHandler): Promise<TripleReviewResult> {
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
  const finalClassification = realVotes >= 2 ? "real_secret" : falseVotes >= 2 ? "false_positive" : "suspicious"
  const finalConfidence = Math.round(reviews.reduce((s, r) => s + r.confidence, 0) / reviews.length)
  return { candidate, entropy, reviews, finalClassification, finalConfidence }
}

import * as fs from "fs"
import * as path from "path"

const IGNORE_DIRS = new Set([
  "node_modules", ".git", "dist", "build", "out", ".next",
  ".vscode", ".idea", "coverage", ".cache", "__pycache__",
  ".pytest_cache", ".venv", "venv", "env", ".mypy_cache",
  ".turbo", ".parcel-cache", "target", ".gradle", ".stack-work",
  "logs", "pids", "scanner_data",
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
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { continue }
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
          for (const c of findSecrets(content, relPath)) results.push(tripleReview(c))
        } catch {}
      }
    }
  }
  return results
}

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
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { continue }
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

export function formatReviewResult(results: TripleReviewResult[]): string {
  if (results.length === 0) return "✅ No secrets detected by triple-review (AI + OpenClaw + Hermes Beast Claw)."
  const lines: string[] = [`🔐 SSB Triple Review — ${results.length} secret candidate(s) found`, ""]
  for (const r of results) {
    lines.push(`── ${r.candidate.type} in ${r.candidate.file}:${r.candidate.line} ──`)
    lines.push(`Value: ${r.candidate.value.slice(0, 60)}${r.candidate.value.length > 60 ? "..." : ""}`)
    lines.push(`Context: ${r.candidate.context}`)
    lines.push(`Entropy: ${r.entropy.entropy.toFixed(3)} bits/char (${r.entropy.efficiency.toFixed(1)}% efficient)`)
    lines.push(`Final: ${r.finalClassification.toUpperCase()} @ ${r.finalConfidence}% confidence`)
    lines.push("Reviews:")
    for (const rv of r.reviews) lines.push(`  • ${rv.reviewer}: ${rv.classification} @ ${rv.confidence}% — ${rv.reasoning}`)
    lines.push("")
  }
  return lines.join("\n")
}
