import copy
import shutil

import pytest

from labreach.compose import render as R
from labreach.compose import templates as tpl
from labreach.personalize.samples import SAMPLES


@pytest.fixture
def locked_dir(tmp_path):
    d = tmp_path / "templates"
    shutil.copytree(tpl.TEMPLATE_DIR, d, ignore=shutil.ignore_patterns("LOCK.json"))
    tpl.lock_templates(d)
    return d


def test_lock_detects_any_edit(locked_dir):
    assert set(tpl.lock_status(locked_dir).values()) == {"locked"}
    p = locked_dir / "initial_a.yaml"
    p.write_text(p.read_text().replace("Thank you for your time", "Thanks a lot"))
    status = tpl.lock_status(locked_dir)
    assert status["initial_a"] == "modified" and status["initial_b"] == "locked"
    with pytest.raises(tpl.TemplateNotLocked):
        tpl.verify_lock(tpl.load_template(p), locked_dir)


def test_unlocked_templates_cannot_render_for_sending(tmp_path, profile, settings, today):
    t = tpl.load_templates()["initial_a"]
    s = SAMPLES["initial_a"]
    # repo templates are not locked until the parent approves; verify_lock uses the repo dir
    if tpl.read_lock() is None:
        with pytest.raises(tpl.TemplateNotLocked):
            R.render_initial(t, s["slots"], s["recipient"], profile, settings, today)


def test_render_and_conformance(templates, profile, settings, today):
    for tid in ("initial_a", "initial_b", "initial_c"):
        t, s = templates[tid], SAMPLES[tid]
        r = R.render_initial(t, s["slots"], s["recipient"], profile, settings, today, enforce_lock=False)
        ok, problems = R.conforms(t, r.body, profile)
        assert ok, problems


def test_conformance_rejects_rewritten_skeleton(templates, profile, settings, today):
    t, s = templates["initial_a"], SAMPLES["initial_a"]
    body = R.render_initial(t, s["slots"], s["recipient"], profile, settings, today, enforce_lock=False).body
    ok, _ = R.conforms(t, body.replace("My resume is attached.", "I have attached my resume."), profile)
    assert not ok
    ok, problems = R.conforms(t, body.replace("a 501(c)(3) building", "a 501(c)(3) firm building"), profile)
    assert not ok and any("credentials" in p for p in problems)
    ctx = (s["recipient"], settings, today)
    assert R.conforms(t, body, profile, ctx)[0]
    ok, problems = R.conforms(t, body.replace("about 10 hours", "about 40 hours"), profile, ctx)
    assert not ok and any("recomputed" in p for p in problems)
    ok, problems = R.conforms(t, body.replace("Dear Professor Rivera", "Dear Professor Chen"), profile, ctx)
    assert not ok


def test_render_rejects_fact_outside_pool_and_unconfirmed(templates, profile, settings, today):
    t, s = templates["initial_a"], SAMPLES["initial_a"]
    bad = copy.deepcopy(s["slots"])
    bad.credential_fact_ids = ["F_RESTEP", "F_EPA"]  # EPA belongs to variant C
    with pytest.raises(R.RenderError):
        R.render_initial(t, bad, s["recipient"], profile, settings, today, enforce_lock=False)
    bad.credential_fact_ids = ["F_RESTEP", "F_BIOWRAP"]
    with pytest.raises(R.RenderError):
        R.render_initial(t, bad, s["recipient"], profile, settings, today, enforce_lock=False)
    bad.credential_fact_ids, bad.task = ["F_RESTEP", "F_FTC_CAD"], "write the whole paper"
    with pytest.raises(R.RenderError):
        R.render_initial(t, bad, s["recipient"], profile, settings, today, enforce_lock=False)


def test_age_switches_on_birthday(templates, profile, settings):
    from datetime import date
    t, s = templates["initial_a"], SAMPLES["initial_a"]
    before = R.render_initial(t, s["slots"], s["recipient"], profile, settings, date(2027, 6, 30), enforce_lock=False)
    after = R.render_initial(t, s["slots"], s["recipient"], profile, settings, date(2027, 7, 1), enforce_lock=False)
    assert "I'm 16 and" in before.body and "I'm 17 and" in after.body


def test_lab_manager_needs_human(templates, profile, settings, today):
    t, s = templates["initial_a"], SAMPLES["initial_a"]
    r = R.Recipient(first="L", last="M", role="lab_manager", university="X")
    with pytest.raises(R.RenderError):
        R.render_initial(t, s["slots"], r, profile, settings, today, enforce_lock=False)


def test_profile_has_no_tamil_nadu_and_no_private_details(profile):
    blob = " ".join(f.email_phrase + (f.followup_phrase or "") for f in profile.facts).lower()
    assert "tamil" not in blob
    for private in ("citizen", "510", "3.9", "birth"):
        assert private not in blob


def test_initial_templates_contain_no_links(templates):
    import re
    for tid in ("initial_a", "initial_b", "initial_c"):
        assert not re.search(r"https?://|www\.|github", templates[tid]["skeleton"], re.I)
