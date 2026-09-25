"""
End-to-end test through the real persist path (revised — no gating).

Confirms the thing the operator actually asked for this time: whatever Claude and Jev
decide is what gets written. The `reason` (parsed from Jev's composite choice) and
`loss_gate` (informational label) are recorded for the log page, but never change the
action, never suppress it, and never block on a missing thesis, tracker entry, or
confidence level. Runs against a scratch copy of the live database.

Also covers the tracker: a name you don't own gets its own choice set, its own
book label, the five lenses, and no loss gate. And carry-forward (0027): an unchanged
subject's rows are copied exactly, marked with the deciding run, and never alert.

Run: docker compose exec worker python test_persist_e2e.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

from advisory import AdvisoryService, HORIZONS_BY_BOOK, LENSES, horizons_for

SRC = os.environ.get("TRADAI_DATA_DIR", "/data") + "/tradai.sqlite"


def answer(choice: str, confidence: float = 0.9) -> dict:
    return {"type": "choice", "choice": choice, "confidence": confidence}


TRACKED_SYMBOL = "ZZTEST.PA"


def seed_tracked(conn):
    """A tracked name of our own, so the case doesn't depend on what the operator tracks."""
    conn.execute(
        "INSERT INTO instruments (symbol, currency, kind, region, name, created_at, updated_at)"
        " VALUES (?, 'EUR', 'equity', 'eu', 'Scratch Co', 'now', 'now')"
        " ON CONFLICT DO NOTHING",
        (TRACKED_SYMBOL,),
    )
    iid = int(
        conn.execute("SELECT id FROM instruments WHERE symbol = ?", (TRACKED_SYMBOL,)).fetchone()[0]
    )
    conn.execute("DELETE FROM tracker")
    conn.execute(
        "INSERT INTO tracker (symbol, instrument_id, name, added_at, updated_at)"
        " VALUES (?, ?, 'Scratch Co', 'now', 'now')",
        (TRACKED_SYMBOL, iid),
    )
    conn.commit()
    return iid


def run_case(name, *, choices, realized_gains=0.0, expect_action, expect_reason=None,
             expect_gate=None, symbol="DDD", book="portfolio", expect_lenses=None):
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.sqlite")
    shutil.copy(SRC, path)

    svc = AdvisoryService(path)
    conn = svc.connect()
    conn.executemany(
        "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, 'x')"
        " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        [
            ("realized_gains_ytd_override_amount", str(realized_gains)),
            ("realized_gains_ytd_override_currency", "EUR"),
        ],
    )
    conn.execute("DELETE FROM realized_disposals")  # scratch copy would double-count vs the override
    conn.commit()

    if book == "tracker":
        seed_tracked(conn)
    else:
        # Keep the portfolio cases decoupled from whatever is on the live tracker.
        conn.execute("DELETE FROM tracker")
        conn.commit()

    context = svc._build_context(conn)
    subjects = svc._subjects(context)
    target = next(s for s in subjects if s["symbol"] == symbol)
    iid = int(target["instrument_id"])

    # Every lens answers for every subject it is asked about.
    lens_answers = {}
    for lens in LENSES:
        per_lens = {}
        for s_ in svc._subjects(context, lens=lens):
            default = "keep_watching" if s_.get("book") == "tracker" else "hold"
            for hz in horizons_for(s_.get("book", "portfolio")):
                per_lens[f"{int(s_['instrument_id'])}_{hz}"] = answer(default)
        lens_answers[lens] = per_lens

    for hz, choice in choices.items():
        lens_answers["combined"][f"{iid}_{hz}"] = answer(choice)
    combined = lens_answers["combined"]

    cur = conn.execute(
        "INSERT INTO agent_runs (trigger_kind, status, started_at) VALUES ('manual', 'running', 'now')"
    )
    run_id = int(cur.lastrowid)
    conn.commit()

    svc._persist_recommendations(conn, run_id, context, {}, lens_answers, combined, {}, log=[])

    # 0020: books are judged over different horizons now — read back the middle one of
    # whichever set this book uses ('12m' for portfolio, '3m' for tracker).
    mid_horizon = horizons_for(book)[1]
    row = conn.execute(
        "SELECT action, reason, loss_gate, suppressed, book, jev_lenses_json, explanation FROM recommendations"
        " WHERE agent_run_id = ? AND instrument_id = ? AND horizon = ?",
        (run_id, iid, mid_horizon),
    ).fetchone()
    persisted_horizons = {
        r["horizon"] for r in conn.execute(
            "SELECT horizon FROM recommendations WHERE agent_run_id = ? AND instrument_id = ?",
            (run_id, iid),
        )
    }

    conn.close()
    shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    if row["action"] != expect_action:
        print(f"  FAIL {name}: action {row['action']!r} != {expect_action!r}"); ok = False
    if bool(row["suppressed"]):
        print(f"  FAIL {name}: something is still suppressing actions ({row['suppressed']})"); ok = False
    if expect_reason is not None and row["reason"] != expect_reason:
        print(f"  FAIL {name}: reason {row['reason']!r} != {expect_reason!r}"); ok = False
    if expect_gate is not None and row["loss_gate"] != expect_gate:
        print(f"  FAIL {name}: gate {row['loss_gate']!r} != {expect_gate!r}"); ok = False
    if row["book"] != book:
        print(f"  FAIL {name}: book {row['book']!r} != {book!r}"); ok = False
    # 0028: no explanation (Claude skipped or failed) never stops the decision persisting.
    if row["explanation"] is not None:
        print(f"  FAIL {name}: unexpected explanation {row['explanation']!r}"); ok = False
    # 0020: check persisted rows against the decision, not HORIZONS_BY_BOOK used
    # to construct the inputs above, so a wrong production map cannot pass itself.
    expected_horizons = {"portfolio": {"6m", "12m", "24m"}, "tracker": {"1m", "3m", "6m"}}[book]
    if persisted_horizons != expected_horizons:
        print(f"  FAIL {name}: horizons {persisted_horizons!r} != {expected_horizons!r}"); ok = False
    if expect_lenses is not None:
        got = sorted(json.loads(row["jev_lenses_json"] or "{}").keys())
        if got != sorted(expect_lenses):
            print(f"  FAIL {name}: lenses {got} != {sorted(expect_lenses)}"); ok = False
    if ok:
        print(f"  ok  {name}: {row['action']} "
              f"(book={row['book']}, reason={row['reason']}, gate={row['loss_gate']})")
    return ok


print("DDD is -95.4%, no approved thesis, nothing on the tracker — none of that blocks anything now\n")
results = [
    # The exact case that used to be suppressed: no thesis, no pair, price/momentum framing.
    run_case("plain sell passes through untouched",
             choices={h: "sell" for h in HORIZONS_BY_BOOK["portfolio"]},
             expect_action="sell", expect_reason="legacy"),

    run_case("sell_thesis_broken passes through even with no approved thesis on record",
             choices={h: "sell_thesis_broken" for h in HORIZONS_BY_BOOK["portfolio"]},
             expect_action="sell", expect_reason="thesis_broken"),

    run_case("sell_better_use passes through with no tracker match",
             choices={h: "sell_better_use" for h in HORIZONS_BY_BOOK["portfolio"]},
             expect_action="sell", expect_reason="better_use"),

    # loss_gate is still computed and shown — informationally
    run_case("loss + no gains + 24m sell -> labelled no_recovery_24m",
             choices={h: "sell_thesis_broken" for h in HORIZONS_BY_BOOK["portfolio"]}, realized_gains=0.0,
             expect_action="sell", expect_gate="no_recovery_24m"),

    run_case("loss + same-year gain -> labelled offset_same_year",
             choices={h: "sell_thesis_broken" for h in HORIZONS_BY_BOOK["portfolio"]}, realized_gains=9000.0,
             expect_action="sell", expect_gate="offset_same_year"),

    run_case("loss + no gain + 24m disagrees -> no label, but STILL sells",
             choices={"6m": "sell_thesis_broken", "12m": "sell_thesis_broken", "24m": "hold"},
             realized_gains=0.0,
             expect_action="sell", expect_gate=None),

    run_case("hold passes through", choices={h: "hold" for h in HORIZONS_BY_BOOK["portfolio"]}, expect_action="hold",
             expect_lenses=list(LENSES)),
]

print("\nTracker — a name you don't own, its own verbs, five lenses, no loss gate\n")
results += [
    run_case("buy_now becomes buy/entry_now and is labelled tracker",
             symbol=TRACKED_SYMBOL, book="tracker",
             choices={h: "buy_now" for h in HORIZONS_BY_BOOK["tracker"]},
             expect_action="buy", expect_reason="entry_now", expect_gate=None),

    # The regression that shipped broken once: `wait_better_entry` was being read with the
    # portfolio map, which doesn't know the verb, so it silently degraded to the
    # insufficient_evidence fallback and the "wait for a better price" signal was lost.
    run_case("wait_better_entry keeps its own reason, not the generic fallback",
             symbol=TRACKED_SYMBOL, book="tracker",
             choices={h: "wait_better_entry" for h in HORIZONS_BY_BOOK["tracker"]},
             expect_action="watch", expect_reason="await_better_entry"),

    run_case("keep_watching maps onto the shared watch/insufficient_evidence",
             symbol=TRACKED_SYMBOL, book="tracker",
             choices={h: "keep_watching" for h in HORIZONS_BY_BOOK["tracker"]},
             expect_action="watch", expect_reason="insufficient_evidence"),

    run_case("drop_lost_interest persists as the tracker-only `drop` action",
             symbol=TRACKED_SYMBOL, book="tracker",
             choices={h: "drop_lost_interest" for h in HORIZONS_BY_BOOK["tracker"]},
             expect_action="drop", expect_reason="lost_interest"),

    # 0022/0027: Tracker recommendations persist every layer lens, news included.
    run_case("tracker rows carry all five lenses",
             symbol=TRACKED_SYMBOL, book="tracker",
             choices={h: "keep_watching" for h in HORIZONS_BY_BOOK["tracker"]},
             expect_action="watch",
             expect_lenses=list(LENSES)),

    # A holding's verbs are meaningless here; an unmapped choice must fall back safely
    # rather than write a portfolio reason onto a tracker row.
    run_case("a portfolio verb on a tracker row degrades to watch, never a sell",
             symbol=TRACKED_SYMBOL, book="tracker",
             choices={h: "sell_thesis_broken" for h in HORIZONS_BY_BOOK["tracker"]},
             expect_action="watch", expect_reason="insufficient_evidence"),
]



def carry_case():
    """0027: a carried subject keeps Jev's exact decision, names its deciding run, and is silent."""
    name = "carried rows copy the decision and explanation, point at the deciding run, and never alert"
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "t.sqlite")
    shutil.copy(SRC, path)
    svc = AdvisoryService(path)
    conn = svc.connect()
    conn.execute("DELETE FROM tracker")
    conn.commit()

    context = svc._build_context(conn)
    target = next(s for s in svc._subjects(context) if s["symbol"] == "DDD")
    iid = int(target["instrument_id"])
    lens_answers = {lens: {} for lens in LENSES}
    for s_ in svc._subjects(context):
        for hz in horizons_for("portfolio"):
            choice = "buy_new_conviction" if int(s_["instrument_id"]) == iid else "hold"
            for lens in LENSES:
                lens_answers[lens][f"{int(s_['instrument_id'])}_{hz}"] = answer(choice)

    def new_run():
        cur = conn.execute(
            "INSERT INTO agent_runs (trigger_kind, status, started_at) VALUES ('manual', 'running', 'now')"
        )
        conn.commit()
        return int(cur.lastrowid)

    first = new_run()
    explained = {
        "market_read": "Markets calm.",
        "by_symbol": {"DDD": {"text": "Bought on new evidence.", "tension": "Technicals disagree."}},
    }
    svc._persist_recommendations(
        conn, first, context, {}, lens_answers, lens_answers["combined"], {}, log=[],
        explanations=explained,
    )
    rows = conn.execute(
        "SELECT * FROM recommendations WHERE agent_run_id = ? AND instrument_id = ?", (first, iid)
    ).fetchall()
    second = new_run()
    prior = {"record": {"decided_run_id": first, "decided_at": "2026-09-24"}, "rows": rows, "run_id": first}
    carried = svc._carry_forward_all(conn, second, context, {iid: prior}, [iid])
    svc._raise_alerts_for_run(conn, second)
    got = conn.execute(
        "SELECT action, reason, carried_from_run_id, conversation_json, explanation FROM recommendations"
        " WHERE agent_run_id = ? AND instrument_id = ? ORDER BY horizon",
        (second, iid),
    ).fetchall()
    first_alerts = conn.execute(
        "SELECT COUNT(*) FROM alerts a JOIN recommendations r ON r.id = a.recommendation_id"
        " WHERE r.agent_run_id = ?", (first,),
    ).fetchone()[0]
    second_alerts = conn.execute(
        "SELECT COUNT(*) FROM alerts a JOIN recommendations r ON r.id = a.recommendation_id"
        " WHERE r.agent_run_id = ?", (second,),
    ).fetchone()[0]
    conn.close()
    shutil.rmtree(tmp, ignore_errors=True)

    ok = True
    if carried != 3 or len(got) != 3:
        print(f"  FAIL {name}: carried {carried} rows, read back {len(got)}"); ok = False
    if any(r["action"] != "buy" or r["reason"] != "new_conviction" for r in got):
        print(f"  FAIL {name}: carried action changed {[tuple(r)[:2] for r in got]}"); ok = False
    if any(r["carried_from_run_id"] != first for r in got):
        print(f"  FAIL {name}: carried_from_run_id not the deciding run"); ok = False
    if any(json.loads(r["conversation_json"])[-1].get("kind") != "carried" for r in got):
        print(f"  FAIL {name}: conversation not marked carried"); ok = False
    # 0028: one explanation on every horizon row, kept when carried, and in the transcript.
    want = {"text": "Bought on new evidence.", "tension": "Technicals disagree.", "market_read": "Markets calm."}
    if any(json.loads(r["explanation"] or "null") != want for r in list(rows) + list(got)):
        print(f"  FAIL {name}: explanation not on every row / not carried"); ok = False
    if not any(t.get("kind") == "explanation" for t in json.loads(got[0]["conversation_json"])):
        print(f"  FAIL {name}: explanation turn missing from the transcript"); ok = False
    if first_alerts == 0 or second_alerts != 0:
        print(f"  FAIL {name}: alerts first={first_alerts} carried={second_alerts}"); ok = False
    if ok:
        print(f"  ok  {name}")
    return ok


print("\nCarry-forward (0027)\n")
results.append(carry_case())

print()
if all(results):
    print(f"ALL {len(results)} END-TO-END TESTS PASSED — Claude/Jev's decision is never overridden")
else:
    print(f"FAILURES: {results.count(False)} of {len(results)}")
    sys.exit(1)
