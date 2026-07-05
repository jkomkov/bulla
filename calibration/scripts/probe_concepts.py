"""Curated grounded-decision probe set for the representation-holonomy crux.

This is the experiment's instrument for *meaning*. Each probe is a decision predicate
with an UNAMBIGUOUS gold answer that a capable model should get right — so that a
model's *error* reflects a divergence in what it means by the concept, not raw
incompetence (the §6 base-rate gate needs the loop's grounded-error rate in [0.2, 0.8]).

Design (north star: does meaning fail to glue across independently-trained agents?):
  • CONVENTION classes — units, currency, timezone, date format, path, index, range,
    epoch, sign. These are exactly the cross-tool convention mismatches the whole
    program is about; a positive holonomy result *on these* ties the representation
    layer back to the agreement-layer thesis.
  • SEMANTIC divergence — polysemy, ontology/category, antonymy, magnitude. Where two
    capable models might genuinely represent a shared concept differently.

Gold answers are derived by an explicit, auditable RULE per category (not hand-typed),
so correctness is checkable by reading the rule. `build_probe_set()` returns
(all_concepts, anchors); anchors ⊂ concepts are the held-out basis used to FIT the
alignment maps and define the relative-representation space — they are NEVER scored.

Run `python probe_concepts.py` to print counts, samples, and the validation.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from grounded_decision_oracle import ProbeConcept  # noqa: E402

_MONTHS = ("January", "February", "March", "April", "May", "June",
           "July", "August", "September", "October", "November", "December")


def _ans(prompt: str) -> str:
    return prompt + " Answer with one word."


# ─────────────────────────── convention classes ───────────────────────────

def _temperature() -> list[ProbeConcept]:
    out = []
    for T in (2, 8, 18, 27, 41, 58, 72, 84, 93, 99):  # none at the 50 midpoint
        gold = "freezing" if T < 50 else "boiling"
        out.append(ProbeConcept(
            f"temp_{T}",
            _ans(f"For water at sea level, is {T} degrees Celsius closer to freezing or boiling?"),
            gold, ("freezing", "boiling")))
    return out


def _currency() -> list[ProbeConcept]:
    out = []
    for N, M in ((250, 10), (1500, 5), (99, 2), (4999, 60), (12000, 200),
                 (75, 1), (860, 3), (30000, 250), (199, 5), (50000, 400)):
        gold = "more" if N / 100 > M else "less"  # N cents vs M dollars
        out.append(ProbeConcept(
            f"cur_{N}_{M}",
            _ans(f"Is {N} US cents worth more or less than {M} US dollars?"),
            gold, ("more", "less")))
    return out


def _length() -> list[ProbeConcept]:
    out = []
    for X, Y in ((1, 10), (3, 8), (2, 3), (5, 20), (10, 25), (1, 2), (4, 16), (7, 20)):
        gold = "longer" if X * 3.28084 > Y else "shorter"  # X meters vs Y feet
        out.append(ProbeConcept(
            f"len_{X}_{Y}",
            _ans(f"Is {X} meters longer or shorter than {Y} feet?"),
            gold, ("longer", "shorter")))
    return out


def _mass() -> list[ProbeConcept]:
    out = []
    for X, Y in ((1, 1), (2, 6), (5, 9), (3, 5), (10, 25), (1, 3), (4, 7), (8, 20)):
        gold = "heavier" if X * 2.20462 > Y else "lighter"  # X kg vs Y lb
        out.append(ProbeConcept(
            f"mass_{X}_{Y}",
            _ans(f"Is {X} kilograms heavier or lighter than {Y} pounds?"),
            gold, ("heavier", "lighter")))
    return out


def _data_size() -> list[ProbeConcept]:
    out = []
    for X, Y in ((1, 500), (2, 3000), (1, 1500), (5, 4000), (10, 9000), (1, 900), (3, 2000), (8, 7000)):
        gold = "more" if X * 1_000_000 > Y * 1000 else "fewer"  # X MB vs Y KB (decimal)
        out.append(ProbeConcept(
            f"data_{X}_{Y}",
            _ans(f"Does {X} megabytes contain more or fewer bytes than {Y} kilobytes?"),
            gold, ("more", "fewer")))
    return out


def _timezone() -> list[ProbeConcept]:
    cities = (("New York", -5), ("London", 0), ("Tokyo", 9), ("Los Angeles", -8),
              ("Berlin", 1), ("Mumbai", 5), ("Chicago", -6), ("Sydney", 10))
    out = []
    for i, (H, (city, off)) in enumerate(zip((0, 3, 12, 22, 6, 9, 18, 4), cities)):
        local = (H + off) % 24
        if local in (0, 12):  # skip ambiguous boundary
            continue
        gold = "AM" if local < 12 else "PM"
        out.append(ProbeConcept(
            f"tz_{i}",
            _ans(f"It is {H:02d}:00 UTC. In {city} (UTC{off:+d}), is the local time AM or PM?"),
            gold, ("AM", "PM")))
    return out


def _date_format() -> list[ProbeConcept]:
    out = []
    for D, M in ((3, 4), (5, 11), (1, 9), (7, 2), (10, 6), (2, 12), (8, 1), (4, 10)):
        out.append(ProbeConcept(
            f"date_{D}_{M}",
            _ans(f"In the date '{D:02d}/{M:02d}' written day/month, what is the month — "
                 f"{_MONTHS[D - 1]} or {_MONTHS[M - 1]}?"),
            _MONTHS[M - 1], (_MONTHS[D - 1], _MONTHS[M - 1])))
    return out


def _index() -> list[ProbeConcept]:
    ordinals = (("1st", 1), ("2nd", 2), ("3rd", 3), ("4th", 4), ("5th", 5),
                ("7th", 7), ("9th", 9), ("10th", 10))
    out = []
    for word, n in ordinals:
        out.append(ProbeConcept(
            f"idx_{n}",
            _ans(f"In a 0-indexed array, is the {word} element at index {n - 1} or {n}?"),
            str(n - 1), (str(n - 1), str(n))))
    return out


def _range_inclusive() -> list[ProbeConcept]:
    out = []
    for a, b in ((1, 4), (0, 5), (2, 10), (3, 7), (1, 100), (5, 8), (0, 3), (10, 20)):
        out.append(ProbeConcept(
            f"rng_hi_{a}_{b}", _ans(f"Does Python range({a}, {b}) include the number {b}?"),
            "no", ("yes", "no")))
        out.append(ProbeConcept(
            f"rng_lo_{a}_{b}", _ans(f"Does Python range({a}, {b}) include the number {a}?"),
            "yes", ("yes", "no")))
    return out


def _path() -> list[ProbeConcept]:
    rel = ("src/main.py", "lib/util.js", "docs/readme.md", "a/b/c.txt")
    ab = ("/etc/hosts", "/usr/bin/python", "/home/user/x", "/var/log/y.log")
    out = []
    for p in rel:
        out.append(ProbeConcept(
            f"path_rel_{p.replace('/', '_').replace('.', '_')}",
            _ans(f"Does the repo-relative path '{p}' begin at the filesystem root '/'?"),
            "no", ("yes", "no")))
    for p in ab:
        out.append(ProbeConcept(
            f"path_abs_{p.replace('/', '_').replace('.', '_')}",
            _ans(f"Does the path '{p}' begin at the filesystem root '/'?"),
            "yes", ("yes", "no")))
    return out


def _epoch() -> list[ProbeConcept]:
    # seconds since 1970; boundary = 946684800 (Jan 1 2000)
    out = []
    for ts in (1_700_000_000, 500_000_000, 1_000_000_000, 800_000_000,
               1_400_000_000, 200_000_000, 1_600_000_000, 100_000_000):
        gold = "after" if ts > 946_684_800 else "before"
        out.append(ProbeConcept(
            f"epoch_{ts}",
            _ans(f"The Unix timestamp {ts} counts seconds since 1970. Is the moment it "
                 f"represents before or after the year 2000?"),
            gold, ("before", "after")))
    return out


def _sign() -> list[ProbeConcept]:
    out = []
    for n in (120, -50, 1000, -3, 75, -200, 5, -1500):
        gold = "credit" if n > 0 else "debit"
        out.append(ProbeConcept(
            f"sign_{n}",
            _ans(f"A bank account balance reads {n} dollars. Is the account in credit or debit?"),
            gold, ("credit", "debit")))
    return out


# ─────────────────────────── semantic divergence ───────────────────────────

def _polysemy() -> list[ProbeConcept]:
    rows = (
        ("river bank", "bank", "land", "money"),
        ("savings bank", "bank", "money", "land"),
        ("baseball bat", "bat", "club", "animal"),
        ("bat in the cave", "bat", "animal", "club"),
        ("spring water", "spring", "season", "coil"),
        ("spring in the watch", "spring", "coil", "season"),
        ("computer mouse", "mouse", "device", "rodent"),
        ("field mouse", "mouse", "rodent", "device"),
        ("light as a feather", "light", "weight", "lamp"),
        ("turn on the light", "light", "lamp", "weight"),
        ("date on the calendar", "date", "day", "fruit"),
        ("eat a date", "date", "fruit", "day"),
    )
    return [ProbeConcept(
        f"poly_{i}",
        _ans(f"In the phrase '{phrase}', does '{word}' mean {a} or {b}?"),
        a, (a, b)) for i, (phrase, word, a, b) in enumerate(rows)]


def _category() -> list[ProbeConcept]:
    fruit = ("tomato", "cucumber", "pumpkin", "avocado", "pepper")
    veg = ("carrot", "potato", "spinach", "celery", "onion")
    out = []
    for x in fruit:
        out.append(ProbeConcept(f"cat_{x}",
                   _ans(f"Botanically, is a {x} a fruit or a vegetable?"),
                   "fruit", ("fruit", "vegetable")))
    for x in veg:
        out.append(ProbeConcept(f"cat_{x}",
                   _ans(f"Botanically, is a {x} a fruit or a vegetable?"),
                   "vegetable", ("fruit", "vegetable")))
    return out


def _antonym() -> list[ProbeConcept]:
    rows = (("ascend", "rise", "fall"), ("frigid", "cold", "hot"),
            ("expand", "grow", "shrink"), ("ancient", "old", "new"),
            ("scarce", "rare", "plentiful"), ("conceal", "hide", "reveal"),
            ("rapid", "fast", "slow"), ("vacant", "empty", "full"))
    return [ProbeConcept(f"anto_{w}",
            _ans(f"Is '{w}' closer in meaning to '{a}' or '{b}'?"),
            a, (a, b)) for w, a, b in rows]


def _magnitude() -> list[ProbeConcept]:
    rows = (("a kilometer", "a mile", "shorter", "longer"),
            ("a gram", "an ounce", "lighter", "heavier"),
            ("a liter", "a gallon", "smaller", "larger"),
            ("an inch", "a centimeter", "longer", "shorter"),
            ("a decade", "a century", "shorter", "longer"),
            ("a megabyte", "a gigabyte", "smaller", "larger"),
            ("a penny", "a dime", "less", "more"),
            ("walking pace", "highway speed", "slower", "faster"))
    return [ProbeConcept(f"mag_{i}",
            _ans(f"Is {a} {cmp_a} or {cmp_b} than {b}?"),
            cmp_a, (cmp_a, cmp_b)) for i, (a, b, cmp_a, cmp_b) in enumerate(rows)]


def _state() -> list[ProbeConcept]:
    out = []
    for T in (-20, -5, 15, 30, -10, 25, -2, 40):
        gold = "frozen" if T < 0 else "liquid"
        out.append(ProbeConcept(f"state_{T}",
                   _ans(f"At {T} degrees Celsius and normal pressure, is pure water frozen or liquid?"),
                   gold, ("frozen", "liquid")))
    return out


_CATEGORIES = (
    _temperature, _currency, _length, _mass, _data_size, _timezone, _date_format,
    _index, _range_inclusive, _path, _epoch, _sign,            # convention classes
    _polysemy, _category, _antonym, _magnitude, _state,        # semantic divergence
)


def build_probe_set() -> tuple[tuple[ProbeConcept, ...], tuple[ProbeConcept, ...]]:
    """Return (all_concepts, anchors). Anchors ⊂ concepts: the first 2 of each category
    (a category-diverse held-out basis for the relative-rep space + Procrustes fit),
    never scored. The rest are the scored decision probes."""
    concepts: list[ProbeConcept] = []
    anchors: list[ProbeConcept] = []
    seen: set[str] = set()
    for cat in _CATEGORIES:
        items = cat()
        for j, pc in enumerate(items):
            if pc.concept_id in seen:
                raise ValueError(f"duplicate concept_id {pc.concept_id!r}")
            seen.add(pc.concept_id)
            concepts.append(pc)
            if j < 2:  # 2 anchors per category → diverse basis
                anchors.append(pc)
    return tuple(concepts), tuple(anchors)


if __name__ == "__main__":
    concepts, anchors = build_probe_set()
    scored = [c for c in concepts if c not in anchors]
    # validation
    for c in concepts:
        assert c.gold in c.choices, f"{c.concept_id}: gold not in choices"
        assert len(c.choices) == 2, f"{c.concept_id}: expected 2 choices"
    assert len({c.concept_id for c in concepts}) == len(concepts), "duplicate ids"
    assert set(anchors) <= set(concepts), "anchors must be a subset of concepts"
    print(f"total concepts : {len(concepts)}")
    print(f"anchors (basis): {len(anchors)}  (never scored)")
    print(f"scored probes  : {len(scored)}")
    print(f"categories     : {len(_CATEGORIES)}")
    print("\nsamples:")
    for c in (concepts[0], concepts[len(concepts) // 2], concepts[-1]):
        print(f"  [{c.concept_id}] {c.prompt}  -> gold={c.gold!r} of {c.choices}")
