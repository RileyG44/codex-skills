# Codex skills

Reusable Codex skills for Faber Industrial Supply workflows.

## Install

Clone this repository, then copy the desired skill folder into the local Codex skills directory:

```powershell
git clone https://github.com/RileyG44/codex-skills.git
Copy-Item -Recurse -Force .\codex-skills\skills\pud-rfq-history-report "$env:USERPROFILE\.codex\skills\"
```

The skill-specific instructions and dependencies are in each folder's `SKILL.md` and `references/` files.

## Included skills

- `pud-rfq-history-report`: extract Grant County PUD RFQs, research purchase history, and produce a verified printable PDF report.
