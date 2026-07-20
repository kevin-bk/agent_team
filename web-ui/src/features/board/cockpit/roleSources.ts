import type { LoopInfoDTO } from "@/api/types";

export type RoleKind = "plan" | "review" | "build" | "critic";

export interface RoleMeta {
  /** Stable unique key (e.g. "plan", "review-<runId>", "critic-<attemptId>"). */
  key: string;
  /** Human label shown on each turn in Work history. */
  label: string;
  kind: RoleKind;
  conversationId: string;
  /** Per-run alias, used when a role can change agents between fresh turns. */
  agentId?: string;
}

/** The plan / reviews / build / critic conversations that make up a goal. */
export function buildRoleSources(info: LoopInfoDTO): RoleMeta[] {
  const out: RoleMeta[] = [];
  if (info.planner_conversation_id) {
    out.push({
      key: "plan",
      label: "Planner",
      kind: "plan",
      conversationId: info.planner_conversation_id,
    });
  }
  for (const [index, run] of (info.reviewer_runs ?? []).entries()) {
    out.push({
      key: `review-${run.run_id}`,
      label: `Plan reviewer #${index + 1}`,
      kind: "review",
      conversationId: run.conversation_id,
      agentId: run.agent_id,
    });
  }
  if (info.generator_conversation_id) {
    out.push({
      key: "build",
      label: "Builder",
      kind: "build",
      conversationId: info.generator_conversation_id,
    });
  }
  for (const attempt of info.attempts) {
    const conversationId =
      attempt.critic_conversation_id ??
      attempt.evaluations.find((evaluation) => evaluation.conversation_id)
        ?.conversation_id;
    if (conversationId) {
      out.push({
        key: `critic-${attempt.id}`,
        label: `Critic #${attempt.attempt_no}`,
        kind: "critic",
        conversationId,
      });
    }
  }
  return out;
}
