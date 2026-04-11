# Medic Agent Change Management Enhancement

## Summary

The Medic Agent has been enhanced with comprehensive change management capabilities. These features enable the agent to:

1. **Continuously monitor logs** from all relevant sources
2. **Detect anomalies and failures** automatically
3. **Create change plans** with proper documentation
4. **Manage approval workflow** for changes
5. **Execute changes safely** with rollback capability
6. **Maintain audit history** of all changes
7. **Escalate to humans** when needed

## Files Added/Modified

### New Files

1. **`myclaw/agents/medic_change_mgmt.py`** (900+ lines)
   - `ChangeManagementSystem` - Core change management logic
   - `ChangeStatus`, `ChangePriority`, `ChangeType` - Enums for change classification
   - `ChangePlan` - TypedDict for change plan structure
   - `LogAnalyzer` - Log ingestion and anomaly detection
   - `ScheduledReviewSystem` - Automated review scheduling
   - Convenience functions for CLI/tool integration

### Modified Files

1. **`myclaw/agents/__init__.py`**
   - Exported new change management classes and functions

## Key Features

### 1. Change Plan Creation

```python
from myclaw.agents import create_change_plan, ChangeType, ChangePriority

result = await create_change_plan(
    title="Fix memory leak in agent.py",
    description="Patch to address memory leak issue",
    rationale="Detected in logs - memory usage increasing over time",
    change_type="patch",  # config, code, security, patch, hotfix
    priority="high",      # critical, high, medium, low
    affected_components=["myclaw/agent.py"],
    proposed_changes={"myclaw/agent.py": "new content here"},
    risks=["May break existing functionality"],
    rollback_steps=["Restore from backup"]
)
```

### 2. Approval Workflow

- **Auto-approval** for low-risk configuration changes (configurable)
- **Manual approval** required for code changes, security changes, high/critical priority
- **Audit trail** of all approvals with timestamps and approver identity

```python
from myclaw.agents import approve_change

result = await approve_change("CHG_20250111_120000_abc123", approved_by="operator")
```

### 3. Safe Execution with Rollback

```python
from myclaw.agents import execute_change

# Dry run first
result = await execute_change("CHG_20250111_120000_abc123", dry_run=True)

# Execute for real
result = await execute_change("CHG_20250111_120000_abc123", dry_run=False)
# Automatically creates backups and rolls back on failure
```

### 4. Log Analysis and Anomaly Detection

```python
from myclaw.agents import analyze_system_logs

# Analyze logs from last 60 minutes
report = analyze_system_logs(since_minutes=60)
print(report)
```

Anomaly patterns detected:
- ERROR, CRITICAL, FATAL messages
- Exceptions and Tracebacks
- Timeout errors
- Connection refused
- Permission denied
- Disk full / memory errors
- Segmentation faults

### 5. Continuous Monitoring

```python
from myclaw.agents import start_continuous_monitoring, stop_continuous_monitoring

# Start automated reviews every 60 minutes
start_continuous_monitoring()

# Stop monitoring
stop_continuous_monitoring()
```

When anomalies are detected:
1. Automatically creates a change plan
2. Auto-approves if low-risk
3. Executes the change
4. Logs everything to audit trail

### 6. Pending Changes Management

```python
from myclaw.agents import get_pending_changes, get_change_history

# View pending approvals
print(get_pending_changes())

# View change history
print(get_change_history(limit=10))
```

## Configuration

The change management system can be configured via:

- `auto_approve_low_risk` - Auto-approve low priority patches/hotfixes
- `auto_approve_config` - Auto-approve configuration changes
- `maintenance_window_start/end` - Restrict changes to maintenance windows
- `review_interval_minutes` - How often to run scheduled reviews

## Directory Structure

```
~/.myclaw/
├── medic/
│   ├── changes/              # Change plan storage
│   │   └── change_*.json
│   ├── backup/               # Automatic backups before changes
│   ├── audit_log.json        # Complete audit trail
│   └── ...
```

## Integration with Tools

The new functions can be registered as tools for the agent:

```python
from myclaw.tools import register_tool
from myclaw.agents import (
    create_change_plan, approve_change, execute_change,
    analyze_system_logs, get_pending_changes
)

# Register as available tools
register_tool("create_change_plan", create_change_plan)
register_tool("approve_change", approve_change)
register_tool("execute_change", execute_change)
register_tool("analyze_logs", analyze_system_logs)
register_tool("get_pending_changes", get_pending_changes)
```

## Security Features

1. **Least privilege** - Changes are made with minimal required access
2. **Audit trail** - Every action logged with timestamp and identity
3. **Backup before change** - Automatic backups enable rollback
4. **Approval workflow** - Human oversight for high-risk changes
5. **Maintenance windows** - Restrict changes to approved windows
6. **Dry-run capability** - Test changes before applying

## Error Handling

- Failed changes automatically trigger rollback
- All errors logged to audit trail
- Notifications sent for failures requiring human intervention
- Graceful degradation when components are unavailable

## Example Usage

### Manual Change Workflow

```python
import asyncio
from myclaw.agents import (
    create_change_plan, approve_change, execute_change,
    get_pending_changes
)

async def manual_fix():
    # 1. Create change plan
    result = await create_change_plan(
        title="Update config timeout",
        description="Increase timeout from 30s to 60s",
        rationale="Users experiencing timeouts on slow connections",
        change_type="config",
        priority="medium",
        affected_components=["config.py"],
        proposed_changes={"config.py": "TIMEOUT = 60"}
    )
    print(result)
    
    # 2. Check pending (if not auto-approved)
    print(get_pending_changes())
    
    # 3. Approve if needed
    await approve_change("CHG_20250111_120000_abc123", "admin")
    
    # 4. Execute
    result = await execute_change("CHG_20250111_120000_abc123")
    print(result)

asyncio.run(manual_fix())
```

### Automated Monitoring

```python
from myclaw.agents import start_continuous_monitoring

# Start monitoring - will automatically detect and fix issues
start_continuous_monitoring()
```

## Future Enhancements

Potential improvements:
1. Integration with external change management systems (ServiceNow, Jira)
2. Webhook notifications for approvals
3. Multi-operator approval workflows
4. Integration with testing frameworks for pre-change validation
5. Metrics collection for change success rates
6. Machine learning for anomaly detection
