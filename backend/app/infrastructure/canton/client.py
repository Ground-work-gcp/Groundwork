"""JSON Ledger API v2 client for one participant (shapes verified against Canton 3.4's /docs/openapi).

Only the calls Groundwork needs: submit-and-wait-for-transaction (returns once the transaction is
committed), active contracts at the ledger end, the update stream between two offsets, and the ledger
end itself. Every call is bounded by a timeout; transient failures are retried with backoff by the
caller (the outbox), not here, so a retry is always a deduplicated resubmission of the same command id.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .auth import TokenProvider
from .errors import CantonError, classify

TX_SHAPE = "TRANSACTION_SHAPE_ACS_DELTA"


@dataclass
class LedgerClient:
    role: str            # legal_aid | registry
    base_url: str
    party: str
    user_id: str
    tokens: TokenProvider
    timeout: float = 30.0
    verify: bool | str = True
    transport: httpx.BaseTransport | None = None  # tests inject a transport; production uses the network

    def _client(self) -> httpx.Client:
        return httpx.Client(base_url=self.base_url.rstrip("/"), timeout=self.timeout, verify=self.verify, transport=self.transport)

    def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        if not self.base_url or not self.party:
            raise CantonError("not_configured", f"the {self.role} participant is not configured")
        headers = {"Authorization": f"Bearer {self.tokens.token()}"}
        try:
            with self._client() as c:
                r = c.request(method, path, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise CantonError("timeout", f"the {self.role} participant did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise CantonError("unavailable", f"the {self.role} participant could not be reached") from exc
        data = None
        if r.content:
            try:
                data = r.json()
            except ValueError:
                data = None
        if r.status_code >= 400:
            raise classify(r.status_code, data if isinstance(data, dict) else None)
        return data

    # ── reads ──
    def ledger_end(self) -> int:
        return int(self._call("GET", "/v2/state/ledger-end")["offset"])

    def version(self) -> dict[str, Any]:
        return self._call("GET", "/v2/version")

    def connected_synchronizers(self) -> list[str]:
        out = self._call("GET", f"/v2/state/connected-synchronizers?party={self.party}") or {}
        return [s.get("synchronizerId", "") for s in out.get("connectedSynchronizers", [])]

    def _event_format(self, templates: list[str]) -> dict[str, Any]:
        cumulative = [{"identifierFilter": {"TemplateFilter": {"value": {"templateId": t, "includeCreatedEventBlob": False}}}} for t in templates]
        return {"filtersByParty": {self.party: {"cumulative": cumulative}}, "verbose": False}

    def active_contracts(self, templates: list[str]) -> list[dict[str, Any]]:
        end = self.ledger_end()
        rows = self._call("POST", "/v2/state/active-contracts", {"eventFormat": self._event_format(templates), "verbose": False, "activeAtOffset": end}) or []
        out = []
        for row in rows:
            entry = row.get("contractEntry", {}).get("JsActiveContract")
            if entry:
                out.append(entry["createdEvent"])
        return out

    def updates(self, begin_exclusive: int, end_inclusive: int, templates: list[str]) -> list[dict[str, Any]]:
        """Committed transactions in (begin, end]; each with its events (created and archived)."""
        if end_inclusive <= begin_exclusive:
            return []
        body = {
            "beginExclusive": begin_exclusive, "endInclusive": end_inclusive, "verbose": False,
            "updateFormat": {"includeTransactions": {"eventFormat": self._event_format(templates), "transactionShape": TX_SHAPE}},
        }
        rows = self._call("POST", "/v2/updates", body) or []
        return [row["update"]["Transaction"]["value"] for row in rows if "Transaction" in row.get("update", {})]

    # ── writes (each returns only after the transaction is committed) ──
    def _submit(self, command: dict[str, Any], command_id: str) -> dict[str, Any]:
        body = {
            "commands": {"commands": [command], "commandId": command_id, "actAs": [self.party], "readAs": [self.party], "userId": self.user_id},
            "transactionFormat": {"eventFormat": {"filtersByParty": {self.party: {}}, "verbose": False}, "transactionShape": TX_SHAPE},
        }
        return self._call("POST", "/v2/commands/submit-and-wait-for-transaction", body)["transaction"]

    def create(self, template: str, arguments: dict[str, Any], command_id: str) -> dict[str, Any]:
        return self._submit({"CreateCommand": {"templateId": template, "createArguments": arguments}}, command_id)

    def exercise(self, template: str, contract_id: str, choice: str, argument: dict[str, Any], command_id: str) -> dict[str, Any]:
        return self._submit({"ExerciseCommand": {"templateId": template, "contractId": contract_id, "choice": choice, "choiceArgument": argument}}, command_id)


def created_events(tx: dict[str, Any]) -> list[dict[str, Any]]:
    return [e["CreatedEvent"] for e in tx.get("events", []) if "CreatedEvent" in e]


def archived_events(tx: dict[str, Any]) -> list[dict[str, Any]]:
    return [e["ArchivedEvent"] for e in tx.get("events", []) if "ArchivedEvent" in e]


def entity(template_id: str) -> str:
    return template_id.rsplit(":", 1)[-1]
