<#
.SYNOPSIS
  ragkit command line for Windows PowerShell 5.1+ and PowerShell 7. Bash twin: scripts/rag.sh.
.EXAMPLE
  .\scripts\rag.ps1 setup
  .\scripts\rag.ps1 up -WithOllama
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)] [string] $Command = "help",
  [Parameter(Position = 1, ValueFromRemainingArguments = $true)] [string[]] $Rest = @(),
  [switch] $WithOllama,
  [switch] $Gpu,
  [switch] $Release,
  [switch] $Build,
  [switch] $Observability,
  [switch] $Volumes,
  [switch] $Push,
  [switch] $Yes,
  [string] $Tag
)
$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $PSScriptRoot
$Backend = Join-Path $Root "backend"
$Frontend = Join-Path $Root "frontend"
$EnvFile = Join-Path $Root ".env"
$Python = Join-Path $Backend ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) { $Python = Join-Path $Backend ".venv/bin/python" }
$ComposeArgs = @("compose", "-f", (Join-Path $Root "deploy/compose.yaml"), "--env-file", $EnvFile)

function Info([string] $Message) { Write-Host "> $Message" -ForegroundColor Cyan }
function Fail([string] $Message) { Write-Host "x $Message" -ForegroundColor Red; exit 1 }
function Have([string] $Name) { return [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Confirm([string] $Question) { return (Read-Host "$Question [y/N]") -match '^[Yy]$' }

function Import-DotEnv {
  if (-not (Test-Path $EnvFile)) { return }
  foreach ($line in Get-Content $EnvFile) {
    if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$' -and -not $line.TrimStart().StartsWith("#")) {
      $name = $Matches[1]; $value = $Matches[2].Trim().Trim('"').Trim("'")
      if (-not (Test-Path "Env:$name")) { Set-Item -Path "Env:$name" -Value $value }
    }
  }
}

function Ensure-Env {
  if (-not (Test-Path $EnvFile)) {
    Copy-Item (Join-Path $Root ".env.example") $EnvFile
    Info "created .env from .env.example"
  }
}

function Invoke-Ragkit([string[]] $Arguments) {
  if (-not (Test-Path $Python)) { Fail "backend not installed; run: .\scripts\rag.ps1 setup" }
  Push-Location $Root
  try { & $Python -m ragkit.cli @Arguments; return $LASTEXITCODE } finally { Pop-Location }
}

function Invoke-Setup {
  Ensure-Env
  Info "installing the backend"
  if (Have "uv") {
    Push-Location $Backend; try { uv sync --frozen --extra rerank --extra dev } finally { Pop-Location }
  } else {
    $py = if (Have "py") { "py" } elseif (Have "python") { "python" } else { Fail "Python 3.10+ is required (or install uv: https://docs.astral.sh/uv/)" }
    if (-not (Test-Path (Join-Path $Backend ".venv"))) { & $py -m venv (Join-Path $Backend ".venv") }
    $script:Python = Join-Path $Backend ".venv\Scripts\python.exe"
    & $Python -m pip install --quiet --upgrade pip
    & $Python -m pip install --quiet -e "$Backend[rerank,dev]"
  }
  if (Have "npm") {
    Info "installing the frontend"
    Push-Location $Frontend; try { npm ci --no-audit --no-fund } finally { Pop-Location }
  } else {
    Info "Node.js not found: skipping the frontend (needed only for 'dev' and 'run'; Docker builds it for you)"
  }
  if ((Have "ollama") -and ($Yes -or (Confirm "Pull the default models into Ollama now?"))) { Invoke-Models "pull" }
  Info "done. Next: .\scripts\rag.ps1 dev   (or .\scripts\rag.ps1 up for Docker)"
}

function Invoke-Doctor {
  if (Test-Path $Python) { [void](Invoke-Ragkit @("doctor")) } else { Info "backend not installed yet (run setup)" }
  if ((Have "docker") -and (docker compose version 2>$null)) { Info "docker compose: $(docker compose version --short)" }
  else { Info "docker compose: not found (needed only for 'up')" }
  if (Have "node") { Info "node: $(node --version)" } else { Info "node: not found (needed only for 'dev'/'run')" }
}

function Invoke-Dev {
  Ensure-Env
  if (-not (Test-Path (Join-Path $Frontend "node_modules"))) { Fail "frontend not installed; run: .\scripts\rag.ps1 setup" }
  Info "API on http://127.0.0.1:8000 (auto-reload) - UI on http://localhost:5173 - Ctrl+C stops both"
  $api = Start-Process -FilePath $Python -ArgumentList @("-m", "ragkit.cli", "serve", "--with-worker", "--reload") `
    -WorkingDirectory $Root -NoNewWindow -PassThru
  try {
    Push-Location $Frontend
    npm run dev -- --strictPort
  } finally {
    Pop-Location
    if (-not $api.HasExited) { Stop-Process -Id $api.Id -Force }
  }
}

function Invoke-Run {
  Ensure-Env
  if (-not (Test-Path (Join-Path $Frontend "node_modules"))) { Fail "frontend not installed; run: .\scripts\rag.ps1 setup" }
  Info "building the UI"
  Push-Location $Frontend; try { npm run build | Out-Null } finally { Pop-Location }
  $env:RAG_UI_DIST_DIR = Join-Path $Frontend "dist"
  $port = if ($env:RAG_PORT) { $env:RAG_PORT } else { "8000" }
  Info "open http://127.0.0.1:$port"
  [void](Invoke-Ragkit @("serve", "--with-worker"))
}

function Invoke-Up {
  Ensure-Env
  if (-not (Have "docker")) { Fail "Docker is required for 'up' (or use: .\scripts\rag.ps1 run)" }
  $arguments = @() + $ComposeArgs
  if ($Gpu) { $arguments += @("-f", (Join-Path $Root "deploy/compose.gpu.yaml")) }
  if ($Release) { $arguments += @("-f", (Join-Path $Root "deploy/compose.release.yaml")) }
  if ($WithOllama -or $Gpu) { $arguments += @("--profile", "ollama"); $env:RAG_OLLAMA_URL = "http://ollama:11434" }
  if ($Observability) { $arguments += @("--profile", "observability") }
  $arguments += @("up", "-d", "--wait")
  if ($Build) { $arguments += "--build" }
  Info "starting containers (first run builds images; this takes a few minutes)"
  & docker @arguments
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  $ui = if ($env:RAG_UI_PORT) { $env:RAG_UI_PORT } else { "8080" }
  $apiPort = if ($env:RAG_API_PORT) { $env:RAG_API_PORT } else { "8000" }
  Info "UI:  http://localhost:$ui"
  Info "API: http://localhost:$apiPort/docs"
  if ($Observability) { $promPort = if ($env:RAG_PROMETHEUS_PORT) { $env:RAG_PROMETHEUS_PORT } else { "9090" }; Info "Prometheus: http://localhost:$promPort" }
}

function Invoke-Down {
  if ($Volumes) {
    if (-not (Confirm "Delete ALL data volumes (vectors, chatbots, traces, models)?")) { return }
    & docker @ComposeArgs --profile ollama --profile observability down --volumes
  } else {
    & docker @ComposeArgs --profile ollama --profile observability down
  }
}

function Invoke-Build {
  $version = if ($Tag) { $Tag } elseif ($env:RAG_VERSION) { $env:RAG_VERSION } else { "local" }
  $prefix = if ($env:RAG_IMAGE_PREFIX) { $env:RAG_IMAGE_PREFIX } else { "ragkit" }
  $dockerfile = Join-Path $Root "deploy/docker/Dockerfile"
  foreach ($target in @("api", "ui", "aio")) {
    Info "building $prefix-${target}:$version"
    if ($Push) {
      docker buildx build --platform linux/amd64,linux/arm64 -f $dockerfile --target $target -t "$prefix-${target}:$version" --push $Root
    } else {
      docker build -f $dockerfile --target $target -t "$prefix-${target}:$version" $Root
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
  }
}

function Invoke-Models([string] $Action) {
  if ($Action -ne "pull") { Fail "usage: rag models pull" }
  $models = if ($env:RAG_PULL_MODELS) { $env:RAG_PULL_MODELS } else { "nomic-embed-text qwen3.5:9b" }
  if (Have "ollama") {
    foreach ($model in $models.Split(" ", [StringSplitOptions]::RemoveEmptyEntries)) { Info "ollama pull $model"; ollama pull $model }
  } else {
    & docker @ComposeArgs --profile ollama run --rm ollama-init
  }
}

function Invoke-Passthrough([string[]] $Arguments) {
  if (Test-Path $Python) { exit (Invoke-Ragkit $Arguments) }
  & docker @ComposeArgs exec api ragkit @Arguments
}

function Show-Usage {
  @"
ragkit - build agentic RAG chatbots over your own documents

  setup [-Yes]                  install dependencies, create .env, optionally pull models
  doctor                        check that everything needed is reachable
  dev                           hot-reload API + UI for development (http://localhost:5173)
  run                           production-style local run without Docker (http://127.0.0.1:8000)
  up [-WithOllama] [-Gpu] [-Release] [-Build] [-Observability]
                                start the Docker stack (http://localhost:8080)
  down [-Volumes]               stop the Docker stack (-Volumes deletes data, asks first)
  build [-Tag X] [-Push]        build the api, ui and all-in-one images
  logs [service]                follow container logs
  status                        container status
  models pull                   pull the configured models into Ollama
  kb list|build|export ...      manage chatbots headlessly
  eval NAME GOLDEN.jsonl        evaluate retrieval on a golden set
  reset                         delete local state (asks first)
"@
}

Import-DotEnv
switch ($Command) {
  "setup" { Invoke-Setup }
  "doctor" { Invoke-Doctor }
  "dev" { Invoke-Dev }
  "run" { Invoke-Run }
  "up" { Invoke-Up }
  "down" { Invoke-Down }
  "build" { Invoke-Build }
  "logs" { & docker @ComposeArgs logs -f @Rest }
  "status" { & docker @ComposeArgs ps }
  "models" { Invoke-Models ($Rest | Select-Object -First 1) }
  { $_ -in @("kb", "eval") } { Invoke-Passthrough (@($Command) + $Rest) }
  "reset" {
    if (Confirm "Delete local state in .ragkit (chatbots, embedded vectors, traces)?") {
      Remove-Item -Recurse -Force (Join-Path $Root ".ragkit") -ErrorAction SilentlyContinue
      Info "local state removed"
    }
  }
  default { Show-Usage }
}
