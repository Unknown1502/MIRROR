"""Intent compiler, entity resolution, evidence and candidate generation."""

from app.intent.compiler import CommandKind, compile_utterance, has_commit_language
from app.intent.entities import EntityResolver
from app.intent.evidence import EvidenceStatus, Utterance, check_model_value
from app.intent.numbers import words_to_int


def resolver(container):
    return EntityResolver(container.erp.read_snapshot())


def test_demo_command_forks_pune(container):
    cmd = compile_utterance("Move 700 units of XR-17 from Mumbai to the Pune site.", resolver(container))
    assert cmd.kind is CommandKind.PROPOSE
    assert cmd.quantity.value == 700
    assert cmd.product.candidates == ("XR-17",)
    assert cmd.source.candidates == ("MUMBAI-DC",)
    assert cmd.destination.candidates == ("PUNE-SITE-1", "PUNE-SITE-2")


def test_product_codes_are_not_quantities(container):
    cmd = compile_utterance("move 300 AX-77148 from Delhi to Bengaluru", resolver(container))
    assert [q.value for q in cmd.quantities] == [300]
    assert cmd.product.candidates == ("AX-77148",)


def test_site_numbers_are_not_quantities(container):
    cmd = compile_utterance("Site 2.", resolver(container))
    assert cmd.quantities == []
    assert cmd.bare_locations[0].candidates == ("PUNE-SITE-2",)


def test_spoken_numbers(container):
    assert words_to_int("seven hundred") == 700
    assert words_to_int("twelve hundred") == 1200
    assert words_to_int("one thousand two hundred and fifty") == 1250
    cmd = compile_utterance("what if I move nine hundred", resolver(container))
    assert cmd.kind is CommandKind.WHAT_IF and cmd.quantity.value == 900


def test_xr17b_vs_xr17_and_family(container):
    r = resolver(container)
    assert compile_utterance("move 100 XR-17B to Delhi", r).product.candidates == ("XR-17B",)
    assert compile_utterance("move 100 XR 17 to Delhi", r).product.candidates == ("XR-17",)
    assert compile_utterance("move 100 XR units to Delhi", r).product.candidates == ("XR-17", "XR-17B")


def test_unknown_location_resolves_to_nothing(container):
    cmd = compile_utterance("move 100 XR-17 from Chennai to Pune site 2", resolver(container))
    assert cmd.source is None  # never invented
    assert cmd.destination.candidates == ("PUNE-SITE-2",)


def test_classification(container):
    r = resolver(container)
    cases = {
        "Compare 500 and 900.": CommandKind.COMPARE,
        "Move as much as possible without breaking safety stock.": CommandKind.OPTIMIZE,
        "What about 500?": CommandKind.WHAT_IF,
        "Commit 500.": CommandKind.COMMIT,
        "Wait - make that 500.": CommandKind.AMEND,
        "Actually I meant Mumbai.": CommandKind.AMEND,
        "Rollback.": CommandKind.ROLLBACK,
        "cancel": CommandKind.ABORT,
        "how much XR-17 is in Mumbai": CommandKind.QUERY,
    }
    for text, kind in cases.items():
        assert compile_utterance(text, r).kind is kind, text


def test_commit_language_requires_explicit_unnegated_instruction():
    assert has_commit_language("Commit 500.")
    assert has_commit_language("go ahead")
    assert not has_commit_language("don't commit yet")
    assert not has_commit_language("should I commit?")
    assert not has_commit_language("what if I commit 900")
    assert not has_commit_language("wait")


def test_model_value_cannot_resolve_ambiguity(container):
    """The LLM picked Pune Site 1, but the user only said 'Pune': the field stays ambiguous."""
    utts = [Utterance("item_1", "move 700 XR-17 from Mumbai to the Pune site", "VOICE")]
    ev = check_model_value("destination", "Pune Site 1", utts, resolver(container))
    assert ev.candidates == ("PUNE-SITE-1", "PUNE-SITE-2")
    assert ev.status is EvidenceStatus.INFERRED
    assert ev.transcript_item_id == "item_1" and ev.text_span.lower() == "pune"


def test_model_invented_quantity_is_inferred(container):
    utts = [Utterance("item_1", "move XR-17 from Mumbai to Pune site 2", "VOICE")]
    ev = check_model_value("quantity", 700, utts, resolver(container))
    assert ev.status is EvidenceStatus.INFERRED
    assert ev.source_type.value == "MODEL_PROPOSAL"


def test_model_value_supported_by_speech_is_explicit(container):
    utts = [Utterance("item_2", "Site 2", "VOICE"), Utterance("item_1", "move 700 XR-17 from Mumbai to Pune", "VOICE")]
    r = resolver(container)
    dest = check_model_value("destination", "PUNE-SITE-2", utts, r)
    assert dest.candidates == ("PUNE-SITE-2",) and dest.status is EvidenceStatus.RESOLVED
    qty = check_model_value("quantity", 700, utts, r)
    assert qty.status is EvidenceStatus.EXPLICIT and qty.transcript_item_id == "item_1"


def test_model_unknown_entity(container):
    utts = [Utterance("item_1", "move 700 XR-17 from Chennai", "VOICE")]
    ev = check_model_value("source", "Chennai", utts, resolver(container))
    assert ev.status is EvidenceStatus.UNKNOWN and ev.candidates == ()
