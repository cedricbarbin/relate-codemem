# relate-codemem installer for Windows (PowerShell 5.1 or later).
#
#   .\install.ps1                                   from a checkout
#   irm <raw url>/install.ps1 | iex                 from the published repository
#
# Parameters:
#   -Embeddings local|ollama|openai|none   default embedding provider (default: local)
#   -Model NAME  -Url URL  -ApiKeyEnv VAR  provider settings
#   -Yes                                   install missing prerequisites without asking
param(
    [ValidateSet("", "local", "ollama", "openai", "none")][string]$Embeddings = "",
    [string]$Model = "",
    [string]$Url = "",
    [string]$ApiKeyEnv = "",
    [switch]$Yes
)
$ErrorActionPreference = "Stop"
$RepoUrl = if ($env:RELATE_CODEMEM_REPO) { $env:RELATE_CODEMEM_REPO } else { "https://github.com/cedricbarbin/relate-codemem" }

function Confirm-Step([string]$Question) {
    if ($Yes) { return $true }
    $answer = Read-Host "$Question [Y/n]"
    return ($answer -eq "" -or $answer -match "^(y|yes)$")
}

function Update-SessionPath {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                "$env:USERPROFILE\.local\bin;$env:LOCALAPPDATA\Programs\relate"
}

Update-SessionPath

# 1. uv: installs the tool in its own environment, with a Python it manages
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    if (Confirm-Step "uv (Python tool installer, https://docs.astral.sh/uv) is missing. Install it?") {
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        Update-SessionPath
    } else {
        Write-Host "uv is required: https://docs.astral.sh/uv/getting-started/installation/"; exit 1
    }
}

# 2. relate: the embedded graph database
if (-not (Get-Command relate -ErrorAction SilentlyContinue)) {
    if (Confirm-Step "relate (embedded graph database, https://relatedb.ai) is missing. Install it?") {
        Invoke-RestMethod https://relatedb.ai/install.ps1 | Invoke-Expression
        Update-SessionPath
    } else {
        Write-Host 'relate is required: powershell -ExecutionPolicy ByPass -c "irm https://relatedb.ai/install.ps1 | iex"'; exit 1
    }
}

# 3. embedding provider
if (-not $Embeddings) {
    $Embeddings = "local"
    if (-not $Yes) {
        Write-Host ""
        Write-Host "Semantic search needs embeddings. Default provider for this machine:"
        Write-Host "  1) local   on this machine, no service, ~220 MB model downloaded once (default)"
        Write-Host "  2) ollama  an Ollama server you run"
        Write-Host "  3) openai  an OpenAI-compatible API (file contents are sent to it)"
        Write-Host "  4) none    full-text search only"
        switch (Read-Host "Choice [1]") {
            { $_ -in "2", "ollama" } { $Embeddings = "ollama" }
            { $_ -in "3", "openai" } { $Embeddings = "openai" }
            { $_ -in "4", "none" } { $Embeddings = "none" }
        }
    }
}

# 4. the tool itself: from this checkout when run inside one, else from the repository
$Source = "git+$RepoUrl"
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "pyproject.toml"))) { $Source = $PSScriptRoot }
Write-Host ""
Write-Host "Installing relate-codemem from $Source ..."
if ($Embeddings -eq "local") {
    uv tool install --python 3.12 --force --with "fastembed>=0.4" $Source
} else {
    uv tool install --python 3.12 --force $Source
}
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
uv tool update-shell | Out-Null
$Bin = Join-Path (uv tool dir --bin) "relate-codemem.exe"

# 5. machine setup: default provider, global Claude Code skill, checks
$SetupArgs = @("setup", "--embeddings", $Embeddings)
if ($Model) { $SetupArgs += @("--model", $Model) }
if ($Url) { $SetupArgs += @("--url", $Url) }
if ($ApiKeyEnv) { $SetupArgs += @("--api-key-env", $ApiKeyEnv) }
Write-Host ""
& $Bin @SetupArgs

Write-Host ""
Write-Host "Done. In a project:  relate-codemem init     (or /relate-codemem-init in Claude Code)"
if (-not (Get-Command relate-codemem -ErrorAction SilentlyContinue)) {
    Write-Host "Open a new terminal first so that relate-codemem is on your PATH."
}
