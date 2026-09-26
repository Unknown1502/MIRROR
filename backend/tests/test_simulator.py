"""Simulator: deterministic, side-effect free, and correct on the signature demo state."""

from app.policy.engine import Outcome, outcome_of
from app.simulation.optimizer import max_safe_quantity
from app.simulation.simulator import TransferIntent, simulate


def codes(sim):
    return sim.codes()


def test_valid_transfer_to_pune_site_2(container):
    snap = container.erp.read_snapshot()
    sim = simulate(snap, TransferIntent("XR-17", 700, "MUMBAI-DC", "PUNE-SITE-2"))
    effects = {e.location_id: (e.before, e.after) for e in sim.effects}
    assert effects == {"MUMBAI-DC": (2000, 1300), "PUNE-SITE-2": (300, 1000)}
    assert outcome_of(sim) is Outcome.VALID


def test_capacity_breach_at_pune_site_1(container):
    snap = container.erp.read_snapshot()
    sim = simulate(snap, TransferIntent("XR-17", 700, "MUMBAI-DC", "PUNE-SITE-1"))
    breach = next(c for c in sim.consequences if c.code == "DESTINATION_CAPACITY_EXCEEDED")
    assert (breach.before, breach.after, breach.threshold, breach.delta) == (1200, 1900, 1500, 400)
    assert outcome_of(sim) is Outcome.BLOCKED


def test_safety_stock_breach_at_900(container):
    snap = container.erp.read_snapshot()
    sim = simulate(snap, TransferIntent("XR-17", 900, "MUMBAI-DC", "PUNE-SITE-2"))
    breach = next(c for c in sim.consequences if c.code == "SOURCE_SAFETY_STOCK_BREACH")
    assert (breach.before, breach.after, breach.threshold, breach.delta) == (2000, 1100, 1200, -100)
    assert outcome_of(sim) is Outcome.BLOCKED


def test_insufficient_inventory(container):
    snap = container.erp.read_snapshot()
    sim = simulate(snap, TransferIntent("AX-77148", 480, "PUNE-SITE-2", "MUMBAI-DC"))
    assert "SOURCE_INSUFFICIENT_INVENTORY" in codes(sim)
    assert outcome_of(sim) is Outcome.BLOCKED


def test_invalid_location_and_product(container):
    snap = container.erp.read_snapshot()
    sim = simulate(snap, TransferIntent("XR-99", 100, "CHENNAI", "PUNE-SITE-2"))
    assert {"UNKNOWN_PRODUCT", "UNKNOWN_LOCATION"} <= codes(sim)
    assert sim.effects == ()
    assert outcome_of(sim) is Outcome.BLOCKED


def test_route_unavailable(container):
    snap = container.erp.read_snapshot()
    sim = simulate(snap, TransferIntent("XR-17", 100, "BENGALURU-HUB", "DELHI-WAREHOUSE"))
    assert "ROUTE_UNAVAILABLE" in codes(sim)


def test_simulation_is_deterministic(container):
    snap = container.erp.read_snapshot()
    a = simulate(snap, TransferIntent("XR-17", 700, "MUMBAI-DC", "PUNE-SITE-1"))
    b = simulate(snap, TransferIntent("XR-17", 700, "MUMBAI-DC", "PUNE-SITE-1"))
    assert a.result_hash == b.result_hash
    assert a.as_dict()["consequences"] == b.as_dict()["consequences"]


def test_simulation_never_writes_reality(container):
    erp = container.erp
    before_rev, before_world = erp.revision(), erp.read_snapshot().on_hand_map()
    snap = erp.read_snapshot()
    for q in range(50, 1001, 50):
        for dst in ("PUNE-SITE-1", "PUNE-SITE-2", "DELHI-WAREHOUSE"):
            simulate(snap, TransferIntent("XR-17", q, "MUMBAI-DC", dst))
    assert erp.revision() == before_rev
    assert erp.read_snapshot().on_hand_map() == before_world
    assert erp.operations() == []


def test_snapshot_is_immutable(container):
    snap = container.erp.read_snapshot()
    import pytest

    with pytest.raises(TypeError):
        snap.cells[("MUMBAI-DC", "XR-17")] = None  # MappingProxyType


def test_optimizer_finds_800(container):
    snap = container.erp.read_snapshot()
    result = max_safe_quantity(snap, "XR-17", "MUMBAI-DC", "PUNE-SITE-2")
    assert result.best_quantity == 800
    assert result.binding_constraint == "SOURCE_SAFETY_STOCK_BREACH"
    probes = {p["quantity"]: p["outcome"] for p in result.probes}
    assert probes[801] == "BLOCKED" and probes[800] != "BLOCKED" and probes[500] == "VALID"


def test_simulation_latency_under_target(container):
    snap = container.erp.read_snapshot()
    sim = simulate(snap, TransferIntent("XR-17", 700, "MUMBAI-DC", "PUNE-SITE-2"))
    assert sim.latency_ms < 100
