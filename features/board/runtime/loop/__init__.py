"""Autonomous loop layer: drive a task to a verified result.

A task in ``autonomous`` execution mode is run by a controller that repeatedly
drives a *generator* worker and an independent *evaluator*, persisting each
attempt and verdict, until the objective is met or a guardrail stops it. Plain
chat tasks never enter this layer.

The pieces are deliberately split so the decision logic carries no I/O and is
unit-testable:

* :mod:`~agent_team.features.board.runtime.loop.verdict` — the verdict value type
  and a parser for an agent-produced verdict.
* :mod:`~agent_team.features.board.runtime.loop.controller` — pure continue/stop
  decisions (``LoopController``).
* :mod:`~agent_team.features.board.runtime.loop.evaluator` — the evaluator
  contract + prompt.
* :mod:`~agent_team.features.board.runtime.loop.driver` — the orchestration that
  performs the I/O (``run_loop``).
"""
