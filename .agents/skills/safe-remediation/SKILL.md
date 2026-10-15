---
name: safe-remediation
description: Implement surgical fixes only after root cause is established and proven with tests.
---

# TrustTrip Safe Remediation Skill

Use this workflow to implement bug fixes and code enhancements safely after a root-cause audit has been approved.

## Workflow
```
Audit
 ↓
Verify Root Cause
 ↓
Smallest Change
 ↓
Unit Test Creation/Update
 ↓
Targeted Regression (Affected Subsystem)
 ↓
Full Regression (All Test Suites)
 ↓
Runtime Validation (Browser/API)
 ↓
Report
```

## Mandatory Rules
1. **Never Fix Symptoms Blindly:** Do not add string parsing fallbacks or catch-all try-except blocks when upstream data is malformed. Fix the source.
2. **Never Refactor Unrelated Code:** Touch only the lines directly necessary to remedy the audited root cause.
3. **Never Fabricate Data:** Ensure all IDs, amounts, currencies, and names come from authoritative models or user input.
4. **Never Bypass Existing Contracts:** Respect Section 8 revision rules, Section 9 authorization rules, and Section 10 payment guards.
5. **Never Modify Frozen Architecture Without Explicit Approval:** Wave 1 and verified baseline features must remain untouched.
