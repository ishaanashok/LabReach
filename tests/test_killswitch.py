from labreach import killswitch as ks


def _sent(conn, n, bounced):
    for i in range(n):
        conn.execute("INSERT INTO targets (id, name, name_norm, university, university_norm) VALUES (?,?,?,?,?)",
                     (i + 1, f"p{i}", f"p{i}", "u", "u"))
        conn.execute("INSERT INTO emails (target_id, kind, sent_at, bounced_at) VALUES (?, 'initial', ?, ?)",
                     (i + 1, f"2026-10-{10 + i % 9:02d}T10:00:{i:02d}", "x" if i < bounced else None))


def test_manual_stop_file(conn, stop_file):
    assert not ks.is_engaged(conn, stop_file)
    stop_file.write_text("x")
    assert ks.is_engaged(conn, stop_file)


def test_bounce_spike_engages(conn, stop_file):
    _sent(conn, 20, bounced=4)  # 20% > 15%
    assert ks.check_bounce_rate(conn, stop_file)
    assert ks.is_engaged(conn, stop_file) and "bounce rate" in ks.reason(conn, stop_file)


def test_bounce_under_threshold_and_short_window_ok(conn, stop_file):
    _sent(conn, 20, bounced=3)  # exactly 15% is not "above"
    assert not ks.check_bounce_rate(conn, stop_file)
    assert not ks.is_engaged(conn, stop_file)


def test_claim_failure_streak_same_source_type(conn, stop_file):
    for _ in range(2):
        ks.record_claim_check(conn, 1, "paper", False)
    assert not ks.check_claim_failure_streak(conn, stop_file)
    ks.record_claim_check(conn, 1, "paper", False)
    assert ks.check_claim_failure_streak(conn, stop_file)


def test_claim_failures_mixed_types_or_with_pass_do_not_engage(conn, stop_file):
    ks.record_claim_check(conn, 1, "paper", False)
    ks.record_claim_check(conn, 1, "lab_news", False)
    ks.record_claim_check(conn, 1, "paper", False)
    assert not ks.check_claim_failure_streak(conn, stop_file)


def test_login_failure_complaint_and_smtp(conn, stop_file):
    ks.on_login_failure(conn, stop_file, "gmail imap")
    assert "login failure" in ks.reason(conn, stop_file)
    ks.resume(conn, stop_file)
    assert not ks.is_engaged(conn, stop_file)
    ks.on_unsafe_reply(conn, stop_file, 1, "please remove me")
    assert ks.is_engaged(conn, stop_file)
    ks.resume(conn, stop_file)
    assert ks.on_smtp_error(conn, stop_file, 550, "5.4.5 Daily user sending limit exceeded")
    ks.resume(conn, stop_file)
    assert not ks.on_smtp_error(conn, stop_file, 550, "5.1.1 mailbox unavailable")
