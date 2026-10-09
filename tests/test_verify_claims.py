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
    held = profile.model_copy(deep=True)
    held.fact("F_BIOWRAP").status = "needs_confirmation"
    assert any("unconfirmed" in f for f in verify_draft(bad, sources, held, template).failures)
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


def test_framing_words_do_not_count_against_support(case, profile):
    template, slots, sources = case
    ok = copy.deepcopy(slots)
    ok.p1 = ('I read your recent paper "Adaptive Socket Interfaces for Low-Cost Prosthetic Limbs" which presents a '
             "compliant socket that adapts to residual-limb volume change.")
    ok.claims[0].text = ok.p1.rstrip(".")
    assert verify_draft(ok, sources, profile, template).ok


def test_subject_formula_must_fit_the_recipient_role(case, profile):
    template, slots, sources = case
    grad_formula = copy.deepcopy(slots)
    grad_formula.subject_formula = "S4"
    assert any("S4" in f for f in verify_draft(grad_formula, sources, profile, template, "professor").failures)
    assert not any("subject formula" in f for f in verify_draft(grad_formula, sources, profile, template, "grad_student").failures)
    prof_formula = copy.deepcopy(slots)
    prof_formula.subject_formula = "S3"
    assert not any("subject formula" in f for f in verify_draft(prof_formula, sources, profile, template, "professor").failures)
    assert any("S3" in f for f in verify_draft(prof_formula, sources, profile, template, "postdoc").failures)


def test_personalization_may_not_repeat_the_credentials_paragraph(case, profile):
    template, slots, sources = case
    dup = copy.deepcopy(slots)
    dup.p2 = ("I founded Project ReStep, a 501(c)(3) building low-cost 3D-printed prosthetics, so I wondered how your "
              "socket adapts to volume change.")
    dup.claims[1] = Claim(text="I founded Project ReStep, a 501(c)(3) building low-cost 3D-printed prosthetics",
                          profile_fact_id="F_RESTEP")
    dup.claims[2] = Claim(text="how your socket adapts to volume change", source_id=1)
    assert any("repeat wording" in f for f in verify_draft(dup, sources, profile, template).failures)


def test_question_marks_are_not_allowed_in_personalization(case, profile):
    template, slots, sources = case
    q = copy.deepcopy(slots)
    q.p2 = "Because our ReStep design is also a low-cost adjustable limb, how does your interface handle fit?"
    q.claims[1].text = "our ReStep design is also a low-cost adjustable limb"
    q.claims[2].text = "how does your interface handle fit?"
    assert any("question mark" in f for f in verify_draft(q, sources, profile, template).failures)


def test_light_stemming_tolerates_paraphrase_but_not_fabrication(case, profile):
    template, slots, sources = case
    para = copy.deepcopy(slots)
    para.p1 = ('I read your 2025 paper "Adaptive Socket Interfaces for Low-Cost Prosthetic Limbs" on a socket reducing fitting '
               "iteration in clinics.")
    para.claims[0].text = para.p1.rstrip(".")
    assert verify_draft(para, sources, profile, template).ok
    fab = copy.deepcopy(para)
    fab.p1 = fab.p1.replace("reducing fitting iteration in clinics", "using neural implants that cure paralysis")
    fab.claims[0].text = fab.p1.rstrip(".")
    assert not verify_draft(fab, sources, profile, template).ok
