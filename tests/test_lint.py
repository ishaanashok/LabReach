import pytest

from labreach.compose import render as R
from labreach.compose.lint import LintContext, count_words, lint_email, ngram_overlap
from labreach.personalize.samples import SAMPLES


@pytest.fixture
def good(templates, profile, settings, today):
    t, s = templates["initial_a"], SAMPLES["initial_a"]
    r = R.render_initial(t, s["slots"], s["recipient"], profile, settings, today, enforce_lock=False)
    ctx = LintContext(recipient_last="Rivera", subject_terms=["low-cost prosthetic limbs"],
                      personalization=r.personalization, banned_claims=profile.banned_claims)
    return r, ctx


def rules(report):
    return report.rules()


def test_good_email_passes(good):
    r, ctx = good
    report = lint_email(r.subject, r.body, ctx)
    assert report.ok, report.issues
    assert 120 <= report.word_count <= 180


def test_word_count_bounds(good):
    r, ctx = good
    assert "word_count" in rules(lint_email(r.subject, "Dear Professor Rivera,\n\nWould you be open to a call?", ctx))
    long = r.body + " " + "extra " * 40
    assert rules(lint_email(r.subject, long, ctx)) >= {"hard_max_words"}


def test_subject_rules(good):
    r, ctx = good
    assert "subject_generic" in rules(lint_email("Research opportunity", r.body, ctx))
    assert "subject_prefix" in rules(lint_email("Re: your low-cost prosthetic limbs paper", r.body, ctx))
    assert "subject_prefix" in rules(lint_email("Fwd: low-cost prosthetic limbs", r.body, ctx))
    assert "subject_specificity" in rules(lint_email("A question for you", r.body, ctx))


@pytest.mark.parametrize("bad,rule", [
    ("I know you're busy, but", "banned_phrase"),
    ("Your work is groundbreaking.", "exaggeration"),
    ("I am an expert in CAD.", "exaggeration"),
    ("I published a paper.", "unsupported_claim_word"),
    ("I am very interested in your research.", "generic_opener"),
    ("Great work 😀", "emoji"),
    ("Wow! Amazing! Really!", "exclamations"),
    ("Could you also review my resume?", "multiple_asks"),
    ("Professor Chen also works on this.", "other_person"),
    ("See https://example.com for more.", "link_not_allowed"),
])
def test_body_rules(good, bad, rule):
    r, ctx = good
    assert rule in rules(lint_email(r.subject, r.body + "\n\n" + bad, ctx)), bad


def test_other_target_name_detected(good):
    r, ctx = good
    ctx.other_names = ["Okafor"]
    assert "other_person" in rules(lint_email(r.subject, r.body + " Sam Okafor suggested this.", ctx))


def test_links_allowed_only_on_allowlist_in_followups():
    ctx = LintContext(kind="fu1", recipient_last="X", allow_links=True, link_allowlist=["github.com/ishaanashok"])
    ok = "Dear Professor X,\n\nI wanted to follow up. Demo: https://github.com/ishaanashok/LUNA-TurbiditySensor here."
    assert "link_off_allowlist" not in rules(lint_email(None, ok, ctx))
    bad = ok.replace("github.com/ishaanashok/LUNA-TurbiditySensor", "evil.example.com/x")
    assert "link_off_allowlist" in rules(lint_email(None, bad, ctx))


def test_attachment_limits(good):
    r, ctx = good
    ctx.attachments = [("a.pdf", 1), ("b.pdf", 1), ("c.pdf", 1)]
    assert "attachments_count" in rules(lint_email(r.subject, r.body, ctx))
    ctx.attachments = [("a.pdf", 2_500_000)]
    assert "attachments_size" in rules(lint_email(r.subject, r.body, ctx))
    ctx.kind, ctx.attachments = "fu1", [("a.pdf", 10)]
    assert "attachments_followup" in rules(lint_email(None, "Dear Professor Rivera,\n\nFollowing up. Thanks.", ctx))


def test_ngram_overlap_on_personalization_only(good):
    r, ctx = good
    ctx.recent_personalization = [r.personalization]  # identical text sent recently
    assert "ngram_overlap" in rules(lint_email(r.subject, r.body, ctx))
    ctx.recent_personalization = ["Completely different sentence about unrelated soft robotics grippers and tendons."]
    assert "ngram_overlap" not in rules(lint_email(r.subject, r.body, ctx))
    assert ngram_overlap("a b c d e f", "a b c d x y") == pytest.approx(1 / 3)


def test_followup_sentence_counts():
    ctx = LintContext(kind="fu1", recipient_last="X")
    assert "followup_length" in rules(lint_email(None, "Dear Professor X,\n\nJust checking in.", ctx))
    ctx = LintContext(kind="fu2", recipient_last="X")
    assert "followup_length" in rules(lint_email(None, "Dear Professor X,\n\nOne. Two. Three.", ctx))


def test_count_words_ignores_punctuation_tokens():
    assert count_words("Hello — world, $30!") == 3
