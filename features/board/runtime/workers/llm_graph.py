"""Worker that drives a regular agent through its LangGraph graph.

Builds the agent's full capability set, streams it token-by-token, translates
each chunk into ``AgentEvent`` frames and emits them. Token usage is accumulated
on ``ctx.usage`` in place. The checkpointer context is owned here so it is always
released when the graph finishes, even on cancellation.
"""

from __future__ import annotations

import asyncio
import time

from agent_team.features.board.runtime import event_store
from agent_team.features.board.runtime.graph_builder import (
    build_graph,
    make_checkpointer,
)
from agent_team.features.board.runtime.translator import (
    StreamTranslator,
    extract_usage,
)
from agent_team.features.board.runtime.workers.base import (
    EmitFn,
    TurnContext,
    TurnResult,
)

#: How often to poll the DB for a cross-process cancel while streaming.
_CANCEL_POLL_SECONDS = 2.0


class LlmGraphWorker:
    """Runs an agent alias through its graph for one turn."""

    async def run_turn(
        self, ctx: TurnContext, emit: EmitFn, cancel: asyncio.Event
    ) -> TurnResult:
        cp_ctx = None
        try:
            checkpointer, cp_ctx = await asyncio.to_thread(
                make_checkpointer, ctx.agent_alias
            )
            agent = await build_graph(
                ctx.agent_alias, checkpointer, workspace_path=ctx.workspace_path
            )
            translator = StreamTranslator()
            stream = agent.astream(
                {"messages": [{"role": "user", "content": ctx.prompt}]},
                {"configurable": {"thread_id": ctx.thread_id}},
                subgraphs=True,
                # ``messages`` streams the model output token-by-token (text +
                # thinking); ``updates`` carries the structured tool frames and
                # the final-answer snapshot; ``custom`` carries AI-coding
                # sub-agent live progress.
                stream_mode=["messages", "updates", "custom"],
            )
            last_cancel_poll = 0.0
            try:
                async for raw_chunk in stream:
                    if cancel.is_set():
                        break
                    now = time.monotonic()
                    if now - last_cancel_poll >= _CANCEL_POLL_SECONDS:
                        last_cancel_poll = now
                        if await asyncio.to_thread(
                            event_store.is_cancel_requested, ctx.run_id
                        ):
                            cancel.set()
                            break
                    for event_type, data in translator.translate(raw_chunk):
                        await emit(event_type, data)
                    chunk_usage = extract_usage(raw_chunk)
                    ctx.usage["input_tokens"] += chunk_usage["input_tokens"]
                    ctx.usage["output_tokens"] += chunk_usage["output_tokens"]
                    ctx.usage["cache_read_tokens"] += chunk_usage["cache_read_tokens"]
            finally:
                try:
                    await stream.aclose()
                except Exception:
                    pass

            ctx.usage["total_tokens"] = (
                ctx.usage["input_tokens"] + ctx.usage["output_tokens"]
            )
            return TurnResult(
                final_text=translator.final_text,
                cancelled=cancel.is_set(),
                usage=ctx.usage,
            )
        finally:
            if cp_ctx is not None:
                try:
                    cp_ctx.__exit__(None, None, None)
                except Exception:
                    pass
