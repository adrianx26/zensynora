---
name: powershell-scripting
description: Write safe, readable PowerShell scripts (Windows PowerShell 5.1 and PowerShell 7) with parameters, error handling and help.
---

# PowerShell scripting

## Style

- Use approved verbs and full cmdlet names (`Get-ChildItem`, not `gci`) in scripts.
- Start scripts with `[CmdletBinding()]` and a `param()` block; validate input
  (`[ValidateNotNullOrEmpty()]`, `[ValidateSet()]`, `[ValidateRange()]`).
- Add comment-based help (`.SYNOPSIS`, `.PARAMETER`, `.EXAMPLE`).
- Prefer objects over text: return `[pscustomobject]`, format only at the end.
- Use `Join-Path` for paths, `-LiteralPath` for user-supplied paths.

## Safety

- `Set-StrictMode -Version Latest` and `$ErrorActionPreference = 'Stop'`; wrap risky parts in
  `try { } catch { Write-Error ...; exit 1 }`.
- Anything that changes or deletes things supports `-WhatIf`/`-Confirm`
  (`[CmdletBinding(SupportsShouldProcess)]` and `if ($PSCmdlet.ShouldProcess(...))`).
- Never put passwords in scripts: use `Get-Credential`, `SecretManagement`, or environment variables.
- Say which version the script needs (`#Requires -Version 7.2`) when you use 7-only features
  (`??`, `ForEach-Object -Parallel`, ternary operator).

## Deliver

- The full script in one block, then how to run it (`pwsh -File .\script.ps1 -Param value`)
  and what it changes.
- Mention execution policy only if it will block the user (`Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`).
