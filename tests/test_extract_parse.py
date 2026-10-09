from labreach.discovery.email_extract import (
    address_in_snippet,
    deobfuscate,
    domain_allowed,
    extract_emails,
    pick_address_for,
)
from labreach.discovery.parse_directory import parse_directory_heuristic
from labreach.discovery.parse_profile import choose_variant, fit_score, parse_profile

ALLOWED = ["stanford.edu", "berkeley.edu"]


def test_extracts_mailto_plain_and_obfuscated_with_snippets():
    html = """<p>Contact: <a href="mailto:arivera@stanford.edu">Alex</a></p>
    <p>Student: jlee at-sign: jlee@cs.stanford.edu</p>
    <p>Postdoc: pnair [at] ee [dot] stanford [dot] edu</p>
    <p>Other: someone@gmail.com and x@evil.com</p>"""
    hits = {h.address: h for h in extract_emails(html, ALLOWED)}
    assert set(hits) == {"arivera@stanford.edu", "jlee@cs.stanford.edu", "pnair@ee.stanford.edu"}
    assert hits["pnair@ee.stanford.edu"].kind == "obfuscated" and "[at]" in hits["pnair@ee.stanford.edu"].snippet
    assert all(address_in_snippet(h.address, h.snippet) for h in hits.values())


def test_snippet_check_rejects_addresses_not_in_the_text():
    assert not address_in_snippet("invented@stanford.edu", "Contact: arivera@stanford.edu")
    assert address_in_snippet("pnair@ee.stanford.edu", "pnair [at] ee [dot] stanford [dot] edu")
    assert deobfuscate("a (at) b (dot) edu") == "a@b.edu"
    assert domain_allowed("x@cs.stanford.edu", ALLOWED) and not domain_allowed("x@stanford.edu.evil.com", ALLOWED)


def test_never_guesses_a_pattern_when_no_address_is_published():
    html = "<h1>Alex Rivera</h1><p>Professor of Mechanical Engineering. Office: Building 530.</p>"
    hits = extract_emails(html, ALLOWED)
    assert hits == [] and pick_address_for("Rivera", "Alex", hits) is None


def test_pick_address_matches_name_and_refuses_ambiguity():
    html = '<a href="mailto:arivera@stanford.edu">e</a> <a href="mailto:me-admin@stanford.edu">admin</a>'
    hits = extract_emails(html, ALLOWED)
    assert pick_address_for("Rivera", "Alex", hits).address == "arivera@stanford.edu"
    assert pick_address_for("Chen", "Wei", hits) is None                        # nothing matches: not guessed
    two = extract_emails('<a href="mailto:rivera@stanford.edu">a</a><a href="mailto:arivera@stanford.edu">b</a>', ALLOWED)
    assert pick_address_for("Rivera", "Alex", two) is None                      # ambiguous


def directory_html(n=10):
    people = "".join(f'<li><a href="/people/p{i}">First{chr(65 + i)} Lastname{chr(65 + i)}</a><span>Associate Professor of ME</span></li>'
                     for i in range(n))
    extra = ('<li><a href="/people/em">Old Timer</a><span>Professor Emeritus</span></li>'
             '<li><a href="/people/lec">Lee Lecturer</a><span>Lecturer</span></li>'
             '<li><a href="/people/adj">Adam Adjunct</a><span>Adjunct Professor</span></li>'
             '<li><a href="/about">About the Department</a></li><li><a href="/news">Read more</a></li>')
    return f"<ul>{people}{extra}</ul>"


def test_directory_heuristic_takes_active_professors_only():
    people = parse_directory_heuristic(directory_html(), "https://me.stanford.edu/people/faculty")
    names = {p.name for p in people}
    assert len(people) == 10 and "Old Timer" not in names and "Lee Lecturer" not in names and "Adam Adjunct" not in names
    assert people[0].profile_url == "https://me.stanford.edu/people/p0" and "Professor" in people[0].title


def test_profile_exclusions_are_quoted_with_url():
    html = "<p>We do not accept unsolicited inquiries from high school students or summer interns.</p><p>Research: soft robotics.</p>"
    info = parse_profile(html, "https://x.stanford.edu/p")
    assert info.exclusions and "unsolicited" in info.exclusions[0].quote and info.exclusions[0].url == "https://x.stanford.edu/p"
    for text in ("Applicants must be 18 years or older.", "No high school students are accepted.",
                 "We are not accepting unsolicited emails.", "Mentors should not be contacted directly: see the Science Internship Program."):
        assert parse_profile(f"<p>{text} This lab studies controls and robotics for many applications.</p>", "u").exclusions, text


def test_profile_openness_signals_and_members():
    html = """<h2>Research Interests</h2><p>Soft robotics, prosthetic hands, and embedded sensing for rehabilitation.</p>
    <p>We host high school interns each summer and past high school interns have co-authored posters.</p>
    <p>Our outreach program brings pre-college students into the lab every year.</p>
    <h3>Graduate Students</h3><ul><li><a href="/people/jlee">Jordan Lee</a></li><li>Mina Park</li><li>Sam Cho</li><li>Ana Ruiz</li></ul>
    <h3>Postdocs</h3><ul><li><a href="/people/pnair">Priya Nair</a></li></ul>"""
    info = parse_profile(html, "https://x.stanford.edu/lab")
    assert not info.exclusions and info.hs_positive and info.outreach and info.prior_interns
    assert "Soft robotics" in info.research_areas
    groups = {n: g for n, g, _ in info.members}
    assert groups["Jordan Lee"] == "grad_student" and groups["Priya Nair"] == "postdoc" and len(groups) == 5
    score, parts = fit_score(info, has_recent_paper=True, local=True)
    assert parts == {"prior_hs_interns": 3, "outreach_or_precollege": 2, "recent_paper": 2, "bay_area": 1,
                     "computational_task": 1, "large_lab": 1} and score == 10


def test_fit_score_is_conservative_when_page_is_silent():
    info = parse_profile("<p>Our lab studies bridge fatigue in concrete beams and long structures.</p>", "u")
    score, parts = fit_score(info, has_recent_paper=False, local=False)
    assert score == 0 and parts["prior_hs_interns"] == 0


def test_choose_variant():
    assert choose_variant("soft robotics, prosthetic hands, actuators") == "initial_a"
    assert choose_variant("embedded sensors and wireless circuits") == "initial_b"
    assert choose_variant("machine learning and computer vision algorithms") == "initial_c"
    assert choose_variant("medieval poetry") is None
