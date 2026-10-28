#Requires -Version 7
<#
.SYNOPSIS
    Start the local dev stack: verifies Ollama + model, then runs backend and frontend.

.PARAMETER Model
    Ollama model tag to use. Defaults to $env:OLLAMA_MODEL, then qwen3:8b.

.PARAMETER SkipOllama
    Skip the Ollama checks (e.g. when running with LLM_PROVIDER=fake).
#>
param(
    [string]$Model = $(if ($env:OLLAMA_MODEL) { $env:OLLAMA_MODEL } else { 'qwen3:8b' }),
    [switch]$SkipOllama
)

$ErrorActionPreference = 'Stop'
$RepoRoot = Split-Path -Parent $PSScriptRoot
$BackendDir = Join-Path $RepoRoot 'backend'
$FrontendDir = Join-Path $RepoRoot 'frontend'

# ---------------------------------------------------------------------------
# Ollama: ensure server is reachable and the model is pulled
# ---------------------------------------------------------------------------

function Test-OllamaServer {
    try {
        Invoke-RestMethod -Uri 'http://localhost:11434/api/version' -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

if (-not $SkipOllama) {
    if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
        throw 'ollama is not installed. Install from https://ollama.com or rerun with -SkipOllama.'
    }

    if (-not (Test-OllamaServer)) {
        Write-Host 'Starting ollama serve...'
        Start-Process ollama -ArgumentList 'serve' -WindowStyle Hidden
        # Poll for up to ~15s while the server boots.
        foreach ($attempt in 1..30) {
            if (Test-OllamaServer) { break }
            Start-Sleep -Milliseconds 500
        }
        if (-not (Test-OllamaServer)) { throw 'ollama serve did not become ready on :11434.' }
    }

    # `ollama list` prints a header row then "NAME ID SIZE MODIFIED"; match the NAME column.
    $installed = ollama list | Select-Object -Skip 1 | ForEach-Object { ($_ -split '\s+')[0] }
    if ($installed -notcontains $Model) {
        Write-Host "Pulling model $Model (first run only)..."
        ollama pull $Model
        if ($LASTEXITCODE -ne 0) { throw "ollama pull $Model failed." }
    }
    Write-Host "Ollama ready with $Model."
}

# ---------------------------------------------------------------------------
# Backend + frontend
# ---------------------------------------------------------------------------

$env:OLLAMA_MODEL = $Model

Write-Host 'Starting backend on http://localhost:8000 ...'
$backend = Start-Process uv -WorkingDirectory $BackendDir -NoNewWindow -PassThru -ArgumentList @(
    'run', 'uvicorn', 'main:app', '--app-dir', 'src', '--reload', '--port', '8000'
)

try {
    Write-Host 'Starting frontend on http://localhost:5173 ...'
    Push-Location $FrontendDir
    npm run dev
} finally {
    Pop-Location
    # Stop uvicorn (and its reload worker) when the frontend exits / Ctrl+C.
    if ($backend -and -not $backend.HasExited) {
        Write-Host 'Stopping backend...'
        taskkill /PID $backend.Id /T /F | Out-Null
    }
}
