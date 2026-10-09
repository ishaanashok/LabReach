import copy

import pytest

from labreach.models import Claim
from labreach.personalize.samples import SAMPLES
from labreach.personalize.verify_claims import verify_draft


@pytest.fixture
def case(templates, profile):
    s = SAMPLES["initial_a"]
    return templates["initial_a"], s["slots"], {s["source"].id: s["source"]}


def test_valid_draft_passes(case, profile):
    template, slots, sources = case
    result = verify_draft(slots, sources, profile, template)
    assert result.ok, result.failures


def test_fabricated_paper_title_is_caught(case, profile):
    template, slots, sources = case
    bad = copy.deepcopy(slots)
    bad.p1 = bad.p1.replace("Adaptive Socket Interfaces for Low-Cost Prosthetic Limbs", "Self-Healing Prosthetic Sockets")
    bad.claims[0].text = bad.claims[0].text.replace("Adaptive Socket Interfaces for Low-Cost Prosthetic Limbs",
                                                    "Self-Healing Prosthetic Sockets")
    result = verify_draft(bad, sources, profile, template)
    assert not result.ok
    assert any("quoted text not found verbatim" in f for f in result.failures)


def test_fabricated_year_and_number_are_caught(case, profile):
    template, slots, sources = case
    bad = copy.deepcopy(slots)
    bad.p1 = bad.p1.replace("2025", "2023")
    bad.claims[0].text = bad.claims[0].text.replace("2025", "2023")
    assert any("year 2023" in f for f in verify_draft(bad, sources, profile, template).failures)
    bad = copy.deepcopy(slots)
    bad.p2 = bad.p2.replace("a low-cost", "a $5,000 low-cost")
    bad.claims[1].text = bad.claims[1].text.replace("a low-cost", "a $5,000 low-cost")
    assert any("number" in f for f in verify_draft(bad, sources, profile, template).failures)


def test_unsupported_claim_about_work_is_caught(case, profile):
    template, slots, sources = case
    fake = "on a quantum neural interface that outperforms every commercial prosthesis"
    real = "on a compliant socket that adapts to residual-limb volume change"
    bad = copy.deepcopy(slots)
    bad.p1 = bad.p1.replace(real, fake)
    bad.claims[0].text = bad.claims[0].text.replace(real, fake)
    result = verify_draft(bad, sources, profile, template)
    assert not result.ok and any("not supported by source" in f for f in result.failures)


def test_unknown_named_method_is_caught(case, profile):
    template, slots, sources = case
    bad = copy.deepcopy(slots)
    bad.p2 = "Because our ReStep design is also a low-cost adjustable limb, I wondered whether your Kalman Filter helps."
    bad.claims[2].text = "whether your Kalman Filter helps"
    assert any("Kalman" in f for f in verify_draft(bad, sources, profile, template).failures)


def test_sentence_without_claim_and_student_sentence_without_fact(case, profile):
    template, slots, sources = case
    bad = copy.deepcopy(slots)
    bad.p2 = bad.p2 + " Your lab seems wonderfully organized."
    assert any("no supporting claim" in f for f in verify_draft(bad, sources, profile, template).failures)
    bad = copy.deepcopy(slots)
    bad.claims[1] = Claim(text=bad.claims[1].text, source_id=1)
    assert not verify_draft(bad, sources, profile, template).ok


def test_unknown_source_unconfirmed_fact_low_confidence(case, profile):
    template, slots, sources = case
    bad = copy.deepcopy(slots)
    bad.claims[0] = Claim(text=bad.claims[0].text, source_id=99)
    assert any("unknown source" in f for f in verify_draft(bad, sources, profile, template).failures)
    bad = copy.deepcopy(slots)
    bad.claims[1] = Claim(text=bad.claims[1].text, profile_fact_id="F_BIOWRAP")
    assert any("unconfirmed" in f for f in verify_draft(bad, sources, profile, template).failures)
    bad = copy.deepcopy(slots)
    bad.confidence = 0.4
    assert any("confidence" in f for f in verify_draft(bad, sources, profile, template).failures)


def test_choices_must_come_from_locked_pools(case, profile):
    template, slots, sources = case
    bad = copy.deepcopy(slots)
    bad.task = "write your grant"
    bad.credential_fact_ids = ["F_RESTEP", "F_EPA"]
    failures = verify_draft(bad, sources, profile, template).failures
    assert any("task" in f for f in failures) and any("F_EPA" in f for f in failures)


def test_subject_topic_must_exist_in_sources(case, profile):
    template, slots, sources = case
    bad = copy.deepcopy(slots)
    bad.subject_topic = "quantum gravity"
    assert any("subject topic" in f for f in verify_draft(bad, sources, profile, template).failures)
