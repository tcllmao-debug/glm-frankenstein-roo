/**
 * GLM Frankenstein — SSB Autostart Service
 * Boots the SSB Python stack on extension activation.
 */
import * as vscode from "vscode"
import * as path from "path"
import * as fs from "fs"
import * as cp from "child_process"

const SSB_ASSET_DIR = "assets/ssb"

function getExtensionContext(): vscode.ExtensionContext | undefined {
  return (global as any).__glmFrankensteinContext as vscode.ExtensionContext | undefined
}

function ssbDir(): string {
  const ctx = getExtensionContext()
  if (!ctx) return ""
  return vscode.Uri.joinPath(ctx.extensionUri, SSB_ASSET_DIR).fsPath
}

function python(): string {
  return process.platform === "win32" ? "python" : "python3"
}

export interface SsbStatus {
  running: boolean
  services: Record<string, { port: number; listening: boolean; pid: number | null }>
  logFile?: string
  ssbDir: string
}

export class SsbService {
  private static instance: SsbService
  private autostartProc: cp.ChildProcess | null = null
  private statusPoll: NodeJS.Timeout | null = null
  private output: vscode.OutputChannel

  private constructor() {
    this.output = vscode.window.createOutputChannel("GLM Frankenstein SSB")
  }

  static getInstance(): SsbService {
    if (!SsbService.instance) SsbService.instance = new SsbService()
    return SsbService.instance
  }

  async autostart(): Promise<void> {
    const dir = ssbDir()
    if (!dir) { this.output.appendLine("[SSB] No extension context — skipping autostart."); return }
    if (!fs.existsSync(path.join(dir, "frankenstein_autostart.py"))) {
      this.output.appendLine(`[SSB] autostart script not found at ${dir}/frankenstein_autostart.py`); return
    }
    if (!await this.findPython()) { this.output.appendLine("[SSB] Python 3 not found on PATH — skipping autostart."); return }
    this.output.appendLine(`[SSB] Autostarting from ${dir}…`)
    try {
      this.autostartProc = cp.spawn(python(), ["frankenstein_autostart.py", "start"], {
        cwd: dir,
        env: { ...process.env, SSB_DIR: dir, PYTHONUNBUFFERED: "1" },
        detached: false,
        stdio: ["ignore", "pipe", "pipe"],
      })
      this.autostartProc.stdout?.on("data", (d) => this.output.append(`[SSB] ${d}`))
      this.autostartProc.stderr?.on("data", (d) => this.output.append(`[SSB ERR] ${d}`))
      this.autostartProc.on("exit", (code) => {
        this.output.appendLine(`[SSB] autostart exited with code ${code}`)
        this.autostartProc = null
      })
      const statusFile = path.join(dir, "frankenstein_status.json")
      this.statusPoll = setInterval(() => {
        try {
          if (fs.existsSync(statusFile)) {
            const s = JSON.parse(fs.readFileSync(statusFile, "utf8"))
            this.output.appendLine(`[SSB] status: ${Object.entries(s.services || {}).map(([k, v]: any) => `${k}:${v.listening ? "UP" : "DOWN"}:${v.port}`).join("  ")}`)
          }
        } catch {}
      }, 30_000)
    } catch (e: any) { this.output.appendLine(`[SSB] Autostart failed: ${e.message}`) }
  }

  private async findPython(): Promise<boolean> {
    for (const candidate of ["python3", "python", "/usr/bin/python3"]) {
      try { cp.execSync(`${candidate} --version`, { stdio: "pipe" }); return true } catch {}
    }
    return false
  }

  async stop(): Promise<void> {
    const dir = ssbDir()
    if (!dir) return
    try { cp.execSync(`${python()} frankenstein_autostart.py stop`, { cwd: dir, stdio: "pipe" }); this.output.appendLine("[SSB] stop issued") }
    catch (e: any) { this.output.appendLine(`[SSB] stop failed: ${e.message}`) }
    if (this.statusPoll) { clearInterval(this.statusPoll); this.statusPoll = null }
  }

  async status(): Promise<SsbStatus> {
    const dir = ssbDir()
    if (!dir) return { running: false, services: {}, ssbDir: "" }
    const statusFile = path.join(dir, "frankenstein_status.json")
    let raw: any = {}
    try { if (fs.existsSync(statusFile)) raw = JSON.parse(fs.readFileSync(statusFile, "utf8")) } catch {}
    const services: SsbStatus["services"] = {}
    for (const [name, info] of Object.entries(raw.services || {})) {
      const i = info as any
      services[name] = { port: i.port, listening: i.listening, pid: i.pid ?? null }
    }
    return { running: Object.values(services).some((s) => s.listening), services, logFile: path.join(dir, "logs", "autostart.log"), ssbDir: dir }
  }

  async statusMarkdown(): Promise<string> {
    const s = await this.status()
    if (!s.ssbDir) return "_SSB not available (no extension context)._"
    const lines: string[] = ["## SSB Stack Status", "", `**Directory:** \`${s.ssbDir}\``, `**Overall:** ${s.running ? "🟢 Running" : "🔴 Stopped"}`, "", "| Service | Port | Listening | PID |", "|---------|------|-----------|-----|"]
    for (const [name, info] of Object.entries(s.services)) lines.push(`| ${name} | ${info.port} | ${info.listening ? "✅" : "❌"} | ${info.pid ?? "—"} |`)
    lines.push("", "**Endpoints:**")
    lines.push("- Scanner: http://127.0.0.1:8787", "- Persistent connection: http://127.0.0.1:8788", "- Activation: http://127.0.0.1:8789", "- Portal (Next.js): http://localhost:3000", "- Triple-review: http://127.0.0.1:8790/triple-review", "- MCP bridge: ws://127.0.0.1:8791", "- CLI omni-runner: http://127.0.0.1:8792")
    return lines.join("\n")
  }
}
