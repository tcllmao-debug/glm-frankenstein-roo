/**
 * CodeRabbit-style local code review — adapted for GLM Frankenstein.
 *
 * Pulls uncommitted git changes in the workspace, splits them into per-file
 * hunks, and asks GLM-5.2 to review each hunk. Returns line-by-line
 * suggestions that surface in the Roo chat as a tool result.
 *
 * Inspired by CodeRabbit.coderabbit-vscode v0.21.1's "Start Review" command.
 * Reimplemented to run entirely locally against the configured GLM model
 * (no external CodeRabbit account required).
 */

import * as cp from "child_process"
import * as vscode from "vscode"
import * as path from "path"
import { GLMClient } from "../api/glm"

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

/** Get the git diff for uncommitted changes (staged + unstaged) in the workspace. */
export function getUncommittedDiff(cwd: string): { files: Record<string, string>; raw: string } {
  try {
    const raw = cp
      .execSync("git diff HEAD --no-color", { cwd, encoding: "utf8", maxBuffer: 10 * 1024 * 1024 })
      .trim()
    if (!raw) return { files: {}, raw: "" }
    return { files: splitDiffByFile(raw), raw }
  } catch (e: any) {
    return { files: {}, raw: "", ...(e as any) }
  }
}

/** Get diff for a specific commit (vs its first parent). */
export function getCommitDiff(cwd: string, commitSha: string): { files: Record<string, string>; raw: string } {
  try {
    const raw = cp
      .execSync(`git show ${commitSha} --no-color --format=`, {
        cwd,
        encoding: "utf8",
        maxBuffer: 10 * 1024 * 1024,
      })
      .trim()
    return { files: splitDiffByFile(raw), raw }
  } catch {
    return { files: {}, raw: "" }
  }
}

function splitDiffByFile(diff: string): Record<string, string> {
  const files: Record<string, string> = {}
  const chunks = diff.split(/^diff --git /m).filter(Boolean)
  for (const chunk of chunks) {
    const m = chunk.match(/^a\/(\S+) b\/(\S+)/)
    if (!m) continue
    const filePath = m[2]
    files[filePath] = "diff --git " + chunk
  }
  return files
}

/** Parse a per-file diff into ReviewHunks. */
export function parseHunks(fileDiff: string, filePath: string): ReviewHunk[] {
  const hunks: ReviewHunk[] = []
  const lines = fileDiff.split("\n")
  let currentHunk: ReviewHunk | null = null
  let added = 0
  let removed = 0

  for (const line of lines) {
    const hunkMatch = line.match(/^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@/)
    if (hunkMatch) {
      if (currentHunk) {
        currentHunk.added = added
        currentHunk.removed = removed
        hunks.push(currentHunk)
      }
      currentHunk = {
        file: filePath,
        startLine: parseInt(hunkMatch[2], 10),
        endLine: parseInt(hunkMatch[2], 10),
        added: 0,
        removed: 0,
        diff: line + "\n",
      }
      added = 0
      removed = 0
    } else if (currentHunk) {
      currentHunk.diff += line + "\n"
      if (line.startsWith("+") && !line.startsWith("+++")) {
        added++
        currentHunk.endLine++
      } else if (line.startsWith("-") && !line.startsWith("---")) {
        removed++
      }
    }
  }

  if (currentHunk) {
    currentHunk.added = added
    currentHunk.removed = removed
    hunks.push(currentHunk)
  }

  return hunks
}

/** Ask GLM-5.2 to review a single hunk and return structured comments. */
export async function reviewHunkWithGLM(
  glm: GLMClient,
  hunk: ReviewHunk,
  systemPrompt: string,
): Promise<ReviewComment[]> {
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

    glm.chat(
      [{ role: "user", content: userPrompt }],
      undefined,
      {
        onChunk: (c) => (buf += c),
        onDone: (full) => {
          const text = (full.content || buf).trim()
          // Strip markdown fences if present
          const jsonText = text.replace(/^```(?:json)?\n?/, "").replace(/\n?```$/, "")
          try {
            const parsed = JSON.parse(jsonText)
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
            // LLM returned prose — return as a single info comment
            comments.push({
              file: hunk.file,
              line: hunk.startLine,
              severity: "info",
              category: "review",
              message: text.slice(0, 500),
            })
          }
          resolve(comments)
        },
        onError: () => resolve([]),
      },
      systemPrompt,
    )
  })
}

/** Run a full review of uncommitted changes. */
export async function reviewUncommitted(
  glm: GLMClient,
  cwd: string,
  onProgress?: (msg: string) => void,
): Promise<ReviewResult> {
  const { files, raw } = getUncommittedDiff(cwd)
  if (Object.keys(files).length === 0) {
    return { hunks: [], comments: [], summary: "No uncommitted changes to review." }
  }

  const allHunks: ReviewHunk[] = []
  for (const [file, diff] of Object.entries(files)) {
    allHunks.push(...parseHunks(diff, file))
  }

  onProgress?.(`Found ${allHunks.length} hunk(s) across ${Object.keys(files).length} file(s). Reviewing…`)

  const systemPrompt = `You are CodeRabbit-GLM, a senior code reviewer embedded in VS Code as part of GLM Frankenstein.
You review git diff hunks and reply with a strict JSON array of issues.
Be specific. Skip nitpicks. Surface real bugs, security issues, performance problems, and architectural concerns.
Praise good changes when warranted.`

  const allComments: ReviewComment[] = []
  for (let i = 0; i < allHunks.length; i++) {
    const hunk = allHunks[i]
    onProgress?.(`Reviewing hunk ${i + 1}/${allHunks.length}: ${hunk.file}:${hunk.startLine}`)
    const comments = await reviewHunkWithGLM(glm, hunk, systemPrompt)
    allComments.push(...comments)
  }

  const counts = allComments.reduce(
    (acc, c) => {
      acc[c.severity] = (acc[c.severity] ?? 0) + 1
      return acc
    },
    {} as Record<string, number>,
  )

  const summary = `Reviewed ${allHunks.length} hunk(s) across ${Object.keys(files).length} file(s).
Found ${allComments.length} comment(s): ${counts.critical ?? 0} critical, ${counts.warning ?? 0} warning, ${counts.info ?? 0} info, ${counts.praise ?? 0} praise.`

  return { hunks: allHunks, comments: allComments, summary }
}

/** Render a ReviewResult as a Markdown string for the chat. */
export function formatReviewResult(r: ReviewResult): string {
  if (r.hunks.length === 0) return r.summary

  const lines: string[] = [`## 🔍 Code Review (CodeRabbit-GLM)`, "", r.summary, ""]

  const bySeverity: Record<string, ReviewComment[]> = { critical: [], warning: [], info: [], praise: [] }
  for (const c of r.comments) {
    (bySeverity[c.severity] ??= []).push(c)
  }

  const emoji: Record<string, string> = { critical: "🔴", warning: "🟡", info: "🔵", praise: "🟢" }

  for (const sev of ["critical", "warning", "info", "praise"]) {
    const cs = bySeverity[sev]
    if (!cs || cs.length === 0) continue
    lines.push(`### ${emoji[sev]} ${sev.toUpperCase()} (${cs.length})`)
    lines.push("")
    for (const c of cs) {
      lines.push(`**${c.file}:${c.line}** — _${c.category}_`)
      lines.push(`> ${c.message}`)
      if (c.suggestion) {
        lines.push("```suggestion")
        lines.push(c.suggestion)
        lines.push("```")
      }
      lines.push("")
    }
  }

  return lines.join("\n")
}

/** Apply a review suggestion to a file at a given line. */
export async function applySuggestion(
  file: string,
  line: number,
  suggestion: string,
  cwd: string,
): Promise<boolean> {
  const fullPath = path.isAbsolute(file) ? file : path.join(cwd, file)
  const uri = vscode.Uri.file(fullPath)
  try {
    const doc = await vscode.workspace.openTextDocument(uri)
    const editor = await vscode.window.showTextDocument(doc)
    const lineIdx = Math.max(0, Math.min(doc.lineCount - 1, line - 1))
    const currentLine = doc.lineAt(lineIdx)
    const range = new vscode.Range(lineIdx, 0, lineIdx, currentLine.text.length)
    await editor.edit((e) => e.replace(range, suggestion))
    return true
  } catch {
    return false
  }
}
