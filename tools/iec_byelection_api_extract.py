#!/usr/bin/env python3
"""Fresh extraction of South African municipal by-election data from the IEC HTTP API.

No previous scraped/report data is used. Discovery starts at the IEC ElectoralEvent API,
then candidates, ward results, delimitation and VD results are fetched from the API.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

BASE = os.environ.get("IEC_API_BASE", "https://api.elections.org.za").rstrip("/")
OUT = Path(os.environ.get("IEC_OUT", "iec_api_output"))
OUT.mkdir(parents=True, exist_ok=True)
START_DATE = os.environ.get("IEC_START_DATE", "2021-11-02")
END_DATE = os.environ.get("IEC_END_DATE", "2026-08-13")
TIMEOUT = int(os.environ.get("IEC_TIMEOUT", "45"))
PAUSE = float(os.environ.get("IEC_PAUSE", "0.03"))

session = requests.Session()
session.headers.update({
    "Accept": "application/json, text/json;q=0.9, */*;q=0.1",
    "User-Agent": "Netwerk24-IEC-byelection-research/1.0",
})

audit: list[dict[str, Any]] = []


def scalar(v: Any) -> Any:
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False, sort_keys=True)
    return v


def norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def get_ci(d: dict[str, Any], *names: str, default=None):
    nmap = {norm_key(k): v for k, v in d.items()}
    for name in names:
        if norm_key(name) in nmap:
            return nmap[norm_key(name)]
    return default


def as_int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        try:
            return int(float(v))
        except (TypeError, ValueError):
            return None


def iso_date_from_any(v: Any) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    m = re.search(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        return f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(\d{1,2})[-/](\d{1,2})[-/](20\d{2})", s)
    if m:
        return f"{int(m.group(3)):04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    # .NET JSON date
    m = re.search(r"/Date\((\d+)", s)
    if m:
        try:
            return datetime.fromtimestamp(int(m.group(1))/1000, tz=timezone.utc).date().isoformat()
        except Exception:
            pass
    # Month-name strings such as 19 Jun 2024
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %d %Y", "%B %d %Y"):
        try:
            return datetime.strptime(s[:30], fmt).date().isoformat()
        except ValueError:
            pass
    return None


def event_date(rec: dict[str, Any]) -> str | None:
    preferred = [
        "ElectionDate", "EventDate", "ElectoralEventDate", "VotingDate", "Date",
        "StartDate", "EventStartDate", "BallotDate"
    ]
    for k in preferred:
        v = get_ci(rec, k)
        x = iso_date_from_any(v)
        if x:
            return x
    # scan all scalar fields as a fallback
    for v in rec.values():
        if isinstance(v, (str, int, float)):
            x = iso_date_from_any(v)
            if x:
                return x
    return None


def event_id(rec: dict[str, Any]) -> int | None:
    for k in ("ElectoralEventID", "ElectoralEventId", "EventID", "EventId", "ID", "Id"):
        x = as_int(get_ci(rec, k))
        if x is not None:
            return x
    return None


def event_type_id(rec: dict[str, Any]) -> int | None:
    for k in ("ElectoralEventTypeID", "ElectoralEventTypeId", "EventTypeID", "EventTypeId", "ID", "Id"):
        x = as_int(get_ci(rec, k))
        if x is not None:
            return x
    return None


def text_blob(rec: dict[str, Any]) -> str:
    return " | ".join(str(v) for v in rec.values() if isinstance(v, (str, int, float))).lower()


def api_get(path: str, params: dict[str, Any] | None = None, label: str = "") -> Any:
    url = BASE + path
    last_exc = None
    for attempt in range(1, 5):
        started = time.time()
        try:
            r = session.get(url, params=params, timeout=TIMEOUT)
            elapsed = round(time.time() - started, 3)
            row = {
                "timestamp_utc": datetime.now(timezone.utc).isoformat(),
                "label": label,
                "url": r.url,
                "status_code": r.status_code,
                "elapsed_s": elapsed,
                "attempt": attempt,
                "ok": r.ok,
                "content_type": r.headers.get("Content-Type", ""),
                "error": "",
            }
            if r.ok:
                try:
                    data = r.json()
                except Exception as e:
                    row["ok"] = False
                    row["error"] = f"JSON decode: {e}; body={r.text[:500]!r}"
                    audit.append(row)
                    raise RuntimeError(row["error"])
                audit.append(row)
                time.sleep(PAUSE)
                return data
            row["error"] = r.text[:1000].replace("\n", " ")
            audit.append(row)
            if r.status_code in (400, 401, 403, 404):
                raise requests.HTTPError(f"{r.status_code} {r.url}: {r.text[:300]}", response=r)
            last_exc = requests.HTTPError(f"{r.status_code} {r.url}", response=r)
        except requests.HTTPError:
            raise
        except Exception as e:
            last_exc = e
            audit.append({
                "timestamp_utc": datetime.now(timezone.utc).isoformat(), "label": label,
                "url": url, "status_code": "", "elapsed_s": round(time.time()-started,3),
                "attempt": attempt, "ok": False, "content_type": "", "error": repr(e)
            })
        if attempt < 4:
            time.sleep(attempt * 2)
    raise RuntimeError(f"API request failed after retries: {url} {params}: {last_exc}")


def write_csv(path: Path, rows: list[dict[str, Any]], preferred: list[str] | None = None):
    preferred = preferred or []
    keys = []
    seen = set()
    for k in preferred:
        if k not in seen:
            keys.append(k); seen.add(k)
    for row in rows:
        for k in row:
            if k not in seen:
                keys.append(k); seen.add(k)
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: scalar(row.get(k)) for k in keys})


def flatten_record(prefix: str, rec: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in rec.items():
        if isinstance(v, (dict, list)):
            out[f"{prefix}{k}"] = json.dumps(v, ensure_ascii=False, sort_keys=True)
        else:
            out[f"{prefix}{k}"] = v
    return out


def discover_events() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[int]]:
    types_raw = api_get("/api/v1/ElectoralEvent", label="event_types")
    types = types_raw if isinstance(types_raw, list) else [types_raw]
    type_rows = [r for r in types if isinstance(r, dict)]
    write_csv(OUT / "electoral_event_types_api.csv", type_rows)
    (OUT / "electoral_event_types_raw.json").write_text(json.dumps(types_raw, indent=2, ensure_ascii=False), encoding="utf-8")

    likely = []
    for r in type_rows:
        blob = text_blob(r)
        tid = event_type_id(r)
        if tid is not None and ("by-election" in blob or "by election" in blob or "byelection" in blob):
            likely.append(tid)

    # Query type IDs found in the catalogue. If labels are unhelpful, probe a modest ID range.
    type_ids = sorted(set(x for x in (event_type_id(r) for r in type_rows) if x is not None))
    probe_ids = sorted(set(likely or type_ids or list(range(1, 21))))
    if not likely:
        probe_ids = sorted(set(probe_ids + list(range(1, 21))))

    all_events: dict[int, dict[str, Any]] = {}
    by_type: dict[int, list[dict[str, Any]]] = {}
    for tid in probe_ids:
        try:
            data = api_get("/api/v1/ElectoralEvent", {"ElectoralEventTypeID": tid}, label=f"events_type_{tid}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code in (400, 404):
                continue
            raise
        recs = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        recs = [r for r in recs if isinstance(r, dict)]
        if recs:
            by_type[tid] = recs
        for r in recs:
            eid = event_id(r)
            if eid is not None:
                rr = dict(r); rr["_queried_event_type_id"] = tid
                all_events[eid] = rr

    # Find event types that behave like by-election types by event text and, as a decisive test,
    # whether ByElectionCandidatesByEvent returns candidate rows.
    selected_type_ids = set(likely)
    candidate_positive_events: set[int] = set()
    for tid, recs in by_type.items():
        if any("by-election" in text_blob(r) or "by election" in text_blob(r) or "byelection" in text_blob(r) for r in recs):
            selected_type_ids.add(tid)

    if not selected_type_ids:
        # Sample every discovered event from 2021 onward until candidate endpoint identifies the relevant type(s).
        for tid, recs in by_type.items():
            for r in recs:
                d = event_date(r)
                eid = event_id(r)
                if not eid or (d and d < START_DATE):
                    continue
                try:
                    c = api_get("/api/v1/ByElectionCandidatesByEvent", {"ElectoralEventID": eid}, label=f"candidate_probe_{eid}")
                except requests.HTTPError as e:
                    if e.response is not None and e.response.status_code in (400, 404):
                        continue
                    raise
                if isinstance(c, list) and c:
                    selected_type_ids.add(tid)
                    candidate_positive_events.add(eid)
                    break

    selected = []
    for eid, r in sorted(all_events.items()):
        tid = as_int(r.get("_queried_event_type_id"))
        if tid not in selected_type_ids:
            continue
        d = event_date(r)
        # Keep unknown-date records for later candidate validation, but apply boundaries where date is parseable.
        if d and (d < START_DATE or d > END_DATE):
            continue
        rr = dict(r)
        rr["electoral_event_id_normalized"] = eid
        rr["event_date_iso"] = d
        rr["event_type_id_normalized"] = tid
        selected.append(rr)

    write_csv(OUT / "byelection_events_api.csv", selected,
              ["event_date_iso", "electoral_event_id_normalized", "event_type_id_normalized"])
    (OUT / "event_discovery.json").write_text(json.dumps({
        "selected_type_ids": sorted(selected_type_ids), "probe_type_ids": probe_ids,
        "selected_event_count": len(selected), "start_date": START_DATE, "end_date": END_DATE
    }, indent=2), encoding="utf-8")
    return selected, type_rows, sorted(selected_type_ids)


def main():
    events, type_rows, selected_type_ids = discover_events()
    if not events:
        raise RuntimeError("No by-election events discovered. See electoral_event_types_raw.json and api_extraction_audit.csv")

    event_rows = []
    candidate_rows = []
    ward_rows = []
    ward_party_rows = []
    vd_rows = []
    vd_party_rows = []
    reconciliation_rows = []

    seen_wards = set()
    event_with_candidates = 0

    for idx, ev in enumerate(events, 1):
        eid = as_int(ev.get("electoral_event_id_normalized")) or event_id(ev)
        d = ev.get("event_date_iso") or event_date(ev)
        print(f"[{idx}/{len(events)}] Event {eid} {d or ''}", flush=True)
        try:
            candidates = api_get("/api/v1/ByElectionCandidatesByEvent", {"ElectoralEventID": eid}, label=f"candidates_event_{eid}")
        except requests.HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                reconciliation_rows.append({"event_id": eid, "event_date": d, "status": "candidate_endpoint_404", "ward_id": ""})
                continue
            raise
        if not isinstance(candidates, list):
            candidates = []
        if not candidates:
            reconciliation_rows.append({"event_id": eid, "event_date": d, "status": "no_candidates", "ward_id": ""})
            continue
        event_with_candidates += 1
        event_rows.append(ev)

        wards: dict[tuple[int,int,int], list[dict[str, Any]]] = defaultdict(list)
        for c in candidates:
            if not isinstance(c, dict):
                continue
            prov = as_int(get_ci(c, "ProvinceId", "ProvinceID"))
            mun = as_int(get_ci(c, "MunicipalityId", "MunicipalityID"))
            ward = as_int(get_ci(c, "WardId", "WardID"))
            crow = {
                "event_date_iso": d, "electoral_event_id": eid,
                "province_id": prov, "municipality_id": mun, "ward_id": ward,
                "candidate_id": get_ci(c, "CandidateID", "CandidateId"),
                "party_id": get_ci(c, "PartyID", "PartyId"),
                "party_abbr": get_ci(c, "PartyAbbr", "PartyAbbreviation"),
                "party_name": get_ci(c, "PartyName"),
                "candidate_name": get_ci(c, "CandidateName"),
                "by_election_type_id": get_ci(c, "ByElectionTypeId", "ByElectionTypeID"),
                **flatten_record("api_", c),
            }
            candidate_rows.append(crow)
            if prov is not None and mun is not None and ward not in (None, 0):
                wards[(prov, mun, ward)].append(c)

        for (prov, mun, ward), ward_candidates in sorted(wards.items()):
            key = (eid, prov, mun, ward)
            if key in seen_wards:
                continue
            seen_wards.add(key)
            try:
                result = api_get("/api/v1/LGEBallotResults", {
                    "ElectoralEventID": eid, "ProvinceID": prov,
                    "MunicipalityID": mun, "WardID": ward
                }, label=f"ward_result_{eid}_{ward}")
            except requests.HTTPError as e:
                reconciliation_rows.append({"event_id": eid, "event_date": d, "province_id": prov,
                    "municipality_id": mun, "ward_id": ward, "status": f"ward_result_http_{getattr(e.response,'status_code','')}"})
                continue
            if not isinstance(result, dict):
                reconciliation_rows.append({"event_id": eid, "event_date": d, "province_id": prov,
                    "municipality_id": mun, "ward_id": ward, "status": "ward_result_not_object"})
                continue

            parties = get_ci(result, "PartyBallotResults", default=[]) or []
            if not isinstance(parties, list): parties = []
            ranked = sorted([p for p in parties if isinstance(p, dict)],
                            key=lambda p: (as_int(get_ci(p, "Ward_ValidVotes", "ValidVotes", "TotalValidVotes")) or 0), reverse=True)
            winner = ranked[0] if ranked else {}
            winner_pid = as_int(get_ci(winner, "ID", "PartyID", "PartyId"))
            winner_candidate = next((c for c in ward_candidates if as_int(get_ci(c, "PartyID", "PartyId")) == winner_pid), {})
            wrow = {
                "event_date_iso": d, "electoral_event_id": eid,
                "province_id": prov, "province": get_ci(result, "Province"),
                "municipality_id": mun, "municipality": get_ci(result, "Municipality"),
                "ward_id": ward,
                "registered_voters": get_ci(result, "RegisteredVoters"),
                "spoilt_votes": get_ci(result, "SpoiltVotes"),
                "special_votes": get_ci(result, "SpecialVotes"),
                "voter_turnout_percent": get_ci(result, "PercVoterTurnout"),
                "total_votes_cast": get_ci(result, "TotalVotesCast"),
                "total_valid_votes": get_ci(result, "TotalValidVotes"),
                "vd_count": get_ci(result, "VDCount"),
                "vd_with_results_captured": get_ci(result, "VDWithResultsCaptured"),
                "results_complete": get_ci(result, "bResultsComplete"),
                "report_date": get_ci(result, "ReportDate"),
                "winner_party_id": winner_pid,
                "winner_party_name": get_ci(winner, "Name", "PartyName"),
                "winner_party_abbr": get_ci(winner, "PartyAbbr", "Abbreviation"),
                "winner_votes": get_ci(winner, "Ward_ValidVotes", "ValidVotes", "TotalValidVotes"),
                "winner_percent": get_ci(winner, "PercOfVotes"),
                "winner_candidate_id": get_ci(winner_candidate, "CandidateID", "CandidateId"),
                "winner_candidate_name": get_ci(winner_candidate, "CandidateName"),
                **flatten_record("api_", {k:v for k,v in result.items() if norm_key(k) != norm_key("PartyBallotResults")}),
            }
            ward_rows.append(wrow)
            reconciliation_rows.append({"event_id": eid, "event_date": d, "province_id": prov,
                "municipality_id": mun, "ward_id": ward, "status": "ok",
                "candidate_count": len(ward_candidates), "party_result_count": len(parties)})

            for p in parties:
                if not isinstance(p, dict): continue
                ward_party_rows.append({
                    "event_date_iso": d, "electoral_event_id": eid, "province_id": prov,
                    "municipality_id": mun, "ward_id": ward,
                    "party_id": get_ci(p, "ID", "PartyID", "PartyId"),
                    "party_name": get_ci(p, "Name", "PartyName"),
                    "party_abbr": get_ci(p, "PartyAbbr", "Abbreviation"),
                    "ward_valid_votes": get_ci(p, "Ward_ValidVotes", "ValidVotes"),
                    "pr_valid_votes": get_ci(p, "PR_ValidVotes"),
                    "dc40_valid_votes": get_ci(p, "DC40Perc_ValidVotes"),
                    "total_valid_votes": get_ci(p, "TotalValidVotes"),
                    "percent_of_votes": get_ci(p, "PercOfVotes"),
                    **flatten_record("api_", p),
                })

            # Enumerate voting districts for this event/ward, then fetch fresh VD result objects.
            try:
                vdlist = api_get("/api/v1/Delimitation", {
                    "ElectoralEventID": eid, "ProvinceID": prov, "MunicipalityID": mun, "WardID": ward
                }, label=f"delim_vds_{eid}_{ward}")
            except requests.HTTPError:
                vdlist = []
            if not isinstance(vdlist, list): vdlist = []
            for vdrec in vdlist:
                if not isinstance(vdrec, dict): continue
                vd = as_int(get_ci(vdrec, "VDNumber", "VotingDistrict"))
                if vd is None: continue
                try:
                    vres = api_get("/api/v1/LGEBallotResults", {
                        "ElectoralEventID": eid, "ProvinceID": prov,
                        "MunicipalityID": mun, "VDNumber": vd
                    }, label=f"vd_result_{eid}_{vd}")
                except requests.HTTPError:
                    continue
                if not isinstance(vres, dict): continue
                vparties = get_ci(vres, "PartyBallotResults", default=[]) or []
                if not isinstance(vparties, list): vparties=[]
                vd_rows.append({
                    "event_date_iso": d, "electoral_event_id": eid,
                    "province_id": prov, "province": get_ci(vres, "Province"),
                    "municipality_id": mun, "municipality": get_ci(vres, "Municipality"),
                    "ward_id": get_ci(vres, "WardID", default=ward), "vd_number": vd,
                    "registered_voters": get_ci(vres, "RegisteredVoters"),
                    "spoilt_votes": get_ci(vres, "SpoiltVotes"),
                    "special_votes": get_ci(vres, "SpecialVotes"),
                    "voter_turnout_percent": get_ci(vres, "PercVoterTurnout"),
                    "total_votes_cast": get_ci(vres, "TotalVotesCast"),
                    "total_valid_votes": get_ci(vres, "TotalValidVotes"),
                    "results_complete": get_ci(vres, "bResultsComplete"),
                    "report_date": get_ci(vres, "ReportDate"),
                    **flatten_record("api_", {k:v for k,v in vres.items() if norm_key(k) != norm_key("PartyBallotResults")}),
                })
                for p in vparties:
                    if not isinstance(p, dict): continue
                    vd_party_rows.append({
                        "event_date_iso": d, "electoral_event_id": eid, "province_id": prov,
                        "municipality_id": mun, "ward_id": get_ci(vres, "WardID", default=ward), "vd_number": vd,
                        "party_id": get_ci(p, "ID", "PartyID", "PartyId"),
                        "party_name": get_ci(p, "Name", "PartyName"),
                        "party_abbr": get_ci(p, "PartyAbbr", "Abbreviation"),
                        "valid_votes": get_ci(p, "ValidVotes", "Ward_ValidVotes", "TotalValidVotes"),
                        "percent_of_votes": get_ci(p, "PercOfVotes"),
                        **flatten_record("api_", p),
                    })

    # Persist all fresh API-derived tables.
    write_csv(OUT / "byelection_events_api_validated.csv", event_rows,
              ["event_date_iso", "electoral_event_id_normalized", "event_type_id_normalized"])
    write_csv(OUT / "candidate_detail_api.csv", candidate_rows,
              ["event_date_iso", "electoral_event_id", "province_id", "municipality_id", "ward_id",
               "candidate_id", "party_id", "party_abbr", "party_name", "candidate_name"])
    write_csv(OUT / "ward_results_api.csv", ward_rows,
              ["event_date_iso", "electoral_event_id", "province_id", "province", "municipality_id", "municipality",
               "ward_id", "registered_voters", "total_votes_cast", "total_valid_votes", "spoilt_votes",
               "voter_turnout_percent", "winner_candidate_name", "winner_party_name", "winner_party_abbr",
               "winner_votes", "winner_percent"])
    write_csv(OUT / "ward_party_results_api.csv", ward_party_rows)
    write_csv(OUT / "vd_results_api.csv", vd_rows)
    write_csv(OUT / "vd_party_results_api.csv", vd_party_rows)
    write_csv(OUT / "api_reconciliation.csv", reconciliation_rows)
    write_csv(OUT / "api_extraction_audit.csv", audit,
              ["timestamp_utc", "label", "status_code", "ok", "attempt", "elapsed_s", "url", "error"])

    # Explicit completeness checks, including the ward that exposed the old scraper gap.
    ward_ids = {as_int(r.get("ward_id")) for r in ward_rows}
    checks = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "api_base": BASE,
        "start_date": START_DATE, "end_date": END_DATE,
        "selected_event_type_ids": selected_type_ids,
        "events_discovered": len(events),
        "events_with_candidates": event_with_candidates,
        "ward_event_rows": len(ward_rows),
        "candidate_rows": len(candidate_rows),
        "ward_party_rows": len(ward_party_rows),
        "vd_rows": len(vd_rows),
        "vd_party_rows": len(vd_party_rows),
        "contains_ward_52103001": 52103001 in ward_ids,
        "non_ok_reconciliation": sum(1 for r in reconciliation_rows if r.get("status") != "ok"),
        "failed_api_calls": sum(1 for r in audit if not r.get("ok")),
    }
    (OUT / "extraction_summary.json").write_text(json.dumps(checks, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(checks, indent=2), flush=True)

    # Ward-results failures are fatal: we want a conspicuous failure, never a silently incomplete master file.
    bad_wards = [r for r in reconciliation_rows if str(r.get("status", "")).startswith("ward_result_") and r.get("status") != "ok"]
    if bad_wards:
        print(f"ERROR: {len(bad_wards)} ward result requests failed. See api_reconciliation.csv", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    try:
        main()
    finally:
        # Always leave an audit file behind, even if discovery/authentication fails early.
        if audit:
            write_csv(OUT / "api_extraction_audit.csv", audit,
                      ["timestamp_utc", "label", "status_code", "ok", "attempt", "elapsed_s", "url", "error"])
