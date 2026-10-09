

def make_target(conn, tid=1, *, name="Alex Rivera", first="Alex", last="Rivera", role="professor", university="Stanford University",
                department="Mechanical Engineering", email="rivera@stanford.edu", status="active", lab_key=None,
                tz="America/Los_Angeles", local=1, variant="initial_a", fit=7, lab="Rivera Lab"):
    conn.execute(
        "INSERT INTO targets (id, name, name_norm, first_name, last_name, role, university, university_norm, department, lab, "
        "lab_key, email, status, timezone, local, campus, variant, fit_score) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (tid, name, name.lower(), first, last, role, university, university.lower(), department, lab,
         lab_key or f"lab{tid}", email, status, tz, local, "Stanford" if local else None, variant, fit))
    conn.commit()
    return tid


def make_sent(conn, tid, kind="initial", message_id="<sent1@gmail.com>", sent_at=None, subject="Question about X",
              body="Dear Professor Rivera,\n\nBody.", root=None):
    conn.execute(
        "INSERT INTO emails (target_id, kind, state, subject, body, message_id, thread_root_message_id, sent_at) "
        "VALUES (?,?,?,?,?,?,?,?)", (tid, kind, "sent", subject, body, message_id, root or message_id,
                                     sent_at or "2026-10-13T15:30:00"))
    conn.commit()
    return conn.execute("SELECT id FROM emails WHERE target_id=? AND kind=?", (tid, kind)).fetchone()[0]
