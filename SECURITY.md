# Security

Diffasaurus analyzes Microsoft 365 CSV snapshots and can launch the included
PowerShell report scripts. Please do not report suspected vulnerabilities in a
public issue.

When reporting a security concern, include the affected version, reproduction
steps, and impact while removing tenant identifiers, access tokens, report
contents, and other confidential information.

## Data safety

- Real tenant CSV files must never be committed.
- Authentication tokens and PowerShell profiles must never be committed.
- Review Microsoft Graph consent prompts before running a report.
- Prefer least-privilege delegated permissions and a test tenant while
  evaluating changes.
