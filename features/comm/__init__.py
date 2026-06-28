"""Communication Gateway: outbound notifications to external chat (Mattermost).

v1 is outbound-only. The data model mirrors the repository feature: an
owner-scoped :class:`AgentTeamCommConnection` (credential, reused across boards)
plus a board↔connection link :class:`AgentTeamBoardChannel` (per-board routing).
"""
