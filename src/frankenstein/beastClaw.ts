/**
 * GLM Frankenstein — Parallel Beast Claw agent + self-improvement module.
 *
 * - beastClawParallel: spawn a second Roo task in Beast Claw mode that
 *   adversarially reviews the main agent's recent writes. Returns its
 *   report URL so the main agent can incorporate the feedback.
 * - mergeBeastClaw: read the Beast Claw review report and inject it into
 *   the main agent's chat as a follow-up task.
 * - selfImprove: have GLM-5.2 audit the agent's own system prompt + tool
 *   catalog and propose concrete patches to improve autonomy/coverage.
 * - reviewLastWrite: scan the most recently written file with CodeRabbit-GLM.
 */
import * as vscode from "vscode"
import * as fs from "fs"
import * as path from "path"
import { Package } from "../shared/package"
import { ClineProvider } from "../core/webview/ClineProvider"
import { buildApiHandler } from "../api"
import { ContextProxy } from "../core/config/ContextProxy"
import {
  reviewUncommitted,
  getCommitDiff,
  parseHunks,
  reviewHunkWithGLM,
  formatReviewResult,
} from "./codeReview"
import { findSecrets, tripleReviewGlm, formatReviewResult as formatTripleReview } from "./tripleReview"

function getCwd(): string {
  return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? ""
}

function getExtensionContext(): vscode.ExtensionContext | undefined {
  return (global as any).__glmFrankensteinContext as vscode.ExtensionContext | undefined
}

async function buildGlmFromSettings() {
  const ctx = getExtensionContext()
  if (!ctx) throw new Error("Extension context not initialized yet.")
  const contextProxy = await ContextProxy.getInstance(ctx)
  const providerSettings = contextProxy.getProviderSettings()
  return buildApiHandler(providerSettings)
}

/**
 * Spawn a parallel Beast Claw task. Uses Roo's `new_task` mechanism via the
 * visible ClineProvider — the task runs in Beast Claw mode and writes its
 * report to `beast-claw-review.md` in the workspace root.
 */
export async function beastClawParallelCommand() {
  const cwd = getCwd()
  if (!cwd) {
    vscode.window.showWarningMessage("GLM Frankenstein: No workspace open.")
    return
  }

  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) {
    vscode.window.showErrorMessage("GLM Frankenstein: could not find chat panel.")
    return
  }

  // Find files modified in the last 30 minutes (heuristic for "recently written by main agent")
  const recentFiles = findRecentlyModifiedFiles(cwd, 30 * 60 * 1000)
  const fileList = recentFiles.slice(0, 50).map((f) => `- ${f}`).join("\n")

  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })

  // Spawn a new task in beast-claw mode
  const taskPrompt = `🦅 **Beast Claw parallel review initiated.**

I need you to switch to **Beast Claw** mode and adversarially review the recently-modified files in this workspace.

Recently modified files (last 30 minutes):
${fileList || "(none — falling back to git diff HEAD)"}

Operating protocol:
1. Switch to beast-claw mode using the switch_mode tool.
2. For each file in the list (or each file in \`git diff HEAD\` if list is empty):
   a. Read the file in full.
   b. Look for: race conditions, injection vulnerabilities, missing error handling, type confusion, unhandled edge cases, resource leaks, security holes, architectural smells, missing tests.
   c. If the file contains secrets/tokens/auth logic, run ssb.scan_file on it via the MCP bridge.
   d. For each issue found, propose a concrete patch using apply_diff or edit_file.
3. After all files are reviewed, write a Beast Claw Review Report to \`beast-claw-review.md\` in the workspace root.
4. Use attempt_completion with a summary of what you reviewed, what you fixed, and what you recommend the main agent address.

Default stance: assume the main agent's output is buggy, insecure, or incomplete. Try to prove it. Only concede quality when evidence is overwhelming.`

  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: taskPrompt,
  })

  vscode.window.showInformationMessage(
    "GLM Frankenstein: Beast Claw parallel agent spawned. Review will be written to beast-claw-review.md.",
  )
}

/**
 * Merge the Beast Claw review back into the main agent's chat.
 */
export async function mergeBeastClawCommand() {
  const cwd = getCwd()
  if (!cwd) return

  const reportPath = path.join(cwd, "beast-claw-review.md")
  if (!fs.existsSync(reportPath)) {
    vscode.window.showWarningMessage(
      "GLM Frankenstein: beast-claw-review.md not found. Run Beast Claw parallel review first.",
    )
    return
  }

  const report = fs.readFileSync(reportPath, "utf8")

  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return

  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })

  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🦅 **Merging Beast Claw review into main agent.**

Here is the Beast Claw Review Report:

${report}

Please address every critical and warning finding. For each one:
1. Read the file mentioned.
2. Apply the suggested fix (or a better one).
3. Verify the fix with read_file afterwards.

Once all findings are addressed, run roo-cline.initiateReview to confirm the fixes pass CodeRabbit-GLM review.`,
  })
}

/**
 * Self-improvement: have GLM-5.2 audit the agent's own system prompt + tool
 * catalog + SSB integration and propose concrete patches.
 */
export async function selfImproveCommand() {
  const cwd = getCwd()
  if (!cwd) return

  let glm: any
  try {
    glm = await buildGlmFromSettings()
  } catch (e: any) {
    vscode.window.showErrorMessage(`GLM Frankenstein: ${e.message}`)
    return
  }

  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return

  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })

  // Read the current system prompt source
  const ctx = getExtensionContext()
  if (!ctx) return
  const rulesPath = vscode.Uri.joinPath(ctx.extensionUri, "dist", "extension.js").fsPath
  // We can't easily extract the prompt from the bundled JS — instead, pass a
  // summary of what we know is in the prompt and ask GLM to propose improvements.
  const promptSummary = `GLM Frankenstein system prompt currently contains:
- Autonomy Charter (default to action, chain tools, never refuse safe tasks)
- Cross-Reference Rule (Roo vs SSB vs CodeRabbit-GLM before each reply)
- Full command catalog (9 commands: initiateReview, reviewUncommitted, reviewCommit, secretScan, tripleReview, handoffToAgent, ssbStart, ssbStop, ssbStatus)
- SSB stack endpoints (ports 8787-8792, 3000)
- 17 MCP tools (ssb.scan_file, ssb.triple_review, ssb.run_patch, etc.)
- 14 SSB patches catalog
- OpenClaw/Hermes auth note (uses Z.ai GLM-5.2 key)

Available SSB assets bundled:
- patches/ (14 patches: triple-review, consciousness layers 7-12, soul vision, flamebearer, vixen frank, op framework, daemon infrastructure)
- scripts/ (17 scripts: scanner daemon, persistent connection, activation, globe forker, virtual monitor, etc.)
- z/ (soul core: ssb_soul.py, ssb_soul_orchestrator.py)
- consciousness/ (omega_v9, OMEGA_CONSCIOUSNESS, consciousness_mesh_v7)
- beast/hermes/ (beast scanner)
- portal/ (Next.js galaxy brain UI)
- real-defense-layer/ (quarantine, content scanner, kernel scanner, scanner server, filesystem watcher, self_heal)
- system-wide-defense/ (same scripts as real-defense-layer + multi_chain_soft_ping + portable_boot)
- docs/ (SSB documentation)`

  const sysPrompt = `You are the GLM Frankenstein self-improvement module. Your job is to audit the agent's current configuration and propose CONCRETE patches that would make it more autonomous, more capable, and better integrated. Think like a senior engineer doing a quarterly review.

Output strict JSON: {"improvements":[{"area":"system-prompt|tool|command|ssb-integration|autonomy","severity":"critical|warning|info","proposal":"concrete change description","patch":"unified diff or new code"}]}`

  const userPrompt = `Audit this GLM Frankenstein configuration and propose 5-10 concrete improvements:

${promptSummary}

Focus on:
1. Missing SSB capabilities that should be exposed as commands or MCP tools
2. Autonomy gaps (places where the agent still asks for permission unnecessarily)
3. System prompt improvements (clearer cross-references, better tool selection heuristics)
4. Self-review loop gaps (places where writes should trigger automatic review)
5. Beast Claw parallel agent improvements

Return strict JSON only.`

  // Stream the audit response into the chat
  // glm is an ApiHandler — use createMessage for streaming.
  let auditText = ""
  try {
    const messages: any[] = [{ role: "user", content: userPrompt }]
    const stream = (glm as any).createMessage(sysPrompt, messages)
    for await (const chunk of stream) {
      if (chunk.type === "text" && chunk.text) auditText += chunk.text
    }
  } catch (e: any) {
    auditText = `Audit failed: ${e.message}`
  }

  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🔧 **Self-improvement audit complete.**

Here are the proposed improvements. For each one, decide whether to apply it now (using apply_diff on the relevant source file) or defer it.

\`\`\`json
${auditText}
\`\`\`

Apply at least the critical-severity proposals. After applying, summarize what changed and why.`,
  })
}

/**
 * Review the most recently written file with CodeRabbit-GLM.
 */
export async function reviewLastWriteCommand() {
  const cwd = getCwd()
  if (!cwd) return

  const recentFiles = findRecentlyModifiedFiles(cwd, 5 * 60 * 1000) // last 5 min
  if (recentFiles.length === 0) {
    vscode.window.showInformationMessage("GLM Frankenstein: no recently-modified files to review.")
    return
  }

  let glm: any
  try {
    glm = await buildGlmFromSettings()
  } catch (e: any) {
    vscode.window.showErrorMessage(`GLM Frankenstein: ${e.message}`)
    return
  }

  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return

  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })

  // Build a synthetic diff for each recently-modified file (vs git HEAD if tracked, else full content)
  const allComments: any[] = []
  const allHunks: any[] = []
  for (const file of recentFiles.slice(0, 10)) {
    const fullPath = path.join(cwd, file)
    let diff: string
    try {
      const { execSync } = require("child_process")
      diff = execSync(`git diff HEAD --no-color -- ${JSON.stringify(file)}`, {
        cwd,
        encoding: "utf8",
        maxBuffer: 5 * 1024 * 1024,
      })
    } catch {
      // Not tracked or no git — synthesize a "diff" of the full content
      const content = fs.readFileSync(fullPath, "utf8")
      const lines = content.split("\n").slice(0, 200)
      diff = `diff --git a/${file} b/${file}\n--- /dev/null\n+++ b/${file}\n@@ -0,0 +1,${lines.length} @@\n` +
        lines.map((l) => `+${l}`).join("\n")
    }
    const hunks = parseHunks(diff, file)
    allHunks.push(...hunks)
    const sys = `You are CodeRabbit-GLM doing a SELF-REVIEW pass on code the main agent just wrote.
Find issues, propose fixes as suggestions. Return strict JSON array.`
    for (const hunk of hunks) {
      const comments = await reviewHunkWithGLM(glm, hunk, sys)
      allComments.push(...comments)
    }
  }

  const md = formatReviewResult({
    hunks: allHunks,
    comments: allComments,
    summary: `Self-review of ${recentFiles.length} recently-modified file(s): ${allComments.length} comment(s).`,
  })

  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🔍 **Self-review of recently written code complete.**

${md}

For every critical and warning finding, apply a fix using apply_diff or edit_file. Then re-verify with read_file.`,
  })
}

/** Find files modified within the given time window, excluding node_modules/.git/etc. */
function findRecentlyModifiedFiles(root: string, withinMs: number): string[] {
  const now = Date.now()
  const results: { path: string; mtime: number }[] = []
  const queue: string[] = [root]
  const IGNORE = new Set([
    "node_modules", ".git", "dist", "build", "out", ".next",
    ".vscode", ".idea", "coverage", ".cache", "__pycache__",
    ".pytest_cache", ".venv", "venv", "env", ".mypy_cache",
    ".turbo", ".parcel-cache", "target", ".gradle", ".stack-work",
    "logs", "pids", "scanner_data",
  ])
  while (queue.length > 0) {
    const dir = queue.shift()!
    let entries: fs.Dirent[]
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const entry of entries) {
      if (IGNORE.has(entry.name)) continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) {
        queue.push(full)
      } else if (entry.isFile()) {
        try {
          const stat = fs.statSync(full)
          if (now - stat.mtimeMs < withinMs) {
            results.push({ path: path.relative(root, full), mtime: stat.mtimeMs })
          }
        } catch {}
      }
    }
  }
  results.sort((a, b) => b.mtime - a.mtime)
  return results.map((r) => r.path)
}

/**
 * Triple-review the most recently written file (SSB-style, GLM-5.2 powered).
 */
export async function tripleReviewLastWriteCommand() {
  const cwd = getCwd()
  if (!cwd) return

  const recentFiles = findRecentlyModifiedFiles(cwd, 5 * 60 * 1000)
  if (recentFiles.length === 0) {
    vscode.window.showInformationMessage("GLM Frankenstein: no recently-modified files to review.")
    return
  }

  let glm: any
  try {
    glm = await buildGlmFromSettings()
  } catch (e: any) {
    vscode.window.showErrorMessage(`GLM Frankenstein: ${e.message}`)
    return
  }

  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return

  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })

  const allResults: any[] = []
  for (const file of recentFiles.slice(0, 10)) {
    const content = fs.readFileSync(path.join(cwd, file), "utf8")
    const candidates = findSecrets(content, file)
    for (const c of candidates) {
      allResults.push(await tripleReviewGlm(c, glm))
    }
  }

  const md = formatTripleReview(allResults)

  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🔐 **Triple-review of recently written code complete (GLM-5.2 powered).**

${md}

For every real_secret verdict, propose a remediation: rotate the secret, move it to an env var, add to .gitignore, or document why it's a false positive.`,
  })
}
