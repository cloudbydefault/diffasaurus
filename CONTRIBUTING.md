# Contributing

Thanks for helping Diffasaurus dig through tenant history.

1. Create a focused branch.
2. Keep tenant CSV files, credentials, tokens, and private PowerShell modules
   out of commits.
3. Add or update tests for behavior changes.
4. Run `python3 -m unittest discover -s tests -v`.
5. Explain the user-facing impact in the pull request.

Bug reports should include sanitized CSV headers and synthetic example rows
whenever report shape matters. Never attach real tenant exports.
