import pytest
from freezegun import freeze_time

from labreach.budget import BudgetExhausted, calls_today, check_budget, record_call, remaining


def test_budget_counts_per_day_and_halts(conn):
    with freeze_time("2026-10-13 10:00:00"):
        for _ in range(3):
            record_call(conn, "draft", True)
        assert calls_today(conn) == 3 and remaining(conn, 3) == 0
        with pytest.raises(BudgetExhausted):
            check_budget(conn, 3)
    with freeze_time("2026-10-14 10:00:00"):
        assert calls_today(conn) == 0
        check_budget(conn, 3)
