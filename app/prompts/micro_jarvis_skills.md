# Micro Jarvis Skills (Compiled)

Auto-generated micro execution allowlist from SQL `skills` registry.
- skill_count: 3

## Calendar (`skill.calendar.core`)

- markdown_path: `app/prompts/skills/calendar_skill.md`
- micro_functions:
  - calendar.view -> calendar.view

## Lights (`skill.home.lights`)

- markdown_path: `app/prompts/skills/lights_skill.md`
- micro_functions:
  - lights.set_switch -> home.set_switch
  - lights.get_switch_state -> home.get_switch_state

## Lists (`skill.lists.core`)

- markdown_path: `app/prompts/skills/lists_skill.md`
- micro_functions:
  - lists.add_item -> lists.add_item
  - lists.get_items -> lists.get_items
