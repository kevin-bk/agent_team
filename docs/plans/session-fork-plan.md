# Session Fork Plan

Status: backlog / future feature.

This document captures the design direction for adding a Cursor-like "fork from
here" feature to Agent Team direct CLI conversations.

The feature should let a human fork a task conversation from a completed
assistant message, keep context up to that point, and continue in a new branch
without corrupting the parent conversation or workspace.

## Why This Matters

Coding-agent conversations are not only text. They also mutate files, run
commands, create artifacts, and leave behind hidden session state inside CLI
agents.

Because of that, a useful fork feature must decide two things:

1. What conversation context should the new branch inherit?
2. What file/workspace state should the new branch inherit?

If Agent Team only forks the chat transcript but continues in the same current
workspace, the user can get a misleading branch:

- The conversation says "continue from message 5".
- The filesystem is actually at message 12.
- The agent may reason from old context while reading new files.

That mismatch is dangerous for coding agents. The recommended design is:

```text
conversation fork + workspace checkpoint + fresh sandbox
```

Native ACP `session/fork` is useful when available, but it must be treated as an
optimization, not the source of truth.

## Current Research Summary

### ACP

ACP has an optional `session/fork` concept. It is currently described in the ACP
RFD as a way to create a new session from an existing session. The RFD also
discusses future direction around forking from a specific message, but Agent
Team should not assume every ACP agent supports message-level fork today.

Important implication:

- ACP fork support is capability-gated.
- Agent Team must probe runtime capabilities.
- Agent Team must provide a fallback for agents that do not support native fork.

### Claude Agent ACP

`@agentclientprotocol/claude-agent-acp@0.57.0` advertises
`sessionCapabilities.fork` and registers a `session/fork` handler.

The implementation creates a new Claude Code session using the parent session as
the resume source and passes a fork flag. This is the closest match to native
session fork.

Use native fork for Claude only when all of these are true:

- The source ACP session is live.
- The source point is the current completed head of the session.
- The adapter advertises `sessionCapabilities.fork`.
- The user is not asking to fork from an older message whose workspace snapshot
  must be restored independently.

### Codex ACP

`@agentclientprotocol/codex-acp@1.1.0` contains ACP protocol definitions for
`session/fork`, but the current agent initialization does not advertise `fork`
in `sessionCapabilities`, and the published server registration does not wire a
`session/fork` handler.

Treat Codex ACP as not supporting native fork for now.

Fallback strategy:

- Create a fresh ACP session.
- Bootstrap it with a bounded transcript summary/context up to the fork point.
- Point it at the forked workspace.

### Cursor CLI / Cursor ACP

Cursor IDE has a visible fork-session UX. The Cursor CLI currently exposes
session creation/resume commands, but public evidence for CLI-level fork parity
is weak. Cursor forum discussion has requested `--fork-session` or duplicate
chat support in the CLI.

Treat Cursor fork capability as unknown until runtime capability probing proves
otherwise.

Fallback strategy:

- Create a new Cursor chat/session.
- Bootstrap from transcript/context.
- Point it at the forked workspace.

## Existing Agent Team Context

Relevant current implementation:

- `AgentTeamConversation` represents one task-agent conversation attempt.
- `AgentTeamRun` represents one user/agent turn in a conversation.
- Direct CLI ACP session key is currently:

```text
cli:<engine>::<thread_id>
```

- ACP session IDs are persisted through `runtime/acp/store.py`.
- The ACP manager already has a technical `ask()` helper that uses
  `session/fork` for a one-off sub-query when the live session supports it.

Important limitation:

The existing `ask()` fork is not a user-visible persisted branch. It does not
create a new `AgentTeamConversation`, does not persist a new session mapping,
and does not fork workspace files.

## Product Behavior

### Entry Point

Show `Fork from here` on completed assistant messages in the task direct-CLI
chat.

Do not show or enable fork while:

- The assistant message is still streaming.
- The parent conversation has a running turn.
- The workspace has no available checkpoint and the requested fork point is not
  the current head.

### Fork Dialog

Suggested options:

1. `New workspace branch` - recommended.
2. `Read-only chat fork` - for exploration only.
3. `Fork from current files` - allowed only with explicit warning.

Default should be `New workspace branch`.

### User-Facing Copy

For normal coding fork:

```text
Create a new branch of this task from the selected message. The new conversation
will use the same context up to that point and a separate workspace snapshot, so
future file changes do not affect the original task.
```

For read-only fork:

```text
Create a read-only chat branch from this message. The agent can answer questions
using the prior context, but it should not edit files.
```

For unsafe current-files fallback:

```text
No workspace checkpoint is available for this older message. This fork will use
the current files, which may include changes made after the selected message.
```

## Recommended Architecture

### Core Principle

Do not rely on native CLI/ACP session fork as the durable fork mechanism.

Use Agent Team's own durable objects:

- conversation branch
- run checkpoint
- workspace snapshot
- sandbox lifecycle
- optional native ACP fork

Native ACP fork can improve continuity for agents that support it, but the
workspace snapshot is still required for correctness.

## Data Model Additions

### AgentTeamConversation

Add nullable fork metadata:

```text
parent_conversation_id: string nullable
forked_from_run_id: string nullable
fork_mode: string nullable
workspace_variant_id: string nullable
fork_title: string nullable
```

Suggested `fork_mode` values:

- `workspace_snapshot`
- `read_only_chat`
- `current_files`

Keep existing `attempt` semantics. Forked conversations can still have their own
attempt/reset lifecycle.

### AgentTeamWorkspaceVariant

Add a new table to represent task workspace branches:

```text
id: string primary key
task_id: string
parent_variant_id: string nullable
source_run_id: string nullable
kind: string
host_workspace_path: string
snapshot_ref: string nullable
git_ref: string nullable
created_by: string nullable
created_at: datetime
archived_at: datetime nullable
```

Suggested `kind` values:

- `main`
- `fork`

This lets multiple conversations for the same task point to different workspaces.

### AgentTeamRunCheckpoint

Create a checkpoint after every completed assistant run that may be forked:

```text
id: string primary key
run_id: string unique
task_id: string
conversation_id: string
workspace_variant_id: string
agent_alias: string
thread_id: string
checkpoint_kind: string
git_head: string nullable
git_ref: string nullable
diff_ref: string nullable
snapshot_path: string nullable
acp_session_key: string nullable
acp_session_id: string nullable
created_at: datetime
```

Suggested `checkpoint_kind` values:

- `git_ref`
- `filesystem_copy`
- `current_head_only`

## Workspace Fork Strategy

### Git Workspace

If the workspace contains a git repository:

1. After each completed run, create a hidden checkpoint ref or tag.
2. Capture dirty tracked changes.
3. Capture untracked files.
4. On fork, create a new worktree or copied workspace from that checkpoint.

Possible implementation:

```text
.agent-team/checkpoints/<run_id>/
  git-head.txt
  tracked.diff
  untracked.tar.zst
  metadata.json
```

Then fork workspace:

```text
workspaces/<board>/<task>/forks/<fork_id>/
```

Restore:

1. Checkout checkpoint head.
2. Apply tracked diff.
3. Extract untracked archive.
4. Write `.agent-team/FORK.md`.

### Non-Git Workspace

If no git repo exists:

1. Use a filesystem snapshot/copy at completed assistant messages.
2. Prefer hardlink/reflink copy if supported.
3. Fall back to normal recursive copy.

Possible layout:

```text
.agent-team/checkpoints/<run_id>/workspace.tar.zst
```

or:

```text
.agent-team/checkpoints/<run_id>/files/
```

### OpenSandbox

Forked conversations must get a fresh sandbox.

Do not reuse the parent sandbox because:

- It may contain process/session state after the fork point.
- It may contain files written after the checkpoint.
- ACP subprocesses may have in-memory state that does not match the fork.

For each fork:

1. Create/restore host workspace path.
2. Start a new sandbox for the fork workspace.
3. Mount the fork workspace.
4. Start a fresh sidecar.
5. Create/load/fork ACP session depending on engine capability.

## Conversation Context Strategy

### Transcript Source

Agent Team currently reconstructs messages from `AgentTeamRun`. For fallback
forks, build a transcript from:

- runs in the source conversation
- only up to and including `forked_from_run_id`
- user prompt
- assistant final answer
- relevant tool summaries if available
- plan/checkpoint metadata if useful

Do not include messages after the selected assistant run.

### Bootstrap Prompt For Fallback Fork

When native ACP fork is unavailable or not safe, create a new session and send a
bootstrap prompt before the user's first real fork prompt.

Template:

```text
You are continuing a forked branch of an Agent Team task.

This is a new agent session created from a previous conversation up to a
specific completed assistant message. Treat the following transcript and task
artifacts as prior context, but inspect the real workspace before making claims.

Rules:
- The workspace has been restored or copied to the fork point selected by the human.
- Do not assume later messages or later file changes from the parent branch exist.
- If the transcript conflicts with files on disk, trust the files and explain the mismatch.
- Keep future changes scoped to this fork branch.
- Do not write back to the parent branch unless explicitly asked.

Fork metadata:
- Source task: {{task_key}}
- Source conversation: {{parent_conversation_id}}
- Forked from run: {{forked_from_run_id}}
- Fork mode: {{fork_mode}}

Task summary:
{{task_summary}}

Transcript up to fork point:
{{transcript}}

Current fork workspace path:
{{workspace_path}}
```

The bootstrap prompt should be marked system/internal in Agent Team metadata if
the UI should not display it as a user message.

## Native ACP Fork Strategy

### When To Use Native ACP Fork

Use native ACP fork only when:

- Engine advertised `sessionCapabilities.fork`.
- Parent ACP session is live.
- Fork point is the current completed head of that session.
- The workspace fork has already been created.
- Agent Team can map the new fork conversation to the new ACP session id.

If these conditions are not true, use transcript bootstrap.

### Persisting Native Fork

If native fork is used:

1. Call `session/fork` on parent session.
2. Receive child session id.
3. Create new `AgentTeamConversation`.
4. Save ACP session mapping:

```text
cli:<engine>::<new_thread_id> -> <fork_session_id>
```

5. Continue future turns on the new conversation key.

### Important Limitation

ACP's current practical fork support is not enough by itself for "fork from any
old assistant message" unless the adapter can fork from a stable message id.

Until message-level fork is widely supported, Agent Team should treat native
fork as:

```text
fork current live head
```

For older points, use transcript bootstrap plus workspace checkpoint.

## Capability Matrix

| Engine | Native ACP fork today | Recommended behavior |
| --- | --- | --- |
| Claude Code via `@agentclientprotocol/claude-agent-acp` | Yes, capability advertised | Use native fork for live-head fork; otherwise fallback bootstrap |
| Codex via `@agentclientprotocol/codex-acp` | No reliable advertised fork in current package | Always fallback bootstrap for now |
| Cursor CLI/ACP | Unknown / not guaranteed | Capability probe; fallback bootstrap |

## API Design

### POST `/tasks/{task_id}/conversations/{conversation_id}/fork`

Request:

```json
{
  "from_run_id": "run_123",
  "mode": "workspace_snapshot",
  "title": "Try simpler implementation",
  "agent_alias": "cli:claude"
}
```

Response:

```json
{
  "conversation_id": "conv_new",
  "thread_id": "agentteam:T-1:cli_claude_fork_xxx:1",
  "workspace_variant_id": "wv_new",
  "workspace_path": ".../forks/fork_xxx",
  "used_native_acp_fork": false,
  "checkpoint_id": "chk_123",
  "warning": null
}
```

### GET `/tasks/{task_id}/forks`

Return fork tree for UI:

```json
[
  {
    "conversation_id": "conv_new",
    "parent_conversation_id": "conv_parent",
    "forked_from_run_id": "run_123",
    "workspace_variant_id": "wv_new",
    "title": "Try simpler implementation",
    "created_at": "..."
  }
]
```

## UI Design

### Message-Level Action

Add a menu on completed assistant messages:

```text
Fork from here
```

Only show on assistant messages where:

- run status is complete
- not streaming
- run has final answer
- run belongs to a direct CLI conversation or another forkable conversation

### Fork Tree

In task view, show:

- Main conversation
- Forked conversations
- Which message each fork came from
- Workspace branch status

Suggested labels:

```text
Main
Fork: Try simpler implementation
Forked from #12
```

### Workspace Indicator

The chat panel should show if the current conversation is using:

- main workspace
- fork workspace
- read-only fork

This prevents the user from accidentally thinking they are editing the main
task.

## Merge / Apply Later

Do not implement merge in v1.

But design fork workspaces so a later merge feature can compare:

```text
main workspace vs fork workspace
```

Future merge options:

- Show diff.
- Apply selected files.
- Create PR/patch.
- Replace main workspace with fork workspace.
- Archive losing branch.

## Recommended Phasing

### Phase 1: Durable Workspace Fork With Transcript Bootstrap

Implement:

- run checkpoints
- workspace variants
- fork conversation metadata
- fork API
- UI fork button
- fallback transcript bootstrap
- new sandbox per fork

Do not depend on native ACP fork.

This gives consistent behavior for Claude, Codex, and Cursor.

### Phase 2: Native ACP Fork Optimization

Implement:

- runtime capability exposure from ACP manager
- native fork session creation for Claude live-head forks
- persisted mapping from new conversation key to child ACP session id
- fallback when native fork fails

### Phase 3: Message-Level Native Fork

Only after ACP adapters reliably support message-id fork:

- store ACP message ids per assistant run
- request `session/fork` from a specific message id
- avoid transcript bootstrap when exact native fork is possible

### Phase 4: Merge UX

Implement:

- fork diff view
- apply selected changes
- mark fork as merged/abandoned
- optional PR creation

## Edge Cases

### Fork While Parent Run Is Active

Do not allow.

Reason:

- the assistant message is incomplete
- file writes may still be in progress
- checkpoint is unstable

### Fork From A Failed Run

Allow only if a checkpoint exists and user confirms.

Default should be disabled because failed runs can leave partial files.

### Fork From An Old Message Without Checkpoint

Offer `Fork from current files` with warning, or require user to fork from a
newer checkpoint.

Do not pretend the workspace is restored to the old message.

### Parent Workspace Has Uncommitted Changes

If checkpointing is implemented correctly, this is fine. The checkpoint captures
the dirty state at the selected run.

If no checkpoint exists, warn.

### Native ACP Fork Succeeds But Workspace Restore Fails

Abort the fork and close/delete the child ACP session if possible.

Workspace correctness is more important than native session continuity.

### Workspace Fork Succeeds But Native ACP Fork Fails

Proceed with transcript bootstrap fallback.

## Tests For Future Implementation

### Backend

- Can create checkpoint after completed assistant run.
- Can fork a conversation from a completed run.
- Forked conversation has parent metadata.
- Forked conversation uses a different `thread_id`.
- Forked conversation points at a new workspace variant.
- Parent conversation remains active and unchanged.
- Fork API rejects in-progress assistant run.
- Fork API warns or rejects missing checkpoint for old run.
- ACP session store maps child `thread_id` independently.

### Workspace

- Git checkpoint restores tracked changes.
- Git checkpoint restores untracked files.
- Non-git checkpoint restores files.
- Fork workspace changes do not affect parent workspace.
- Parent workspace changes after fork do not affect fork workspace.

### ACP

- Claude native fork path is used only when capability is advertised.
- Codex falls back to transcript bootstrap.
- Cursor falls back unless capability is advertised.
- Native fork failure falls back to bootstrap when workspace fork succeeded.
- New fork conversation can continue for multiple turns.

### UI

- Fork action appears only on completed assistant messages.
- Fork action is disabled during active generation.
- Forked conversation appears in task view.
- Workspace branch indicator is visible.
- Read-only fork prevents write-capable execution.

## Open Questions

1. Should forked conversations be shown under the same task, or as child tasks?

   Recommendation: same task at first, because the goal remains the same and
   merge/apply is easier to reason about.

2. Should forked workspaces share `.agent-team/` artifacts?

   Recommendation: copy artifacts at fork time, then let each fork diverge.

3. Should autonomous loop runs be forkable?

   Recommendation: yes later, but v1 should focus on direct CLI chat in task
   view.

4. Should fork checkpoints be kept forever?

   Recommendation: no. Add retention policy later:

   - keep checkpoints that have forks
   - keep recent N checkpoints
   - prune old unreferenced checkpoints

## Implementation Warning

Do not implement this as only:

```text
copy transcript -> new conversation
```

That is acceptable for read-only exploration, but not for coding work.

For coding agents, the correct mental model is:

```text
fork = conversation branch + workspace branch + sandbox branch
```

## References

- ACP session fork RFD: <https://agentclientprotocol.com/rfds/session-fork>
- Claude ACP package: <https://github.com/agentclientprotocol/claude-agent-acp>
- Codex ACP package: <https://github.com/agentclientprotocol/codex-acp>
- Cursor CLI fork-session feature request: <https://forum.cursor.com/t/fork-session-or-duplicate-chat-in-cursor-cli/149498>
- Cursor ACP overview: <https://zed.dev/acp/agent/cursor>
