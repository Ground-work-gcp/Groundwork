"""The review queue's command bus (ported from ``queue-commands.ts``).

Every mutation is a typed command that hands back the command undoing it, so undo, the
audit trail and an agent accepting a batch on someone's behalf are one mechanism, not
three. Groundwork persists these decisions (Magpie kept them in memory).
"""

from __future__ import annotations

from typing import Any

QueueState = dict[str, str]  # entry id -> "accepted" | "rejected"; absent means open


def apply_queue_command(state: QueueState, command: dict[str, Any]) -> dict[str, Any]:
    kind = command.get("type")
    scope = str(command.get("scope", ""))
    if kind == "Resolve":
        to = command["to"]
        if to not in ("accepted", "rejected"):
            raise ValueError("Resolve.to must be 'accepted' or 'rejected'")
        ids: list[str] = list(command["ids"])
        nxt = dict(state)
        # The inverse captures the *prior* status of each id, not a blanket "open".
        previous: QueueState = {}
        for entry_id in ids:
            previous[entry_id] = state.get(entry_id, "open")
            nxt[entry_id] = to
        verb = "Accepted" if to == "accepted" else "Rejected"
        label = f"{verb} one match in {scope}" if len(ids) == 1 else f"{verb} {len(ids)} matches in {scope}"
        return {"state": nxt, "inverse": {"type": "Restore", "states": previous, "scope": scope}, "label": label}
    if kind == "Restore":
        states: QueueState = dict(command["states"])
        nxt = dict(state)
        previous = {}
        for entry_id, status in states.items():
            previous[entry_id] = state.get(entry_id, "open")
            if status == "open":
                nxt.pop(entry_id, None)
            else:
                nxt[entry_id] = status
        return {
            "state": nxt,
            "inverse": {"type": "Restore", "states": previous, "scope": scope},
            "label": f"Restored {len(states)} entry(ies) in {scope}",
        }
    raise ValueError(f"unknown queue command: {kind!r}")
