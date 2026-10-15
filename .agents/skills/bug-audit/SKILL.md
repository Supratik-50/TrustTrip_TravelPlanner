---
name: bug-audit
description: Perform strict root-cause investigations of reported defects without modifying production code.
---

# TrustTrip Bug Audit Skill

Use this workflow to perform an evidence-based investigation and produce an audit report without modifying production code.

## Required Workflow
```
BUG
 ↓
REPRODUCE
 ↓
TRACE UI
 ↓
TRACE API
 ↓
TRACE SERVICE
 ↓
TRACE DATABASE
 ↓
TRACE PROVIDER
 ↓
IDENTIFY SOURCE OF TRUTH
 ↓
ROOT CAUSE
 ↓
REPORT
```

## Required Audit Report Format

When auditing any defect, output the report using the following structure:

```markdown
### [BUG-ID] <Descriptive Title>

- **OBSERVED:** <What symptom was observed in browser/test/log>
- **REPRODUCTION:** <Exact steps or curl/test to reproduce>
- **EXPECTED:** <What business contract expects>
- **ACTUAL:** <What currently occurs>
- **DATA FLOW:** <Traced path: Component -> API -> Service -> Store -> DB>
- **ROOT CAUSE:** <Exact file, function, line, and rationale>
- **EVIDENCE:** <File paths, line numbers, code snippets, test failures>
- **AFFECTED FILES:** <Full list of touched files>
- **PROPOSED FIX:** <Smallest surgical remediation plan>
- **REGRESSION RISK:** <Risk assessment and impacted components>

**DO NOT MODIFY YET.**
```
