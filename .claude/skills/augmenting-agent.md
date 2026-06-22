# Augmenting Agent Skill

Customize a core agent-network agent for this project via `augment/agents/{name}.yaml`.
Adds project-specific skills, checkpoints, verify commands, references, and plan gating
without forking the agent's base prompt.

## Trigger

`/augmenting-agent [agent-name]`

## Supported agent names

| Name | Role |
|---|---|
| `coder` | Writes production code |
| `tester` | Writes and runs tests |
| `architect` | Designs systems and plans |
| `reviewer` | Reviews code and PRs |
| `verifier` | Confirms changes work end-to-end |
| `spec-agent` | Writes specs and acceptance criteria |
| `context-agent` | Gathers codebase context |

## YAML schema

```yaml
# augment/agents/{name}.yaml
agent: {name}

# Slash-command skills this agent loads in this project
skills: []

# Require plan-mode approval before acting on these categories
plan_gating:
  enabled: false
  require_approval_for: []

# Shell commands to run after the agent finishes work
verify_commands: []

# Rules enforced before or after the agent acts
checkpoints:
  pre_code: []
  post_code: []

# Key files the agent must read before starting any task
references: []
```

## Instructions

When this skill is invoked:

1. **Determine the target agent.**  
   Use the argument from `/augmenting-agent [name]` if provided.  
   Otherwise, ask the user: "Which agent do you want to augment?" and show the supported list.

2. **Read existing config.**  
   If `augment/agents/{name}.yaml` exists, read it and show the user what is already set.

3. **Gather customizations interactively.**  
   Walk through each section. For each, ask what (if anything) to add. Use project context
   from `CLAUDE.md` and `ROADMAP.md` to suggest sensible defaults:

   - **skills** — slash-command skills the agent should load (e.g. `code-review`, `verify`, `run`)
   - **plan_gating** — categories that need plan-mode approval before acting  
     (Swugl defaults: `migrations`, `billing-changes`, `css-changes`)
   - **verify_commands** — commands to run to confirm the work is correct  
     (Swugl default: `.venv/bin/pytest tests/ -v`)
   - **checkpoints.pre_code** — rules the agent checks before writing code  
     (Swugl default: read CLAUDE.md + ROADMAP.md, confirm branch safety, confirm billing tier)
   - **checkpoints.post_code** — rules the agent checks after writing code  
     (Swugl default: run tests, check cross-family isolation)
   - **references** — files the agent should always read first  
     (Swugl defaults: `CLAUDE.md`, `ROADMAP.md`)

4. **Write the file.**  
   Create or overwrite `augment/agents/{name}.yaml` with the merged result.  
   Preserve any existing keys the user did not touch.

5. **Report.**  
   Show a summary of what was written. Offer to commit the file.

## Swugl project defaults to suggest

These are good starting points based on this project's CLAUDE.md:

```yaml
plan_gating:
  enabled: true
  require_approval_for:
    - migrations          # always run flask db migrate + upgrade
    - billing-changes     # billing.py / Stripe / @requires_plan changes
    - css-changes         # CSS is Jeffrey's branch; never touch on main

verify_commands:
  - ".venv/bin/pytest tests/ -v"

checkpoints:
  pre_code:
    - "Read CLAUDE.md and ROADMAP.md"
    - "Confirm feature tier (free vs paid) — apply @requires_plan if paid"
    - "Confirm no CSS/layout changes on main branch"
    - "Use current_user.active_family_id, never bare current_user.family_id"
  post_code:
    - "Run .venv/bin/pytest tests/ -v"
    - "Verify cross-family data isolation if touching queries"
    - "Run flask db upgrade if a migration was generated"

references:
  - CLAUDE.md
  - ROADMAP.md
  - app/models.py
  - app/routes.py
```
