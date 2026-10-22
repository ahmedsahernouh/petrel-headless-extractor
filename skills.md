# Included agent skills

- [Petrel CRS drive/folder audit](skills/petrel-crs-drive-audit/SKILL.md): discover PET/PTD pairs and run CRS/datum metadata audits serially, retaining per-project evidence and RESULTS.csv.
- [Agent integration](AGENT_INTEGRATION.md): setup and scope.

The complete skill directory includes SKILL.md, agents/openai.yaml and scripts/audit.ps1. The package also includes FieldViewer_CRS_Audit.ps1 and the shared CRS batch engine. No model subscription, API key or GUI control is needed by the runner. Use outputs outside the source project; source stores remain read-only.
