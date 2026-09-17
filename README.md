# Codex skills

Reusable Codex skills for Faber Industrial Supply workflows.

## Install

Clone this repository, then copy the desired skill folder into the local Codex skills directory:

```powershell
git clone https://github.com/RileyG44/codex-skills.git
Copy-Item -Recurse -Force .\codex-skills\skills\<skill-name> "$env:USERPROFILE\.codex\skills\"
```

The skill-specific instructions and dependencies are in each folder's `SKILL.md` and `references/` files.

## Included skills

None at the moment.

## Moved

- `pud-rfq-history-report` (Grant County PUD RFQ extraction and purchase-history research) moved into the private PUD Orders plugin on 2026-09-17, which now bundles its scripts and rules. Don't install it from here; older copies in this repo's history are no longer maintained.
