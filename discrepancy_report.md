# Discrepancy Report: README.md and big_diagram.md

## README.md Issues

### 1. Mermaid Diagram Syntax & Logic
- **Syntax Error**: Extra `end` tag in the `Infra` subgraph block.
- **Undefined Node**: `Caching` is referenced in `Agent -.-> Caching` but not defined.
- **Duplicate Styling**: `classDef core` is defined twice.
- **Node Paths**:
  - `Medic Agent` path is shown as `medic_agent.py` (relative to root? or myclaw?), but it is at `myclaw/agents/medic_agent.py`.
  - `State Store` and `Async Scheduler` are in `myclaw/state_store.py` and `myclaw/async_scheduler.py`, but grouped under an `Infra` subgraph which might imply a directory `myclaw/infra/`.

### 2. Branding/Naming
- Mixed usage of "ZenSynora" and "MyClaw". While intentional as "ZenSynora (MyClaw)", some commands might use `zensynora` and others `myclaw` in examples. `pyproject.toml` defines both as entry points.

## big_diagram.md Issues

### 1. Function Name Consistency
- `MedicAgent` functions listed as `check_health()` instead of `check_system_health()` as registered in tools.
- `Agent` functions listed as `chat()` - `Agent` class has `think()` and `chat()`. `think()` is the primary entry point.

### 2. Completeness
- `Strategies (strategies.py)` in Swarm subgraph - the file is `myclaw/swarm/strategies.py`. Correct.
