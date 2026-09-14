# Deferred release requests

## Next update after v0.2.5: website and project repository CLI attribution

Registered on 2026-09-14. Status: deferred until the next release; do not change or republish v0.2.5 for this request.

Original request: "remove my name (first line in the cli) keep only the website, not now, in the next update (register)".

Clarification on 2026-09-14: "also remove the third line, keep only the website (2nd line and the project repo 4th line)". This supersedes the earlier website-only interpretation.

In the BAT startup attribution block, remove line 1 (personal name/SaherLabs) and line 3 (personal GitHub profile). Keep line 2 (website) and line 4 (project repository), in this order:

```text
Website: https://saherlabs.dev/
Project: https://github.com/ahmedsahernouh/petrel-headless-extractor
```

Keep the normal product/version heading, progress, timer and operational messages. This request concerns displayed CLI attribution only; documentation, source comments and license attribution are outside its scope.

When implementing the next update, check the actual BAT startup output and update banner-specific tests and documentation as needed. Mark this entry completed with the release version after validation.
