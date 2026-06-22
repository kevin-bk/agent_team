# Wiki page formats

Keep pages short, interlinked, and honest about what is known vs. inferred.
Pages live under `pages/` in the wiki repo; add or edit them and commit on your
task branch. Use whichever shape fits; all share the same front matter.

## Front matter (all pages)

```markdown
---
title: <human title>
type: entity | concept | summary | comparison | decision
updated: YYYY-MM-DD
sources: [<task key or source name>, ...]
---
```

## Entity page

A person, system, service, repo, customer — anything with a stable identity.

```markdown
## Summary
One paragraph: what it is and why it matters.

## Facts
- Verifiable statements, each with a source.

## Relationships
- Links to related pages by name.

## Open questions
- Things not yet known or confirmed.
```

## Concept page

An idea, pattern, or mechanism.

```markdown
## Definition
## How it works
## Trade-offs / caveats
## Related pages
```

## Summary page

A digest of a source (a finished task, an imported doc, a discussion).

```markdown
## Source
What this summarises (task key / doc).

## Key takeaways
- ...

## Implications for this board
- ...
```

## Comparison page

```markdown
## Options
| Option | Pros | Cons | Notes |
|---|---|---|---|

## Recommendation
State it, and mark it as an **inference** unless a decision was actually made.
```

## Updating an existing page

If your knowledge changes an existing page, **edit that page in place** (don't
create a near-duplicate): update its `## Facts` / `## Open questions`, bump
`updated:`, and add the task key to `sources:`. If the new knowledge contradicts
what the page said, change it and state the contradiction explicitly in your
commit message so the reviewer sees it.
