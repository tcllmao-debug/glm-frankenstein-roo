/**
 * GLM Frankenstein — command implementations.
 * CodeRabbit-style review + SSB triple-review + SSB stack control.
 */
import * as vscode from "vscode"
import { Package } from "../shared/package"
import { ClineProvider } from "../core/webview/ClineProvider"
import { reviewUncommitted, getCommitDiff, parseHunks, reviewHunkWithGLM, formatReviewResult } from "./codeReview"
import { scanPathGlm, formatReviewResult as formatTripleReview, findSecrets, tripleReviewGlm } from "./tripleReview"
import { buildApiHandler } from "../api"
import { ContextProxy } from "../core/config/ContextProxy"

function getCwd(): string { return vscode.workspace.workspaceFolders?.[0]?.uri.fsPath ?? "" }
function getExtensionContext(): vscode.ExtensionContext | undefined { return (global as any).__glmFrankensteinContext as vscode.ExtensionContext | undefined }

async function buildGlmFromSettings() {
  const ctx = getExtensionContext()
  if (!ctx) throw new Error("Extension context not initialized yet.")
  const contextProxy = await ContextProxy.getInstance(ctx)
  const providerSettings = contextProxy.getProviderSettings()
  return buildApiHandler(providerSettings)
}

async function pickCommit(cwd: string): Promise<string | undefined> {
  const { execSync } = require("child_process")
  let log: string
  try { log = execSync("git log --oneline -20", { cwd, encoding: "utf8" }) }
  catch { vscode.window.showErrorMessage("GLM Frankenstein: git log failed."); return }
  const items = log.trim().split("\n").map((line: string) => { const [sha, ...rest] = line.split(" "); return { label: sha, description: rest.join(" ") } })
  const picked = await vscode.window.showQuickPick(items, { placeHolder: "Pick a commit to review" })
  return picked?.label
}

export async function initiateReview() { await reviewUncommittedCommand() }

export async function reviewUncommittedCommand() {
  const cwd = getCwd()
  if (!cwd) { vscode.window.showWarningMessage("GLM Frankenstein: No workspace open."); return }
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) { vscode.window.showErrorMessage("GLM Frankenstein: could not find chat panel."); return }
  let glm
  try { glm = await buildGlmFromSettings() }
  catch (e: any) { vscode.window.showErrorMessage(`GLM Frankenstein: cannot build API handler — ${e.message}`); return }
  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", { userInput: "🔍 **CodeRabbit-GLM review started** — scanning uncommitted git changes…" })
  try {
    const result = await reviewUncommitted(glm, cwd, (progress) => vscode.window.setStatusBarMessage(`GLM Frankenstein: ${progress}`, 3000))
    const md = formatReviewResult(result)
    await ClineProvider.handleCodeAction("newTask", "NEW_TASK", { userInput: `Here is the review output:\n\n${md}\n\nPlease apply any critical fixes.` })
  } catch (e: any) { vscode.window.showErrorMessage(`GLM Frankenstein review failed: ${e.message}`) }
}

export async function reviewCommitCommand() {
  const cwd = getCwd()
  if (!cwd) return
  const sha = await pickCommit(cwd)
  if (!sha) return
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  let glm
  try { glm = await buildGlmFromSettings() } catch (e: any) { vscode.window.showErrorMessage(`GLM Frankenstein: ${e.message}`); return }
  const { files } = getCommitDiff(cwd, sha)
  if (Object.keys(files).length === 0) { vscode.window.showInformationMessage(`GLM Frankenstein: commit ${sha} has no diff.`); return }
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", { userInput: `🔍 **Reviewing commit ${sha}** — ${Object.keys(files).length} file(s) changed.` })
  const allHunks: any[] = []
  for (const [file, diff] of Object.entries(files)) allHunks.push(...parseHunks(diff, file))
  const sysPrompt = `You are CodeRabbit-GLM, a senior code reviewer embedded in VS Code as part of GLM Frankenstein. Reply with a strict JSON array of issues for this hunk.`
  const allComments: any[] = []
  for (const hunk of allHunks) allComments.push(...await reviewHunkWithGLM(glm, hunk, sysPrompt))
  const md = formatReviewResult({ hunks: allHunks, comments: allComments, summary: `Reviewed commit ${sha}: ${allHunks.length} hunk(s), ${allComments.length} comment(s).` })
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", { userInput: `Here is the commit review:\n\n${md}\n\nPlease apply any critical fixes.` })
}

export async function secretScanCommand() {
  const cwd = getCwd()
  if (!cwd) return
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })
  let glm: any = undefined
  try { glm = await buildGlmFromSettings() } catch {}
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "GLM Frankenstein: SSB triple-review scanning workspace…", cancellable: false },
    async (progress) => {
      progress.report({ message: "Scanning files…" })
      const results = await scanPathGlm(cwd, glm, 1000, (msg) => progress.report({ message: msg }))
      progress.report({ message: `Found ${results.length} candidate(s).` })
      const md = formatTripleReview(results)
      await ClineProvider.handleCodeAction("newTask", "NEW_TASK", { userInput: `🔐 **SSB Triple-Review scan complete (GLM-5.2 powered).**\n\n${md}\n\nPlease summarize the highest-risk findings and suggest remediation for any real_secret verdicts.` })
    },
  )
}

export async function tripleReviewCommand() {
  const editor = vscode.window.activeTextEditor
  if (!editor) { vscode.window.showWarningMessage("GLM Frankenstein: open a file first."); return }
  const file = vscode.workspace.asRelativePath(editor.document.uri)
  const content = editor.document.getText()
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  const candidates = findSecrets(content, file)
  let glm: any = undefined
  try { glm = await buildGlmFromSettings() } catch {}
  const results = await Promise.all(candidates.map((c) => tripleReviewGlm(c, glm)))
  const md = formatTripleReview(results)
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", { userInput: `🔐 **Triple-review of ${file}.**\n\n${md}` })
}

export async function handoffToAgentCommand() {
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) return
  await provider.postMessageToWebview({ type: "action", action: "focusInput" })
}

export async function ssbStartCommand() {
  const { SsbService } = await import("./SsbService")
  await vscode.window.withProgress(
    { location: vscode.ProgressLocation.Notification, title: "GLM Frankenstein: starting SSB stack…", cancellable: false },
    async () => { await SsbService.getInstance().autostart() },
  )
  vscode.window.showInformationMessage("GLM Frankenstein: SSB stack start requested. Check Output → 'GLM Frankenstein SSB' channel for logs.")
  await ssbStatusCommand()
}

export async function ssbStopCommand() {
  const { SsbService } = await import("./SsbService")
  await SsbService.getInstance().stop()
  vscode.window.showInformationMessage("GLM Frankenstein: SSB stack stop issued.")
}

export async function ssbStatusCommand() {
  const { SsbService } = await import("./SsbService")
  const md = await SsbService.getInstance().statusMarkdown()
  await vscode.commands.executeCommand(`${Package.name}.SidebarProvider.focus`)
  const provider = ClineProvider.getVisibleInstance()
  if (!provider) { vscode.window.showInformationMessage("GLM Frankenstein: SSB status — see output channel."); return }
  await provider.postMessageToWebview({ type: "action", action: "chatButtonClicked" })
  await ClineProvider.handleCodeAction("newTask", "NEW_TASK", { userInput: md })
}
