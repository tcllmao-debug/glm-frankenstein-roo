/**
 * CodeRabbit-GLM local code review — reimplementation of CodeRabbit's
 * "Start Review" feature that runs entirely locally against your configured
 * GLM model. No CodeRabbit account, no auth, no telemetry.
 */
import * as cp from "child_process"
import * as vscode from "vscode"
import * as path from "path"
import type { ApiHandler } from "../api"
import type { Anthropic } from "@anthropic-ai/sdk"

export interface ReviewHunk {
  file: string
  startLine: number
  endLine: number
  added: number
  removed: number
  diff: string
}

export interface ReviewComment {
  file: string
  line: number
  severity: "info" | "warning" | "critical" | "praise"
  category: string
  message: string
  suggestion?: string
}

export interface ReviewResult {
  hunks: ReviewHunk[]
  comments: ReviewComment[]
  summary: string
}

export function getUncommittedDiff(cwd: string): { files: Record<string, string>; raw: string } {
  try {
    const raw = cp.execSync("git diff HEAD --no-color", { cwd, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 }).trim()
    if (!raw) return { files: {}, raw: "" }
    return { files: splitDiffByFile(raw), raw }
  } catch { return { files: {}, raw: "" } }
}

export function getCommitDiff(cwd: string, sha: string): { files: Record<string, string>; raw: string } {
  try {
    const raw = cp.execSync(`git show ${sha} --no-color --format=`, { cwd, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 }).trim()
    return { files: splitDiffByFile(raw), raw }
  } catch { return { files: {}, raw: "" } }
}

function splitDiffByFile(diff: string): Record<string, string> {
  const files: Record<string, string> = {}
  const chunks = diff.split(/^diff --git /m).filter(Boolean)
  for (const chunk of chunks) {
    const m = chunk.match(/^a\/(\S+) b\/(\S+)/)
    if (!m) continue
    files[m[2]] = "diff --git " + chunk
  }
  return files
}

export function parseHunks(fileDiff: string, filePath: string): ReviewHunk[] {
  const hunks: ReviewHunk[] = []
  const lines = fileDiff.split("\n")
  let currentHunk: ReviewHunk | null = null
  let added = 0
  let removed = 0
  for (const line of lines) {
    const m = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
    if (m) {
      if (currentHunk) {
        currentHunk.added = added
        currentHunk.removed = removed
        hunks.push(currentHunk)
      }
      currentHunk = { file: filePath, startLine: parseInt(m[2], 10), endLine: parseInt(m[2], 10), added: 0, removed: 0, diff: line + "\n" }
      added = 0
      removed = 0
    } else if (currentHunk) {
      currentHunk.diff += line + "\n"
      if (line.startsWith("+") && !line.startsWith("+++")) { added++; currentHunk.endLine++ }
      else if (line.startsWith("-") && !line.startsWith("---")) removed++
    }
  }
  if (currentHunk) { currentHunk.added = added; currentHunk.removed = removed; hunks.push(currentHunk) }
  return hunks
}

export async function reviewHunkWithGLM(glm: ApiHandler, hunk: ReviewHunk, systemPrompt: string): Promise<ReviewComment[]> {
  const userPrompt = `Review this code hunk and identify issues. Be specific and concise.

File: ${hunk.file}
Lines: ${hunk.startLine}-${hunk.endLine} (+${hunk.added}/-${hunk.removed})

\`\`\`diff
${hunk.diff}
\`\`\`

Respond with a JSON array of comments. Each comment MUST have:
  - "line": line number in the new file
  - "severity": "info" | "warning" | "critical" | "praise"
  - "category": e.g. "bug", "security", "performance", "style", "maintainability"
  - "message": one-sentence description
  - "suggestion": optional code suggestion (string)

If the hunk is fine, return [].
Return ONLY the JSON array, no markdown.`

  return new Promise((resolve) => {
    const comments: ReviewComment[] = []
    let buf = ""
    const messages: Anthropic.Messages.MessageParam[] = [{ role: "user", content: userPrompt }]
    try {
      const stream = glm.createMessage(systemPrompt, messages)
      ;(async () => {
        for await (const chunk of stream) {
          if (chunk.type === "text" && chunk.text) buf += chunk.text
        }
        const text = buf.trim().replace(/^```(?:json)?\n?/, "").replace(/\n?```$/, "")
        try {
          const parsed = JSON.parse(text)
          if (Array.isArray(parsed)) {
            for (const p of parsed) {
              if (typeof p.line === "number" && typeof p.message === "string") {
                comments.push({
                  file: hunk.file,
                  line: p.line,
                  severity: p.severity ?? "info",
                  category: p.category ?? "general",
                  message: p.message,
                  suggestion: p.suggestion,
                })
              }
            }
          }
        } catch {
          comments.push({ file: hunk.file, line: hunk.startLine, severity: "info", category: "review", message: text.slice(0, 500) })
        }
        resolve(comments)
      })().catch(() => resolve([]))
    } catch { resolve([]) }
  })
}

export async function reviewUncommitted(glm: ApiHandler, cwd: string, onProgress?: (msg: string) => void): Promise<ReviewResult> {
  const { files, raw } = getUncommittedDiff(cwd)
  if (Object.keys(files).length === 0) return { hunks: [], comments: [], summary: "No uncommitted changes to review." }
  const allHunks: ReviewHunk[] = []
  for (const [file, diff] of Object.entries(files)) allHunks.push(...parseHunks(diff, file))
  onProgress?.(`Found ${allHunks.length} hunk(s) across ${Object.keys(files).length} file(s). Reviewing…`)
  const systemPrompt = `You are CodeRabbit-GLM, a senior code reviewer embedded in VS Code as part of GLM Frankenstein.
You review git diff hunks and reply with a strict JSON array of issues.
Be specific. Skip nitpicks. Surface real bugs, security issues, performance problems, and architectural concerns.
Praise good changes when warranted.`
  const allComments: ReviewComment[] = []
  for (let i = 0; i < allHunks.length; i++) {
    onProgress?.(`Reviewing hunk ${i + 1}/${allHunks.length}: ${allHunks[i].file}:${allHunks[i].startLine}`)
    allComments.push(...await reviewHunkWithGLM(glm, allHunks[i], systemPrompt))
  }
  const counts = allComments.reduce((acc, c) => { acc[c.severity] = (acc[c.severity] ?? 0) + 1; return acc }, {} as Record<string, number>)
  const summary = `Reviewed ${allHunks.length} hunk(s) across ${Object.keys(files).length} file(s).
Found ${allComments.length} comment(s): ${counts.critical ?? 0} critical, ${counts.warning ?? 0} warning, ${counts.info ?? 0} info, ${counts.praise ?? 0} praise.`
  return { hunks: allHunks, comments: allComments, summary }
}

export function formatReviewResult(r: ReviewResult): string {
  if (r.hunks.length === 0) return r.summary
  const lines: string[] = [`## 🔍 Code Review (CodeRabbit-GLM)`, "", r.summary, ""]
  const bySeverity: Record<string, ReviewComment[]> = { critical: [], warning: [], info: [], praise: [] }
  for (const c of r.comments) (bySeverity[c.severity] ??= []).push(c)
  const emoji: Record<string, string> = { critical: "🔴", warning: "🟡", info: "🔵", praise: "🟢" }
  for (const sev of ["critical", "warning", "info", "praise"]) {
    const cs = bySeverity[sev]
    if (!cs || cs.length === 0) continue
    lines.push(`### ${emoji[sev]} ${sev.toUpperCase()} (${cs.length})`)
    lines.push("")
    for (const c of cs) {
      lines.push(`**${c.file}:${c.line}** — _${c.category}_`)
      lines.push(`> ${c.message}`)
      if (c.suggestion) { lines.push("```suggestion"); lines.push(c.suggestion); lines.push("```") }
      lines.push("")
    }
  }
  return lines.join("\n")
}
