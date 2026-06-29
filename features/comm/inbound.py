"""Inbound action handling for the Communication Gateway (v2).

This is the provider-agnostic "brain" that turns a human's chat reply/button
into a real action on a task — without re-implementing any loop logic and
without depending on a live provider connection (so it is fully unit-testable):

1. Resolve the provider user to a **verified** internal user. No verified
   mapping → the action is refused (fall back to "open the web UI").
2. Find the still-open :class:`AgentTeamHumanActionRequest` for the thread and
   check the action is one it permits.
3. Authorize the user on the task's board (``editor``+), request-free.
4. Perform the action by calling :mod:`...loop.human_actions` — the same service
   the cockpit endpoints use — then mark the request resolved.

Transport adapters (Slack/Mattermost webhooks) live elsewhere and only need to
parse a provider payload into ``(connection, provider_user_id, thread, text,
action)`` and call :func:`execute_action`.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from agent_team.features.comm import repositories as comm_repo
from agent_team.features.comm.models import (
    ACTION_ACK_COMPLETE,
    ACTION_ANSWER_QUESTIONS,
    ACTION_APPROVE_PLAN,
    ACTION_NOTE,
    ACTION_OPEN,
    AgentTeamHumanActionRequest,
)


@dataclass
class ActionResult:
    """Outcome of an inbound action, suitable for replying in the thread."""

    ok: bool
    action: str | None = None
    message: str | None = None
    error: str | None = None


def resolve_internal_user(db: Session, *, connection_id: str, provider_user_id: str):
    """Return the internal :class:`User` for a verified provider mapping, else None."""
    from core.database.models import User

    link = comm_repo.get_verified_user_link_by_provider_id(
        db, connection_id=connection_id, mm_user_id=provider_user_id
    )
    if link is None:
        return None
    return db.get(User, link.user_id)


def _authorize(db: Session, task, user) -> str | None:
    """Return an error string if ``user`` lacks ``editor`` on the task's board."""
    from agent_team.features.board import authz
    from agent_team.features.board.repositories import boards as boards_repo
    from agent_team.features.board.repositories import members as members_repo

    board = boards_repo.get_board(db, task.board_id)
    if board is None:
        return "Board not found."
    role = members_repo.access_role(
        db, board, user_id=user.id, is_admin=authz.is_admin(user)
    )
    if not authz.role_at_least(role, "editor"):
        return "You don't have permission to act on this task."
    return None


def execute_action(
    db: Session,
    *,
    action_request: AgentTeamHumanActionRequest,
    user,
    action: str,
    text: str | None = None,
) -> ActionResult:
    """Perform one inbound action against an open action request.

    ``user`` is an already-resolved (verified) internal user; ``action`` must be
    one the request permits. Returns an :class:`ActionResult` (never raises for
    expected validation failures).
    """
    from agent_team.features.board.repositories import tasks as tasks_repo
    from agent_team.features.board.runtime.loop import human_actions

    if action_request.status != ACTION_OPEN:
        return ActionResult(ok=False, action=action, error="This request is no longer open.")
    if action not in comm_repo.action_request_actions(action_request):
        return ActionResult(ok=False, action=action, error="That action isn't allowed here.")

    task = tasks_repo.get_task(db, action_request.task_id)
    if task is None:
        return ActionResult(ok=False, action=action, error="Task not found.")

    auth_err = _authorize(db, task, user)
    if auth_err:
        return ActionResult(ok=False, action=action, error=auth_err)

    try:
        message = _dispatch(db, action=action, task=task, user=user, text=text)
    except human_actions.ActionError as e:
        return ActionResult(ok=False, action=action, error=str(e))

    comm_repo.resolve_action_request(
        db, action_request, user_id=getattr(user, "id", None), action=action
    )
    return ActionResult(ok=True, action=action, message=message)


def _dispatch(db: Session, *, action: str, task, user, text: str | None) -> str:
    """Run one action's effect; returns a short human-facing confirmation."""
    from agent_team.features.board.runtime.loop import human_actions

    if action == ACTION_APPROVE_PLAN:
        human_actions.approve_plan(db, task, user)
        return "Plan approved."

    if action == ACTION_ACK_COMPLETE:
        human_actions.ack_loop(db, task, user)
        return "Acknowledged."

    if action == ACTION_NOTE:
        _append_note(db, task=task, user=user, text=text or "")
        return "Note saved."

    if action == ACTION_ANSWER_QUESTIONS:
        if not (text or "").strip():
            raise human_actions.ActionError("Reply with your answer.")
        answers = _answers_from_freetext(task, text or "")
        resumed = human_actions.answer_questions(db, task, user, answers=answers, note=text)
        return f"Answers recorded — resuming {resumed}."

    raise human_actions.ActionError(f"Unsupported action: {action}")


def _answers_from_freetext(task, text: str) -> dict[str, str]:
    """Map a single free-text reply onto every currently-unanswered question.

    The bot posts all questions in one message and the human answers them in one
    free-text reply; the agent receives the verbatim text against each question
    and sorts out which part answers which. This satisfies the cockpit's
    "all blocking questions answered" gate without requiring structured input.
    """
    from agent_team.features.board.runtime.loop import planning_artifacts as artifacts

    questions = artifacts.read_questions(task.workspace_path)
    return {q["id"]: text for q in questions if not q.get("answer")}


def _append_note(db: Session, *, task, user, text: str) -> None:
    from agent_team.features.board.runtime import task_journal

    task_journal.record_with(
        db,
        task_id=task.id,
        phase="system",
        type="note",
        title="Note from chat",
        body=text,
        actor_id=getattr(user, "id", None),
        actor_type="human",
    )
    db.commit()


def handle_thread_reply(
    db: Session,
    *,
    connection_id: str,
    provider: str,
    provider_user_id: str,
    provider_thread_id: str,
    text: str,
    action: str | None = None,
    provider_message_id: str | None = None,
    channel_id: str | None = None,
    raw: dict | None = None,
) -> ActionResult:
    """End-to-end inbound entry point: store the message, resolve, act, mark.

    ``action`` may be given explicitly (interactive button) or left ``None`` for
    a plain reply, in which case it is inferred from the open request.
    """
    inbound = comm_repo.create_inbound_message(
        db,
        connection_id=connection_id,
        provider=provider,
        channel_id=channel_id,
        provider_user_id=provider_user_id,
        provider_message_id=provider_message_id,
        provider_thread_id=provider_thread_id,
        text=text,
        raw=raw,
    )

    request = comm_repo.get_open_action_request_for_thread(
        db, connection_id=connection_id, provider_thread_id=provider_thread_id
    )
    if request is None:
        comm_repo.mark_inbound(db, inbound, status="ignored", error="No open request for thread")
        return ActionResult(ok=False, error="Nothing is pending in this thread.")

    user = resolve_internal_user(
        db, connection_id=connection_id, provider_user_id=provider_user_id
    )
    if user is None:
        comm_repo.mark_inbound(db, inbound, status="ignored", error="Unverified provider user")
        return ActionResult(
            ok=False,
            error="You're not linked yet — open the web UI to act on this task.",
        )

    chosen = action or _infer_action(request, text)
    if chosen is None:
        comm_repo.mark_inbound(db, inbound, status="ignored", error="Could not infer action")
        return ActionResult(
            ok=False, error="Tell me what to do (e.g. approve, or reply with answers)."
        )

    result = execute_action(db, action_request=request, user=user, action=chosen, text=text)
    comm_repo.mark_inbound(
        db,
        inbound,
        status="processed" if result.ok else "error",
        action_request_id=request.id,
        error=result.error,
    )
    return result


def _infer_action(request: AgentTeamHumanActionRequest, text: str) -> str | None:
    """Pick an action for a plain reply: the only allowed one, else answers."""
    allowed = comm_repo.action_request_actions(request)
    if len(allowed) == 1:
        return allowed[0]
    if (text or "").strip() and ACTION_ANSWER_QUESTIONS in allowed:
        return ACTION_ANSWER_QUESTIONS
    return None
