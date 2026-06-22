"""Board Wiki — an LLM-maintained knowledge base per board.

See ``docs/plans/board-wiki.md``. A board designates one (or more) of its
assigned git repos as its **wiki**. Agents get the usual per-task working copy of
that repo (on their task branch) and the bundled ``board-wiki`` skill pack, which
teaches them to read the wiki before working and to contribute pages as ordinary
commits — reviewed and merged like any other change. This module only ships and
materialises that skill pack; the wiki content lives in the repo itself.
"""
