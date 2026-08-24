"""
Shared helpers for the public patient-booking feature: turning a clinic
name into a URL handle, and working out which appointment slots are
actually free on a given day.

Kept separate from the routers so the slot logic can be reasoned about
(and tested) on its own - it is the part most likely to be wrong in a
way nobody notices until a patient double-books.
"""

import re
import unicodedata
from datetime import date, datetime, time, timedelta

# A slug has to survive being typed, texted and read aloud, so keep it
# to lowercase letters, digits and single hyphens.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
SLUG_MIN = 3
SLUG_MAX = 40

# Guard rails on the weekly hours the doctor can set.
MIN_SLOT_MINUTES = 5
MAX_SLOT_MINUTES = 240
MAX_WINDOWS_PER_DAY = 4

# How far ahead a patient may book. Long enough to be useful, short
# enough that the calendar can't be filled out to infinity.
MAX_DAYS_AHEAD = 60


# Words that add length to a link without helping a patient recognise
# it. Dropped from the SUGGESTED slug only - she can still type any of
# them back in if she wants.
SLUG_STOPWORDS = {
    "dr", "dr's", "doctor", "doctors", "the", "and", "of",
    "clinic", "clinics", "hospital", "centre", "center",
}

# Keep the default short enough to read out over a phone.
SLUG_SUGGEST_MAX = 20


def slugify(name: str, short: bool = True) -> str:
    """
    Turns a clinic name into a link handle.

    "Dr. Mouli's Healing Touch" -> "healing-touch"   (short, the default)
                                -> "dr-moulis-healing-touch"  (short=False)

    A booking link gets read aloud, typed from a poster and pasted into
    WhatsApp, so the suggested handle drops honorifics and generic words
    like "clinic" and stops at a word boundary. She can always edit it -
    this only decides what we propose.

    Accented characters fold to their closest ASCII equivalent so a name
    in any script still yields something typable.
    """
    if not name:
        return ""
    folded = unicodedata.normalize("NFKD", name)
    ascii_only = folded.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower().replace("'", "").replace("’", "")
    words = [w for w in re.split(r"[^a-z0-9]+", lowered) if w]

    if short:
        meaningful = [w for w in words if w not in SLUG_STOPWORDS]
        # If stripping left nothing (a clinic literally called "The
        # Clinic"), fall back to the original words rather than "".
        candidate_words = meaningful or words
        out = []
        for w in candidate_words:
            trial = "-".join(out + [w])
            if out and len(trial) > SLUG_SUGGEST_MAX:
                break
            out.append(w)
        return "-".join(out)[:SLUG_MAX].strip("-")

    return "-".join(words)[:SLUG_MAX].strip("-")


def unique_slug(base: str, taken) -> str:
    """First free variant of `base`: base, base-2, base-3, ..."""
    base = base or "clinic"
    if base not in taken:
        return base
    n = 2
    while True:
        # Leave room for the suffix without breaching SLUG_MAX.
        suffix = f"-{n}"
        candidate = f"{base[: SLUG_MAX - len(suffix)].rstrip('-')}{suffix}"
        if candidate not in taken:
            return candidate
        n += 1


def validate_slug(slug: str) -> str:
    """Returns the cleaned slug, or raises ValueError with a readable reason."""
    if slug is None:
        raise ValueError("Booking link cannot be empty")
    cleaned = slug.strip().lower()
    if len(cleaned) < SLUG_MIN:
        raise ValueError(f"Booking link must be at least {SLUG_MIN} characters")
    if len(cleaned) > SLUG_MAX:
        raise ValueError(f"Booking link must be {SLUG_MAX} characters or fewer")
    if not SLUG_RE.match(cleaned):
        raise ValueError(
            "Booking link can use only lowercase letters, numbers and hyphens"
        )
    return cleaned


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def validate_booking_hours(hours) -> dict:
    """
    Checks the weekly-hours object the doctor submits and returns it in
    a normalized form: {"0": [{"start": "10:00", "end": "13:00"}], ...}
    with weekday keys 0 (Monday) to 6 (Sunday).
    """
    if hours is None:
        return {}
    if not isinstance(hours, dict):
        raise ValueError("Opening hours are not in the expected format")

    normalized = {}
    for raw_day, windows in hours.items():
        try:
            day = int(raw_day)
        except (TypeError, ValueError):
            raise ValueError("Opening hours have an invalid day")
        if day < 0 or day > 6:
            raise ValueError("Opening hours have an invalid day")
        if windows is None:
            windows = []
        if not isinstance(windows, list):
            raise ValueError("Opening hours for a day must be a list of time windows")
        if len(windows) > MAX_WINDOWS_PER_DAY:
            raise ValueError(f"At most {MAX_WINDOWS_PER_DAY} time windows per day")

        cleaned = []
        for w in windows:
            if not isinstance(w, dict) or "start" not in w or "end" not in w:
                raise ValueError("Each time window needs a start and an end")
            try:
                start = _parse_hhmm(str(w["start"]))
                end = _parse_hhmm(str(w["end"]))
            except Exception:
                raise ValueError("Times must look like 09:00")
            if end <= start:
                raise ValueError("A window's end time must be after its start time")
            cleaned.append({"start": start.strftime("%H:%M"), "end": end.strftime("%H:%M")})

        cleaned.sort(key=lambda w: w["start"])
        for earlier, later in zip(cleaned, cleaned[1:]):
            if later["start"] < earlier["end"]:
                raise ValueError("Time windows on the same day cannot overlap")

        normalized[str(day)] = cleaned

    return normalized


def generate_day_slots(day: date, hours: dict, slot_minutes: int):
    """
    Every slot start time the clinic's weekly hours imply for `day`,
    as naive datetimes. Ignores bookings and blocks - callers subtract
    those. A slot is only produced if it fits entirely inside its
    window, so a 30-minute slot never runs past closing time.
    """
    windows = (hours or {}).get(str(day.weekday()), [])
    slots = []
    for w in windows:
        start = datetime.combine(day, _parse_hhmm(w["start"]))
        end = datetime.combine(day, _parse_hhmm(w["end"]))
        cursor = start
        step = timedelta(minutes=slot_minutes)
        while cursor + step <= end:
            slots.append(cursor)
            cursor += step
    return slots


def block_covers(start_time, end_time, hhmm: str) -> bool:
    """
    Does one blocked_slots row hide the slot starting at `hhmm`?

    Deliberately handles all three row shapes (see BlockedSlot):
      - whole day       (start None)        -> everything
      - a range         (start and end)     -> start <= hhmm < end
      - a single slot   (start, end None)   -> exact match only

    Comparing "HH:MM" strings works because they are zero-padded and
    fixed width, so lexical order is chronological order.
    """
    if not start_time:
        return True
    if end_time:
        return start_time <= hhmm < end_time
    return start_time == hhmm
