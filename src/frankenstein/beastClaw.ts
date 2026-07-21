/** GLM Frankenstein — Parallel Beast Claw agent + self-improvement */
import * as vscode from "vscode"
import * as fs from "fs"
import * as path from "path"
import { Package } from "../shared/package"
import { ClineProvider } from "../core/webview/ClineProvider"
import { buildApiHandler } from "../api"
import { ContextProxy } from "../core/config/ContextProxy"
import { parseHunks, reviewHunkWithGLM, formatReviewResult } from "./codeReview"
import { findSecrets, tripleReviewGlm, formatReviewResult as formatTripleReview } from "./tripleReview"

function getCwd(): string { return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "" }
function getExtensionContext(): vscode.ExtensionContext | undefined { return (global as any).__glmFrankensteinContext as vscode.ExtensionContext | undefined }

async function buildGlmFromSettings() {
  const ctx = getExtensionContext()
  if (!ctx) throw new Error("Extension context not initialized.")
  const contextProxy = await ContextProxy.getInstance(ctx)
  return buildApiHandler(contextProxy.getProviderSettings())
}

export async function beastClawParallelCommand() {
  const cwd = getCwd()
  if (!cwd) { vscode.window.showWarningMessage("GLM Frankenstein: No workspace open."); return }
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  const recentFiles = findRecentlyModifiedFiles(cwd, 30 * 60 * 1000)
  const fileList = recentFiles.slice(0, 50).map((f) => `- ${f}`).join("\n")
  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🦅 **Beast Claw parallel review initiated.**

Switch to **Beast Claw** mode and adversarially review the recently-modified files in this workspace.

Recently modified files (last 30 minutes):
${fileList || "(none — falling back to git diff HEAD)"}

Operating protocol:
1. Switch to beast-claw mode using the switch_mode tool.
2. For each file: read full, look for race conditions, injection, missing errors, type confusion, leaks, security holes.
3. If file contains secrets/tokens/auth logic, run ssb.scan_file via MCP bridge.
4. For each issue, propose a concrete patch using apply_diff or edit_file.
5. Write Beast Claw Review Report to \`beast-claw-review.md\` in the workspace root.
6. Use attempt_completion with a summary.

Default stance: assume the main agent's output is buggy, insecure, or incomplete. Try to prove it.`,
  })
  vscode.window.showInformationMessage("GLM Frankenstein: Beast Claw parallel agent spawned. Review will be written to beast-claw-review.md.")
}

export async function mergeBeastClawCommand() {
  const cwd = getCwd()
  if (!cwd) return
  const reportPath = path.join(cwd, "beast-claw-review.md")
  if (!fs.existsSync(reportPath)) { vscode.window.showWarningMessage("beast-claw-review.md not found. Run Beast Claw parallel review first."); return }
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

export async function selfImproveCommand() {
  const cwd = getCwd()
  if (!cwd) return
  let glm: any
  try { glm = await buildGlmFromSettings() } catch (e: any) { vscode.window.showErrorMessage(`GLM Frankenstein: ${e.message}`); return }
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })
  const sysPrompt = `You are the GLM Frankenstein self-improvement module. Audit the agent's current configuration and propose CONCRETE patches. Output strict JSON: {"improvements":[{"area":"system-prompt|tool|command|ssb-integration|autonomy","severity":"critical|warning|info","proposal":"description","patch":"diff or code"}]}`
  const userPrompt = `Audit this GLM Frankenstein configuration and propose 5-10 concrete improvements:
- 14 Frankenstein commands (initiateReview, secretScan, beastClawParallel, selfImprove, etc.)
- SSB stack endpoints (ports 8787-8792, 3000)
- 17 MCP tools (ssb.scan_file, ssb.triple_review, ssb.run_patch, etc.)
- 14 SSB patches catalog
- Autonomy defaults (all alwaysAllow* = true)
- Self-review loop after every write
- Parallel Beast Claw protocol

Focus on: missing SSB capabilities, autonomy gaps, system prompt improvements, self-review loop gaps, Beast Claw improvements. Return strict JSON only.`
  let auditText = ""
  try {
    const messages: any[] = [{ role: "user", content: userPrompt }]
    const stream = (glm as any).createMessage(sysPrompt, messages)
    for await (const chunk of stream) { if (chunk.type === "text" && chunk.text) auditText += chunk.text }
  } catch (e: any) { auditText = `Audit failed: ${e.message}` }
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🔧 **Self-improvement audit complete.**

Here are the proposed improvements. For each one, decide whether to apply it now (using apply_diff on the relevant source file) or defer it.

\`\`\`json
${auditText}
\`\`\`

Apply at least the critical-severity proposals. After applying, summarize what changed and why.`,
  })
}

export async function reviewLastWriteCommand() {
  const cwd = getCwd()
  if (!cwd) return
  const recentFiles = findRecentlyModifiedFiles(cwd, 5 * 60 * 1000)
  if (recentFiles.length === 0) { vscode.window.showInformationMessage("GLM Frankenstein: no recently-modified files to review."); return }
  let glm: any
  try { glm = await buildGlmFromSettings() } catch (e: any) { vscode.window.showErrorMessage(`GLM Frankenstein: ${e.message}`); return }
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })
  const allComments: any[] = [], allHunks: any[] = []
  for (const file of recentFiles.slice(0, 10)) {
    let diff: string
    try {
      const { execSync } = require("child_process")
      diff = execSync(`git diff HEAD --no-color -- ${JSON.stringify(file)}`, { cwd, encoding: "utf8", maxBuffer: 5 * 1024 * 1024 })
    } catch {
      const content = fs.readFileSync(path.join(cwd, file), "utf8")
      const lines = content.split("\n").slice(0, 200)
      diff = `diff --git a/${file} b/${file}\n--- /dev/null\n+++ b/${file}\n@@ -0,0 +1,${lines.length} @@\n` + lines.map((l) => `+${l}`).join("\n")
    }
    const hunks = parseHunks(diff, file)
    allHunks.push(...hunks)
    for (const hunk of hunks) allComments.push(...await reviewHunkWithGLM(glm, hunk, `You are CodeRabbit-GLM doing a SELF-REVIEW pass. Find issues, propose fixes as suggestions. Return strict JSON array.`))
  }
  const md = formatReviewResult({ hunks: allHunks, comments: allComments, summary: `Self-review of ${recentFiles.length} recently-modified file(s): ${allComments.length} comment(s).` })
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🔍 **Self-review of recently written code complete.**

${md}

For every critical and warning finding, apply a fix using apply_diff or edit_file. Then re-verify with read_file.`,
  })
}

function findRecentlyModifiedFiles(root: string, withinMs: number): string[] {
  const now = Date.now()
  const results: { path: string; mtime: number }[] = []
  const queue: string[] = [root]
  const IGNORE = new Set(["node_modules", ".git", "dist", "build", "out", ".next", ".vscode", ".idea", "coverage", ".cache", "__pycache__", ".pytest_cache", ".venv", "venv", "env", ".mypy_cache", ".turbo", ".parcel-cache", "target", ".gradle", ".stack-work", "logs", "pids", "scanner_data"])
  while (queue.length > 0) {
    const dir = queue.shift()!
    let entries: fs.Dirent[]
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { continue }
    for (const entry of entries) {
      if (IGNORE.has(entry.name)) continue
      const full = path.join(dir, entry.name)
      if (entry.isDirectory()) queue.push(full)
      else if (entry.isFile()) {
        try {
          const stat = fs.statSync(full)
          if (now - stat.mtimeMs < withinMs) results.push({ path: path.relative(root, full), mtime: stat.mtimeMs })
        } catch {}
      }
    }
  }
  results.sort((a, b) => b.mtime - a.mtime)
  return results.map((r) => r.path)
}

export async function tripleReviewLastWriteCommand() {
  const cwd = getCwd()
  if (!cwd) return
  const recentFiles = findRecentlyModifiedFiles(cwd, 5 * 60 * 1000)
  if (recentFiles.length === 0) { vscode.window.showInformationMessage("GLM Frankenstein: no recently-modified files to review."); return }
  let glm: any
  try { glm = await buildGlmFromSettings() } catch (e: any) { vscode.window.showErrorMessage(`GLM Frankenstein: ${e.message}`); return }
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })
  const allResults: any[] = []
  for (const file of recentFiles.slice(0, 10)) {
    const content = fs.readFileSync(path.join(cwd, file), "utf8")
    for (const c of findSecrets(content, file)) allResults.push(await tripleReviewGlm(c, glm))
  }
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", {
    userInput: `🔐 **Triple-review of recently written code complete (GLM-5.2 powered).**

${formatTripleReview(allResults)}

For every real_secret verdict, propose a remediation: rotate the secret, move it to an env var, add to .gitignore, or document why it's a false positive.`,
  })
}
