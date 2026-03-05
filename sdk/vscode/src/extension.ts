/**
 * BlockThrough VS Code Extension
 *
 * Scaffold for a VS Code extension that shows real-time LLM cost
 * in the status bar and provides commands for viewing stats and
 * waste scores. This is a scaffold only — the VS Code API calls
 * use stubs that would be replaced with real implementations.
 *
 * Activation: on startup (via onStartupFinished event)
 * Deactivation: disposes the status bar item and polling interval
 */

import * as vscode from "vscode";

// -- Types matching the BlockThrough API response shapes -----------------------

interface StatsResponse {
  total_requests: number;
  total_cost_usd: number;
  total_tokens: number;
  failure_rate: number;
}

interface WasteScoreResponse {
  waste_score: number;
  total_potential_savings_usd: number;
}

// -- API Client (stub) -------------------------------------------------------

class BlockThroughApiClient {
  private baseUrl: string;
  private apiKey: string;

  constructor(baseUrl: string, apiKey: string) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.apiKey = apiKey;
  }

  private async request<T>(path: string): Promise<T> {
    const headers: Record<string, string> = {
      "Content-Type": "application/json",
    };
    if (this.apiKey) {
      headers["Authorization"] = `Bearer ${this.apiKey}`;
    }

    const response = await fetch(`${this.baseUrl}${path}`, { headers });
    if (!response.ok) {
      throw new Error(`BlockThrough API ${response.status}: ${response.statusText}`);
    }
    return (await response.json()) as T;
  }

  async getStats(): Promise<StatsResponse> {
    return this.request<StatsResponse>("/api/v1/stats/summary");
  }

  async getWasteScore(): Promise<WasteScoreResponse> {
    return this.request<WasteScoreResponse>("/api/v1/stats/waste-score");
  }
}

// -- Extension lifecycle -----------------------------------------------------

let statusBarItem: vscode.StatusBarItem | undefined;
let pollInterval: ReturnType<typeof setInterval> | undefined;
let apiClient: BlockThroughApiClient | undefined;

export function activate(context: vscode.ExtensionContext): void {
  const config = vscode.workspace.getConfiguration("blockthrough");

  // Initialize API client
  const apiUrl = config.get<string>("apiUrl", "http://localhost:8100");
  const apiKey = config.get<string>("apiKey", "");
  apiClient = new BlockThroughApiClient(apiUrl, apiKey);

  // Status bar item — shows real-time session cost
  if (config.get<boolean>("statusBar.enabled", true)) {
    statusBarItem = vscode.window.createStatusBarItem(
      vscode.StatusBarAlignment.Right,
      100,
    );
    statusBarItem.command = "blockthrough.showSessionStats";
    statusBarItem.tooltip = "BlockThrough: Click to view session stats";
    statusBarItem.text = "$(pulse) AP: --";
    statusBarItem.show();
    context.subscriptions.push(statusBarItem);

    // Start polling for cost updates
    const refreshMs = config.get<number>("statusBar.refreshIntervalMs", 30000);
    updateStatusBar();
    pollInterval = setInterval(updateStatusBar, refreshMs);
  }

  // Register commands
  context.subscriptions.push(
    vscode.commands.registerCommand("blockthrough.showSessionStats", showSessionStats),
    vscode.commands.registerCommand("blockthrough.showWasteScore", showWasteScore),
    vscode.commands.registerCommand("blockthrough.configure", configureApiUrl),
  );

  // Watch for config changes so the user can update the API URL without reloading
  context.subscriptions.push(
    vscode.workspace.onDidChangeConfiguration((e) => {
      if (e.affectsConfiguration("blockthrough")) {
        const newConfig = vscode.workspace.getConfiguration("blockthrough");
        const newUrl = newConfig.get<string>("apiUrl", "http://localhost:8100");
        const newKey = newConfig.get<string>("apiKey", "");
        apiClient = new BlockThroughApiClient(newUrl, newKey);
      }
    }),
  );
}

export function deactivate(): void {
  if (pollInterval) {
    clearInterval(pollInterval);
    pollInterval = undefined;
  }
  statusBarItem?.dispose();
}

// -- Status bar updater ------------------------------------------------------

async function updateStatusBar(): Promise<void> {
  if (!statusBarItem || !apiClient) {
    return;
  }

  try {
    const stats = await apiClient.getStats();
    const costStr = stats.total_cost_usd.toFixed(2);
    statusBarItem.text = `$(pulse) AP: $${costStr}`;
    statusBarItem.tooltip =
      `BlockThrough Session Stats\n` +
      `Requests: ${stats.total_requests}\n` +
      `Cost: $${costStr}\n` +
      `Tokens: ${stats.total_tokens.toLocaleString()}\n` +
      `Failure rate: ${(stats.failure_rate * 100).toFixed(1)}%`;
  } catch {
    statusBarItem.text = "$(pulse) AP: offline";
    statusBarItem.tooltip = "BlockThrough: Could not reach API server";
  }
}

// -- Commands ----------------------------------------------------------------

async function showSessionStats(): Promise<void> {
  if (!apiClient) {
    vscode.window.showErrorMessage("BlockThrough: API client not initialized");
    return;
  }

  try {
    const stats = await apiClient.getStats();

    const panel = vscode.window.createWebviewPanel(
      "blockthroughStats",
      "BlockThrough: Session Stats",
      vscode.ViewColumn.One,
      {},
    );

    panel.webview.html = `
      <!DOCTYPE html>
      <html>
      <head>
        <style>
          body { font-family: var(--vscode-font-family); padding: 20px; color: var(--vscode-foreground); }
          .stat { margin: 12px 0; }
          .label { font-weight: bold; }
          .value { font-size: 1.4em; }
        </style>
      </head>
      <body>
        <h1>BlockThrough Session Stats</h1>
        <div class="stat">
          <span class="label">Total Requests:</span>
          <span class="value">${stats.total_requests.toLocaleString()}</span>
        </div>
        <div class="stat">
          <span class="label">Total Cost:</span>
          <span class="value">$${stats.total_cost_usd.toFixed(4)}</span>
        </div>
        <div class="stat">
          <span class="label">Total Tokens:</span>
          <span class="value">${stats.total_tokens.toLocaleString()}</span>
        </div>
        <div class="stat">
          <span class="label">Failure Rate:</span>
          <span class="value">${(stats.failure_rate * 100).toFixed(1)}%</span>
        </div>
      </body>
      </html>
    `;
  } catch (err) {
    vscode.window.showErrorMessage(
      `BlockThrough: Failed to fetch stats — ${err}`,
    );
  }
}

async function showWasteScore(): Promise<void> {
  if (!apiClient) {
    vscode.window.showErrorMessage("BlockThrough: API client not initialized");
    return;
  }

  try {
    const waste = await apiClient.getWasteScore();
    const scorePct = (waste.waste_score * 100).toFixed(1);
    const savings = waste.total_potential_savings_usd.toFixed(2);

    vscode.window.showInformationMessage(
      `BlockThrough Waste Score: ${scorePct}% — ` +
        `Potential savings: $${savings}/period`,
    );
  } catch (err) {
    vscode.window.showErrorMessage(
      `BlockThrough: Failed to fetch waste score — ${err}`,
    );
  }
}

async function configureApiUrl(): Promise<void> {
  const currentUrl = vscode.workspace
    .getConfiguration("blockthrough")
    .get<string>("apiUrl", "http://localhost:8100");

  const newUrl = await vscode.window.showInputBox({
    prompt: "Enter BlockThrough API URL",
    value: currentUrl,
    placeHolder: "http://localhost:8100",
  });

  if (newUrl !== undefined) {
    await vscode.workspace
      .getConfiguration("blockthrough")
      .update("apiUrl", newUrl, vscode.ConfigurationTarget.Global);

    vscode.window.showInformationMessage(
      `BlockThrough API URL updated to: ${newUrl}`,
    );
  }
}
