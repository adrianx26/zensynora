## Dead‑code report (auto‑generated)

The following definitions were detected as **unused** (no internal call sites) after a repository‑wide static analysis.

| Module / File | Unused definitions | Remarks |
|---------------|-------------------|---------|
| `myclaw/tools_backup.py` | All functions (`list_tools_backup`, `register_tool_backup`, `register_mcp_tool_backup`, …) | Legacy backup of the toolbox utilities. Not referenced anywhere in the production code. |
| `myclaw/benchmark_runner.py` | `run_all_benchmarks` (wrapper) | Intended for manual execution (`python -m myclaw.benchmark_runner`). Not called by the library. |
| `myclaw/onboard.py` | `run_onboard` | CLI entry point (`onboard` command). Only invoked from the command line, not from other modules – **not dead**, just external. |
| `myclaw/knowledge/advanced_search.py` | `semantic_search` (if not imported elsewhere) | Currently only used in documentation examples; the main code uses `search` from `knowledge/db.py`. |
| Test modules (`myclaw/tests/*`) | All `test_*` functions | Expected – they are exercised by the test runner, not by the runtime code. |

All other functions listed in `function_list.md` have at least one reference either from other modules, from the CLI dispatcher, or from the asynchronous worker pipeline.

**Action items**
1. Delete `tools_backup.py` if it is truly obsolete.
2. If `semantic_search` is meant to be part of the public API, add an import/usage site; otherwise move it to a `examples/` folder.
3. Keep `run_all_benchmarks` as a convenience script or document its purpose clearly.

No further dead code was found.
