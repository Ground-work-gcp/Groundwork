"""The workflow graph's scheduling algorithms and the memory graph's ranking."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.domain.flow import POSITIONS, critical_path, next_action, priority, shortest_path
from app.services.memory import decay, personalized_pagerank, topics


def nodes(**remaining):
    return {k: {"remaining": remaining.get(k, 0), "label": k, "href": f"#{k}"} for k in POSITIONS}


def test_critical_path_follows_the_heaviest_remaining_work():
    graph = nodes(extraction=5, reconciliation=9, findings=1)
    path = critical_path(graph)
    assert path[-1] == "resolution"
    assert "reconciliation" in path and "ownership" not in path  # finance branch outweighs evidence branch
    # The first unfinished node on the critical path is upstream work that gates the rest.
    assert next_action(graph, path)["node"] == "extraction"
    assert next_action(nodes(reconciliation=9), critical_path(nodes(reconciliation=9)))["node"] == "reconciliation"


def test_critical_path_switches_branch_when_the_weights_change():
    path = critical_path(nodes(extraction=5, ownership=7, reconciliation=1))
    assert "ownership" in path and "reconciliation" not in path
    assert next_action(nodes(), critical_path(nodes())) is None  # nothing left anywhere


def test_dijkstra_takes_the_alternative_route_when_it_is_cheaper():
    # Heavy findings + review backlog: settling via an approved "mark paid" action is cheaper.
    cost, path = shortest_path(nodes(findings=20, needs_you=20))
    assert path == ["sources", "extraction", "obligations", "approvals", "resolution"]
    assert cost == 4  # four hops, no remaining work on them
    _, direct = shortest_path(nodes(obligations=50))
    assert "obligations" not in direct


def test_priority_orders_by_severity_money_and_age():
    now = datetime.now(tz=UTC)
    assert priority("critical", 0, now) > priority("high", 0, now) > priority("medium", 0, now)
    assert priority("medium", 500_000, now) > priority("medium", 0, now)
    assert priority("medium", 0, now - timedelta(days=20)) > priority("medium", 0, now)
    assert priority("medium", 0, now - timedelta(days=300)) == priority("medium", 0, now - timedelta(days=30))  # capped


def test_personalized_pagerank_ranks_recent_and_repeated_work_highest():
    edges = {
        "user:u": {"case:a": 3.0, "case:b": 0.2, "doc:x": 1.0},
        "doc:x": {"case:a": 0.5},
        "case:c": {"case:d": 1.0},  # unreachable from the user
    }
    rank = personalized_pagerank(edges, "user:u")
    assert rank["case:a"] > rank["doc:x"] > rank["case:b"]
    assert rank.get("case:c", 0) == 0 and rank.get("case:d", 0) == 0
    assert abs(sum(rank.values()) - 1.0) < 1e-6
    assert personalized_pagerank({}, "user:u") == {}


def test_decay_halves_every_fourteen_days_and_topics_drop_stopwords():
    now = datetime.now(tz=UTC)
    assert abs(decay(now - timedelta(days=14), now) - 0.5) < 1e-6
    assert abs(decay((now - timedelta(days=28)).isoformat(), now) - 0.25) < 1e-6
    assert topics("Who owns the property after the 2016 quitclaim deed?") == ["owns", "after", "quitclaim", "deed"]
