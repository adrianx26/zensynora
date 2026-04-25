## CLI command wiring verification report

The project uses a single `argparse` based entry point located in **`myclaw/cli.py`**.  Each sub‑command is registered via:
```python
sub = subparsers.add_parser('command_name')
sub.set_defaults(func=corresponding_function)
```
The verification script cross‑checked every `add_parser` call against the function name supplied to `set_defaults`.  The result is a full one‑to‑one mapping – every command listed in the help output has a valid function, and every public CLI function is wired.

| CLI command | Bound function | Definition exists? | Notes |
|------------|----------------|--------------------|-------|
| `onboard` | `onboard()` (myclaw/onboard.py) | ✅ | Entry point for interactive onboarding. |
| `agent` | `agent()` (cli.py) | ✅ | Starts the REPL. |
| `gateway` | `gateway()` (cli.py) | ✅ | Launches the API gateway server. |
| `mcp_server` | `mcp_server()` (cli.py) | ✅ | Starts the MCP server. |
| `knowledge` | `knowledge()` (cli.py) | ✅ | Wrapper for knowledge‑base commands. |
| `search` | `search(query)` (cli.py) | ✅ | Performs a knowledge search. |
| `write` | `write()` (cli.py) | ✅ | Creates a new knowledge entry. |
| `read` | `read(permalink)` (cli.py) | ✅ | Retrieves an entry by permalink. |
| `list` | `list()` (cli.py) | ✅ | Lists all entries. |
| `sync` | `sync()` (cli.py) | ✅ | Syncs local store with remote. |
| `tags` | `tags()` (cli.py) | ✅ | Tag management. |
| `memory` | `memory()` (cli.py) | ✅ | Shows memory usage stats. |
| `list_sessions` | `list_sessions()` (cli.py) | ✅ | Lists active sessions. |
| `clear` | `clear(user_id)` (cli.py) | ✅ | Clears stored data for a user. |
| `swarm` | `swarm()` (cli.py) | ✅ | Starts swarm coordination mode. |
| `status` | `status()` (cli.py) | ✅ | Prints overall system status. |
| `skills` | `skills()` (cli.py) | ✅ | Lists available skill modules. |
| `list_skills` | `list_skills()` (cli.py) | ✅ | Detailed skill list. |
| `webui` | `webui(port)` (cli.py) | ✅ | Runs the web UI on the given port. |
| `benchmark` | `benchmark(model, provider)` (cli.py) | ✅ | Runs a benchmark for a model/provider pair. |
| `hardware` | `hardware()` (cli.py) | ✅ | Shows detected CPU/GPU/RAM. |
| `config` | `config_cmd()` (cli.py) | ✅ | General config sub‑commands. |
| `config_encrypt` | `config_encrypt()` (cli.py) | ✅ | Encrypts the config file. |
| `config_decrypt` | `config_decrypt()` (cli.py) | ✅ | Decrypts the config file. |
| `config_status` | `config_status()` (cli.py) | ✅ | Shows encryption status. |
| `audit` | `audit()` (cli.py) | ✅ | Runs a system audit. |
| `audit_verify` | `audit_verify()` (cli.py) | ✅ | Verifies audit signatures. |
| `audit_export` | `audit_export(output_path)` (cli.py) | ✅ | Exports audit logs. |
| `audit_status` | `audit_status()` (cli.py) | ✅ | Shows audit status. |
| `gdpr` | `gdpr()` (cli.py) | ✅ | GDPR command group. |
| `gdpr_delete` | `gdpr_delete(user_id, dry_run)` (cli.py) | ✅ | Deletes user data (dry‑run optional). |
| `gdpr_export` | `gdpr_export(user_id, output)` (cli.py) | ✅ | Exports user data. |
| `mfa` | `mfa()` (cli.py) | ✅ | MFA command group. |
| `mfa_setup` | `mfa_setup(user_id)` (cli.py) | ✅ | Sets up MFA for a user. |
| `mfa_verify` | `mfa_verify(user_id, code)` (cli.py) | ✅ | Verifies an MFA code. |
| `mfa_disable` | `mfa_disable(user_id)` (cli.py) | ✅ | Disables MFA. |
| `mfa_status` | `mfa_status(user_id)` (cli.py) | ✅ | Shows MFA status. |
| `metering` | `metering()` (cli.py) | ✅ | Metering command group. |
| `metering_status` | `metering_status(user_id)` (cli.py) | ✅ | Shows usage quota. |
| `metering_set_quota` | `metering_set_quota(user_id, quota_name, limit_value)` (cli.py) | ✅ | Sets a quota. |
| `spaces` | `spaces()` (cli.py) | ✅ | Space management command group. |
| `spaces_create` | `spaces_create(name, owner, description)` (cli.py) | ✅ | Creates a new space. |
| `spaces_list` | `spaces_list(user_id)` (cli.py) | ✅ | Lists spaces for a user. |
| `spaces_members` | `spaces_members(space_id)` (cli.py) | ✅ | Lists space members. |
| `spaces_add_member` | `spaces_add_member(space_id, user_id, role, added_by)` (cli.py) | ✅ | Adds a member. |
| `spaces_remove_member` | `spaces_remove_member(space_id, user_id, removed_by)` (cli.py) | ✅ | Removes a member. |
| `spaces_delete` | `spaces_delete(space_id, owner)` (cli.py) | ✅ | Deletes a space. |

**Conclusion**
*Every CLI sub‑command is correctly bound to a real function.* No missing or duplicate bindings were found.

**Next steps (optional)**
1. Add unit tests that invoke `parser.parse_args(['onboard', …])` and assert `func` is the expected callable. 
2. Consider migrating to `click` or `typer` for clearer command definitions and automatic help generation, but the current wiring is already solid.
