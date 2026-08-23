"""
Python analysis layer: turns raw rows from Postgres into the same
shaped stats the frontend's data.js used to hardcode (patients, revenue,
repeat-visit %, no-show rate, and a practice health score).

Kept dependency-free (no pandas) so the backend stays light to deploy;
swap in pandas/numpy here later if the calculations grow more complex.
"""

from datetime import datetime, timedelta, timezone, date
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.orm import DailyLog


def compute_data_stage(clinic_created_at: datetime) -> dict:
    """
    Maps how long a clinic has been collecting data onto the rollout
    timeline: Day 1 -> collecting, Week 1 -> baseline, Week 2 -> early
    patterns, Week 3 -> AI identifies opportunities, Week 4+ -> test an
    action / measure results. Used to gate what the AI Advisor is
    allowed to claim - it shouldn't invent "opportunities" from a
    single day of data.
    """
    now = datetime.now(timezone.utc)
    created = clinic_created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    age_days = (now - created).days

    if age_days < 1:
        return {
            "stage": "collecting",
            "label": "Day 1 - collecting data",
            "advisor_mode": "Acknowledge there isn't enough data yet. Encourage the doctor to keep logging visits; do not invent trends or recommendations.",
        }
    if age_days < 7:
        return {
            "stage": "baseline",
            "label": "Week 1 - establishing baseline",
            "advisor_mode": "Describe the current numbers as an early baseline only. Avoid strong claims about trends; there isn't enough history yet to compare against.",
        }
    if age_days < 14:
        return {
            "stage": "patterns",
            "label": "Week 2 - early patterns emerging",
            "advisor_mode": "Point out early week-over-week patterns cautiously, noting they are still emerging and worth watching rather than acting on yet.",
        }
    if age_days < 21:
        return {
            "stage": "opportunities",
            "label": "Week 3 - identifying opportunities",
            "advisor_mode": "Identify specific, concrete opportunities for improvement based on the data, with clear reasoning tied to the numbers.",
        }
    if age_days < 28:
        return {
            "stage": "testing",
            "label": "Week 4 - test an action",
            "advisor_mode": "Recommend one specific, testable action the doctor can try this week, with an expected impact.",
        }
    return {
        "stage": "measuring",
        "label": "Ongoing - measuring results",
        "advisor_mode": "Compare current metrics against the period before any recommended actions were started, and state plainly what worked or didn't.",
    }


def _period_bounds(days: int = 17, start_date: Optional[date] = None, end_date: Optional[date] = None):
    if start_date and end_date:
        start = start_date
        end = end_date
        period_len = max((end - start).days, 1)
        prev_start = start - timedelta(days=period_len)
        return prev_start, start, end

    end = date.today()
    start = end - timedelta(days=days)
    prev_start = start - timedelta(days=days)
    return prev_start, start, end


def compute_stats(
    db: Session,
    clinic_id: int,
    days: int = 17,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> dict:
    """
    Computed entirely from daily_logs - the end-of-day form totals -
    rather than individual visit records. Simpler, and matches how
    data actually enters the system in the MVP.

    Pass start_date/end_date (e.g. from the dashboard's date-range
    filter) to override the rolling "last N days" window with an exact
    custom range. The "previous period" comparison window is then the
    same length, immediately before start_date.
    """
    prev_start, start, end = _period_bounds(days, start_date, end_date)

    def logs_in(period_start, period_end, inclusive_end=False):
        query = db.query(DailyLog).filter(
            DailyLog.clinic_id == clinic_id,
            DailyLog.log_date >= period_start,
        )
        if inclusive_end:
            query = query.filter(DailyLog.log_date <= period_end)
        else:
            query = query.filter(DailyLog.log_date < period_end)
        return query.all()

    # "end" is today's date - use inclusive_end so today's log entry counts
    # towards the current period instead of being excluded by a strict "<".
    current = logs_in(start, end, inclusive_end=True)
    previous = logs_in(prev_start, start, inclusive_end=False)

    def summarize(logs):
        total_consultations = sum(l.total_consultations for l in logs)
        no_shows = sum(l.no_shows for l in logs)
        returning = sum(l.returning_patients for l in logs)
        new_patients = sum(l.new_patients for l in logs)
        revenue = sum(float(l.revenue or 0) for l in logs)

        return {
            "patients": new_patients + returning,
            "revenue": revenue,
            "repeat_rate": (returning / total_consultations * 100) if total_consultations else 0,
            "no_show_rate": (no_shows / total_consultations * 100) if total_consultations else 0,
        }

    cur = summarize(current)
    prev = summarize(previous)

    def pct_change(new, old):
        if old == 0:
            return 0.0
        return round((new - old) / old * 100, 1)

    health_score = round(
        max(0, min(100,
            50
            + (cur["repeat_rate"] - 30) * 0.6
            - (cur["no_show_rate"] - 10) * 0.8
            + min(cur["patients"], 100) * 0.1
        ))
    )
    prev_health_score = round(
        max(0, min(100,
            50
            + (prev["repeat_rate"] - 30) * 0.6
            - (prev["no_show_rate"] - 10) * 0.8
            + min(prev["patients"], 100) * 0.1
        ))
    )

    return {
        "period": {"start": start.isoformat(), "end": end.isoformat()},
        "patients": {"value": cur["patients"], "change_pct": pct_change(cur["patients"], prev["patients"])},
        "revenue": {"value": round(cur["revenue"], 2), "change_pct": pct_change(cur["revenue"], prev["revenue"])},
        "repeat_visits": {"value": round(cur["repeat_rate"], 1), "change_pct": pct_change(cur["repeat_rate"], prev["repeat_rate"])},
        "no_show_rate": {"value": round(cur["no_show_rate"], 1), "change_pct": pct_change(cur["no_show_rate"], prev["no_show_rate"])},
        "practice_health": {"value": health_score, "change_pts": health_score - prev_health_score},
    }