import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import { delimiter, dirname, resolve } from "node:path";

import { checkDecision, type Decision, type DecisionRequest } from "./decision.js";
import process from "node:process";

const pjRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const script = resolve(pjRoot, "harness", "decide.py");

/**
 * One JSON Request on stdin, One JSON decision on stdout.
 * Diagnostics go to stderr.
 */

export async function askRawHarness(
    request: Record<string, unknown>,
    timeoutMs = 40_000,
): Promise<Record<string, unknown>> {
    return new Promise((resolveDecision, rejectDecision) => {
        const child = spawn(process.env.HARNESS_PYTHON || "python3", [script], {
            cwd: pjRoot,
            env: {
                ...process.env,
                PYTHONPATH: [pjRoot, process.env.PYTHONPATH].filter(Boolean).join(delimiter),
            },
            stdio: ["pipe", "pipe", "pipe"],
        });
        let stdout = "";
        let stderr = "";
        let settled = false;
        const finish = (error?: Error, value?: Record<string, unknown>) => {
            if (settled) return;
            settled = true;
            clearTimeout(timer);
            if (error) rejectDecision(error);
            else resolveDecision(value!);
        };
        const timer = setTimeout(() => {
            child.kill("SIGKILL");
            finish(new Error(`Python harness timed out after ${timeoutMs} ms`));
        }, timeoutMs);
        child.on("error", (error) => finish(error));
        child.stdout.setEncoding("utf8");
        child.stderr.setEncoding("utf8");
        child.stdout.on("data", (chunk: string) => { stdout += chunk; });
        child.stderr.on("data", (chunk: string) => { stderr += chunk; });
        child.on("close", (code) => {
            if (settled) return;
            if (code !== 0) {
                finish(new Error(`Python harness exited ${code}: ${stderr.trim()}`));
                return;
            }
            try {
                const decision = JSON.parse(stdout.trim()) as Record<string, unknown>;
                finish(undefined, decision);
            } catch (error) {
                finish(new Error(`not valid harness decision: ${String(error)}; stdout = ${stdout.slice(0, 200)}`));
            }
        });
        child.stdin.end(JSON.stringify(request) + "\n");
    });
}

export async function askPythonHarness(request: DecisionRequest): Promise<Decision> {
    const answer = await askRawHarness(request as unknown as Record<string, unknown>);
    return checkDecision(request, answer as unknown as Decision)
}
