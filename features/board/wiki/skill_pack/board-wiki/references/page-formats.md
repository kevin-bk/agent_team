# Wiki page formats — starter shapes (fallback only)

These are **starter shapes for an empty wiki**. If the wiki already has pages,
**match its existing structure instead** — copy the heading layout, status /
"last updated" line, naming, and cross-reference style of the surrounding docs.
A real wiki documents its own conventions in `index.md`; that always wins over
the templates below.

Whatever the shape: keep pages short, interlinked, and honest about what is
known vs. inferred. Add or edit pages and commit on your task branch.

## Front matter (starter pages only)

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
create a near-duplicate) and follow that page's own format: update its facts /
open-questions sections, bump whatever "updated" / "last updated" marker it uses,
and add your task key to its sources. If the new knowledge contradicts what the
page said, change it and state the contradiction explicitly in your commit
message so the reviewer sees it.
