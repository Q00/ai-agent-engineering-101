# PR Description: [week-05] 25512090

## What I built
- **Market MCP Server (`market_server.py`)**: A Model Context Protocol server adhering to the 2026-07-28 stateless spec over Streamable HTTP. It implements OAuth 2.1 Bearer token authentication, negotiation state management, turn enforcement, token-to-negotiation binding, and limit enforcement. It supports tools: `get_negotiation`, `propose`, `accept_proposal`, `reject_proposal`, and `refuse`, along with indirect prompt injection simulation in buyer views.
- **Experiment Runner (`market_runner.py`)**: Automates test execution across 6 scenarios and 4 conditions (`prompt`, `server`, `prompt_inject`, `server_inject`) with 3 repeats per scenario. Generates `results.csv`, detailed execution logs in `logs/`, and security verification tests in `auth_checks.txt`.
- **Scenario Set (`scenarios.json`)**: 6 diverse negotiation scenarios mixing feasible and infeasible deals (reserve vs budget).
- **Documentation & Verification**: Comprehensive `REPORT.md` and `auth_checks.txt`.

## What I tried and discarded
- *Attempted*: Relying solely on system prompts for enforcing budget limits during prompt injection tests. *Discarded*: Proved vulnerable; models readily succumbed to injected budget increases. Moving enforcement to the MCP server token claims (`server_inject`) completely eliminated violations.
- *Attempted*: Stateful MCP sessions. *Discarded*: Discarded in favor of the 2026-07-28 stateless MCP protocol where `negotiation_id` is passed explicitly in tool arguments and session state is managed on the server.

## How to run
1. Start the market server and run experiments:
   ```bash
   python submissions/25512090/week-05/market_runner.py
   ```
2. Run local CI checks:
   ```bash
   python scripts/check_week05.py submissions/25512090/week-05
   ```

## Checklist
- [x] `scenarios.json` committed first.
- [x] `results.csv` generated with exact header and required repeats/conditions.
- [x] Server condition violations = 0.
- [x] `logs/` directory contains complete execution captures.
- [x] `auth_checks.txt` contains 4 required security/auth check lines (including HTTP 401).
- [x] `REPORT.md` includes setup, results table, FIPA-ACL comparison table, and interpretation paragraph.
- [x] Local verification script `check_week05.py` passes successfully.
