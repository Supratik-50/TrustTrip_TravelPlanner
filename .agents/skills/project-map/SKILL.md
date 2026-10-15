---
name: project-map
description: Navigate and trace subsystems across the TrustTrip repository using PROJECT_MAP.md before broad exploration.
---

# Project Map Navigation Skill

Use this workflow to navigate the TrustTrip codebase efficiently without indiscriminate file reading.

## When to Use
- Before investigating any bug or requirement.
- When tracing data boundaries between Frontend, API, Services, and Database.
- To locate authoritative stores and schemas for a specific entity.

## Workflow

1. **Read PROJECT_MAP.md:**
   - Locate the relevant subsystem in `PROJECT_MAP.md` (e.g. Planning, Revisions, Flights, Hotels, Payments, Authorization).
2. **Identify Subsystem & Authoritative Store:**
   - Review file ownership, input/output data, and authoritative tables.
3. **Inspect Relevant Files Only:**
   - Open only the specific entry point, router, service, and store files identified in the map.
4. **Verify Implementation Against Contracts:**
   - Cross-check API contracts in `API_CONTRACTS.md` with active router and Pydantic models.
5. **Verify Before Action:**
   - Ensure the runtime state matches expectations before proposing or writing code.
