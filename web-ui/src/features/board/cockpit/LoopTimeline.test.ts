import { describe, expect, it } from "vitest";
import type { LoopInfoDTO } from "@/api/types";
import { buildRoleSources } from "./roleSources";

describe("buildRoleSources", () => {
  it("keeps every fresh plan-review conversation in work history", () => {
    const info = {
      task_id: "task-1",
      execution_mode: "autonomous",
      is_running: false,
      planner_conversation_id: "planner-conv",
      reviewer_runs: [
        {
          run_id: "review-1",
          conversation_id: "review-conv-1",
          agent_id: "claude",
        },
        {
          run_id: "review-2",
          conversation_id: "review-conv-2",
          agent_id: "codex",
        },
      ],
      generator_conversation_id: "builder-conv",
      attempts: [],
    } satisfies LoopInfoDTO;

    expect(buildRoleSources(info).map((source) => source.key)).toEqual([
      "plan",
      "review-review-1",
      "review-review-2",
      "build",
    ]);
    expect(buildRoleSources(info).map((source) => source.label)).toEqual([
      "Planner",
      "Plan reviewer #1",
      "Plan reviewer #2",
      "Builder",
    ]);
    expect(
      buildRoleSources(info)
        .filter((source) => source.kind === "review")
        .map((source) => source.agentId),
    ).toEqual(["claude", "codex"]);
  });
});
