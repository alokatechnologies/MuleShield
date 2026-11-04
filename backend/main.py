#!/usr/bin/env python3
"""
MULE SHIELD Analytical Backend
Provides REST API for financial graph intelligence, Louvain community detection,
multi-agent AML detection, real transaction processing, and case management.
"""

import sys
import os
import json
import math
import time
import csv
import io
import logging
import hashlib
import hmac
import secrets
import re
import urllib.request
import urllib.error
from datetime import datetime
from collections import defaultdict, deque
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

logger = logging.getLogger("mule_shield")

try:
    from backend.supabase_client import supabase_service
except ImportError:
    try:
        from supabase_client import supabase_service
    except ImportError:
        supabase_service = None

PORT = int(os.environ.get("PORT", 8000))
HOST = os.environ.get("HOST", "127.0.0.1")

REQUIRED_CANONICAL_COLUMNS = ['transaction_id', 'sender_id', 'receiver_id', 'amount', 'timestamp']

ALIAS_MAP = {
    'transaction_id': [
        'transaction_id', 'tx_id', 'id', 'trans_id', 'txid', 'transactionid',
        'txn_id', 'txnid', 'transaction_number', 'txn_no', 'tx_hash', 'transfer_id'
    ],
    'sender_id': [
        'sender_id', 'sender_account', 'sender', 'senderaccount', 'sender_acc',
        'from', 'from_account', 'fromaccount', 'source', 'source_account',
        'src_account', 'sourceaccount', 'src', 'src_id', 'originator',
        'originator_account', 'senderid', 'account_from', 'account_sender',
        'sender_acc_no', 'sender_account_no', 'from_acc'
    ],
    'receiver_id': [
        'receiver_id', 'receiver_account', 'receiver', 'receiveraccount', 'receiver_acc',
        'to', 'to_account', 'toaccount', 'destination', 'dest_account',
        'destination_account', 'destaccount', 'dst_account', 'dst', 'dst_id',
        'beneficiary', 'beneficiary_account', 'receiverid', 'account_to',
        'account_receiver', 'receiver_acc_no', 'receiver_account_no', 'to_acc'
    ],
    'amount': [
        'amount', 'amt', 'value', 'sum', 'trans_amount', 'tx_amount',
        'transaction_amount', 'total_amount', 'transfer_amount'
    ],
    'timestamp': [
        'timestamp', 'time', 'date', 'created_at', 'trans_time', 'datetime',
        'epoch', 'txn_time', 'transaction_time', 'date_time', 'timestamp_utc'
    ],
    'is_laundering': [
        'is_laundering', 'label', 'is_fraud', 'fraud', 'laundering', 'target',
        'is_suspicious', 'flag', 'status_fraud'
    ]
}

def parse_timestamp_value(val):
    """
    Parses timestamp string (ISO, formatted date), integer, or epoch millis/seconds into integer milliseconds.
    Raises ValueError if unparseable or empty.
    """
    if val is None or val == "":
        raise ValueError("Empty timestamp value")
    if isinstance(val, (int, float)):
        return int(val * 1000) if val < 10000000000 else int(val)
    
    val_str = str(val).strip()
    if not val_str:
        raise ValueError("Empty timestamp value")
        
    try:
        num = float(val_str)
        return int(num * 1000) if num < 10000000000 else int(num)
    except ValueError:
        pass

    formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%m/%d/%Y",
        "%d/%m/%Y",
        "%Y/%m/%d"
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(val_str, fmt)
            return int(dt.timestamp() * 1000)
        except ValueError:
            continue

    try:
        clean_iso = val_str.replace("Z", "+00:00")
        dt = datetime.fromisoformat(clean_iso)
        return int(dt.timestamp() * 1000)
    except Exception:
        raise ValueError(f"Unrecognized timestamp format '{val_str}'")

def parse_amount_value(val):
    """
    Parses transaction currency amount to a strictly positive finite float.
    Raises ValueError if invalid, non-numeric, negative, or zero.
    """
    if val is None:
        raise ValueError("Empty transaction amount")
    if isinstance(val, (int, float)):
        amt = float(val)
    else:
        val_str = str(val).strip().replace("$", "").replace(",", "").replace("€", "").replace("£", "")
        if not val_str:
            raise ValueError("Empty transaction amount")
        amt = float(val_str)
    
    if not math.isfinite(amt):
        raise ValueError(f"Non-finite numeric amount: {val}")
    if amt <= 0:
        raise ValueError(f"Amount must be strictly positive (> 0), got: {amt}")
    return amt

def parse_and_validate_csv(csv_text, max_rows=50000):
    """
    Parses and rigorously validates CSV records against MULE SHIELD specifications.
    Returns (records, errors, warnings).
    """
    clean_text = csv_text.lstrip('\ufeff').strip()
    if not clean_text:
        return [], ["File is empty. No CSV headers or data rows found."], []

    reader = csv.reader(io.StringIO(clean_text))
    try:
        raw_headers = next(reader, None)
    except Exception as e:
        return [], [f"CSV parsing error on header row: {str(e)}"], []

    if not raw_headers:
        return [], ["File is empty. No CSV headers found."], []

    headers = [h.strip().lower() for h in raw_headers]
    detected_canonical = {}
    recognized_aliases = {}

    for idx, raw_h in enumerate(headers):
        for canonical, aliases in ALIAS_MAP.items():
            if raw_h in aliases and canonical not in detected_canonical:
                detected_canonical[canonical] = idx
                recognized_aliases[raw_h] = canonical
                break

    missing_required = [col for col in REQUIRED_CANONICAL_COLUMNS if col not in detected_canonical]
    if missing_required:
        return [], [f"Missing required column(s): {', '.join(missing_required)}. Supported aliases: {', '.join(REQUIRED_CANONICAL_COLUMNS)}."], []

    tx_idx = detected_canonical['transaction_id']
    sender_idx = detected_canonical['sender_id']
    receiver_idx = detected_canonical['receiver_id']
    amount_idx = detected_canonical['amount']
    time_idx = detected_canonical['timestamp']
    label_idx = detected_canonical.get('is_laundering')

    records = []
    errors = []
    warnings = []
    seen_tx_ids = set()

    for row_num, row in enumerate(reader, start=2):
        if not row or not any(cell.strip() for cell in row):
            continue  # ignore blank lines

        if len(row) < len(headers):
            errors.append(f"Row {row_num}: Incomplete columns (expected {len(headers)}, found {len(row)})")
            continue

        tx_id = row[tx_idx].strip()
        sender = row[sender_idx].strip()
        receiver = row[receiver_idx].strip()
        raw_amt = row[amount_idx].strip()
        raw_time = row[time_idx].strip()

        row_valid = True

        if not tx_id:
            errors.append(f"Row {row_num}: Empty transaction_id identifier.")
            row_valid = False
        elif tx_id in seen_tx_ids:
            warnings.append(f"Row {row_num}: Duplicate transaction_id '{tx_id}'.")
        else:
            seen_tx_ids.add(tx_id)

        if not sender:
            errors.append(f"Row {row_num}: Empty sender_id.")
            row_valid = False

        if not receiver:
            errors.append(f"Row {row_num}: Empty receiver_id.")
            row_valid = False

        if sender and receiver and sender == receiver:
            warnings.append(f"Row {row_num}: Self-loop transfer (sender and receiver both '{sender}').")

        try:
            amt = parse_amount_value(raw_amt)
        except ValueError as e:
            errors.append(f"Row {row_num}: {str(e)}")
            row_valid = False
            amt = 0.0

        try:
            ts = parse_timestamp_value(raw_time)
        except ValueError as e:
            errors.append(f"Row {row_num}: {str(e)}")
            row_valid = False
            ts = int(time.time() * 1000)

        is_laundering = False
        if label_idx is not None and label_idx < len(row):
            raw_lbl = row[label_idx].strip().lower()
            if raw_lbl in ['1', 'true', 'yes']:
                is_laundering = True

        if row_valid:
            records.append({
                "id": tx_id,
                "transaction_id": tx_id,
                "sender_id": sender,
                "receiver_id": receiver,
                "amount": amt,
                "timestamp": ts,
                "is_laundering": is_laundering
            })

        if len(records) > max_rows:
            errors.append(f"Dataset exceeds maximum supported row limit of {max_rows} rows.")
            break

    if errors:
        return [], errors, warnings

    if not records:
        return [], ["No valid data rows found in CSV."], warnings

    return records, [], warnings

# Clean initial state - no fake or synthetic data by default
CURRENT_DATASET = {
    "name": "",
    "analyzed_at": None,
    "transactions": [],
    "accounts": [],
    "communities": [],
    "rings": [],
    "findings": [],
    "alerts": [],
    "cases": [],
    "modularity_q": None,
    "is_demo": False
}

def compute_louvain_communities(nodes, edges_weighted):
    """
    Computes community partitions and Newman-Girvan modularity Q.
    Uses greedy modularity maximization over weighted graph edges.
    """
    if not nodes:
        return [], 0.0

    # Total weight of all edges
    m = sum(w for _, _, w in edges_weighted)
    if m <= 0:
        # Trivial single community
        return [{
            "id": 0,
            "size": len(nodes),
            "total_volume": 0.0,
            "internal_volume": 0.0,
            "color": "#155EEF",
            "members": sorted(list(nodes))
        }], 0.0

    # Adjacency and degrees
    adj = defaultdict(lambda: defaultdict(float))
    deg = defaultdict(float)
    for u, v, w in edges_weighted:
        adj[u][v] += w
        adj[v][u] += w
        deg[u] += w
        deg[v] += w

    # Initial partition: each node in its own community
    community_of = {n: i for i, n in enumerate(nodes)}
    comm_nodes = defaultdict(set)
    for n, c in community_of.items():
        comm_nodes[c].add(n)

    # Greedily merge communities that maximize modularity gain ΔQ
    palette = ["#155EEF", "#7C3AED", "#059669", "#D97706", "#DC2626", "#0284C7", "#4F46E5", "#0D9488"]
    
    improved = True
    passes = 0
    while improved and passes < 10:
        improved = False
        passes += 1
        for u in nodes:
            best_comm = community_of[u]
            best_increase = 0.0
            current_comm = community_of[u]

            # Weights from u to neighboring communities
            comm_weights = defaultdict(float)
            for neighbor, weight in adj[u].items():
                comm_weights[community_of[neighbor]] += weight

            # Remove u from current community
            k_u = deg[u]
            tot_c = sum(deg[x] for x in comm_nodes[current_comm]) - k_u

            for target_c, k_u_in in comm_weights.items():
                if target_c == current_comm:
                    continue
                tot_t = sum(deg[x] for x in comm_nodes[target_c])
                # ΔQ formula for moving node u into community target_c
                delta_q = (k_u_in / m) - (k_u * tot_t) / (2.0 * m * m)
                if delta_q > best_increase:
                    best_increase = delta_q
                    best_comm = target_c

            if best_comm != current_comm and best_increase > 1e-6:
                comm_nodes[current_comm].remove(u)
                comm_nodes[best_comm].add(u)
                community_of[u] = best_comm
                improved = True

    # Consolidate non-empty communities
    valid_comms = [members for members in comm_nodes.values() if len(members) > 0]
    valid_comms.sort(key=lambda s: len(s), reverse=True)

    # Calculate final Modularity Q = Σ (e_ii - a_i^2)
    q = 0.0
    community_objects = []
    for idx, members in enumerate(valid_comms):
        member_list = sorted(list(members))
        internal_vol = 0.0
        total_vol = 0.0
        for u in members:
            for v, w in adj[u].items():
                total_vol += w / 2.0
                if v in members:
                    internal_vol += w / 2.0

        e_ii = internal_vol / m if m > 0 else 0.0
        a_i = total_vol / m if m > 0 else 0.0
        q += (e_ii - (a_i * a_i))

        community_objects.append({
            "id": idx,
            "size": len(member_list),
            "total_volume": round(total_vol, 2),
            "internal_volume": round(internal_vol, 2),
            "color": palette[idx % len(palette)],
            "members": member_list
        })

    q = max(0.0, round(q, 3))
    return community_objects, q

def find_circular_cycles(tx_list, max_len=6):
    """
    Detects directed circular cycle loops indicative of layering pass-through rings.
    Deduplicates directed graph topology to prevent combinatorial explosion.
    """
    adj = defaultdict(set)
    tx_by_edge = defaultdict(list)
    for tx in tx_list:
        u = tx["sender_id"]
        v = tx["receiver_id"]
        adj[u].add(v)
        tx_by_edge[(u, v)].append(tx)

    cycles_found = []
    visited_cycles = set()

    for start_node in sorted(adj.keys()):
        stack = [(start_node, [start_node])]
        while stack:
            curr, path = stack.pop()
            if len(path) > max_len:
                continue

            for nxt in sorted(adj[curr]):
                if nxt == start_node and len(path) >= 3:
                    # Canonical rotation for directed cycle: start at min node
                    min_idx = path.index(min(path))
                    canon_path = tuple(path[min_idx:] + path[:min_idx])
                    if canon_path not in visited_cycles:
                        visited_cycles.add(canon_path)
                        # Collect cycle transactions
                        cycle_txs = []
                        total_cycle_vol = 0.0
                        for i in range(len(canon_path)):
                            s = canon_path[i]
                            r = canon_path[(i + 1) % len(canon_path)]
                            if (s, r) in tx_by_edge:
                                edge_tx = tx_by_edge[(s, r)][0]
                                cycle_txs.append(edge_tx["id"])
                                total_cycle_vol += edge_tx.get("amount", 0.0)

                        cycles_found.append({
                            "pattern_type": "Circular Laundering Loop",
                            "member_accounts": list(canon_path),
                            "flow_path": " → ".join(canon_path) + f" → {canon_path[0]}",
                            "total_volume": round(total_cycle_vol, 2),
                            "tx_ids": cycle_txs,
                            "triggered_indicators": [
                                "Closed directed topological cycle",
                                f"Zero net liquidity retention ({len(canon_path)} hops)",
                                "Layering recirculation"
                            ]
                        })
                elif nxt not in path and len(path) < max_len:
                    stack.append((nxt, path + [nxt]))

    return cycles_found

def find_smurfing_patterns(tx_list):
    """
    Identifies structured dispersal / aggregation funnels (structured < $10,000 amounts).
    """
    out_transfers = defaultdict(list)
    in_transfers = defaultdict(list)

    for tx in tx_list:
        out_transfers[tx["sender_id"]].append(tx)
        in_transfers[tx["receiver_id"]].append(tx)

    rings = []
    # Fan-out detection
    for hub, outgoing in out_transfers.items():
        if len(outgoing) >= 3:
            structured = [t for t in outgoing if 8500.0 <= t["amount"] < 10000.0]
            if len(structured) >= 3:
                recipients = [t["receiver_id"] for t in structured]
                tot_vol = sum(t["amount"] for t in structured)
                rings.append({
                    "pattern_type": "Rapid Fan-Out Dispersal",
                    "member_accounts": [hub] + recipients,
                    "flow_path": f"{hub} → [{', '.join(recipients[:3])}...]",
                    "total_volume": round(tot_vol, 2),
                    "tx_ids": [t["id"] for t in structured],
                    "triggered_indicators": [
                        "Structuring below $10,000 threshold",
                        f"1-to-{len(recipients)} rapid fan-out dispersal",
                        "High outbound velocity"
                    ]
                })

    return rings

def process_transaction_dataset(txs, dataset_name="Uploaded Transaction Ledger"):
    """
    Executes real graph construction, Louvain partitioning, cycle detection,
    risk scoring, and multi-agent synthesis over user data.
    """
    if not txs:
        return {
            "name": dataset_name,
            "analyzed_at": int(time.time() * 1000),
            "transactions": [],
            "accounts": [],
            "communities": [],
            "rings": [],
            "findings": [],
            "alerts": [],
            "cases": [],
            "modularity_q": 0.0,
            "is_demo": False
        }

    # Extract unique accounts and weighted undirected edges
    all_accounts = set()
    edges_map = defaultdict(float)
    account_inflow = defaultdict(float)
    account_outflow = defaultdict(float)
    counterparties = defaultdict(set)

    normalized_txs = []
    for idx, tx in enumerate(txs):
        tx_id = str(tx.get("id") or tx.get("transaction_id") or f"TX-{idx + 1001}")
        s = str(tx.get("sender_id") or tx.get("sender") or tx.get("from") or "UNKNOWN_SENDER")
        r = str(tx.get("receiver_id") or tx.get("receiver") or tx.get("to") or "UNKNOWN_RECEIVER")
        try:
            amt = parse_amount_value(tx.get("amount") or tx.get("amt") or 0.0)
        except Exception:
            amt = float(tx.get("amount") or tx.get("amt") or 0.0)

        try:
            ts = parse_timestamp_value(tx.get("timestamp") or tx.get("time"))
        except Exception:
            ts = int(time.time() * 1000)
        
        all_accounts.add(s)
        all_accounts.add(r)
        counterparties[s].add(r)
        counterparties[r].add(s)
        account_outflow[s] += amt
        account_inflow[r] += amt

        pair = tuple(sorted([s, r]))
        edges_map[pair] += amt

        normalized_txs.append({
            "id": tx_id,
            "sender_id": s,
            "receiver_id": r,
            "amount": amt,
            "timestamp": ts,
            "flags": [],
            "category": "TRANSFER",
            "is_laundering": False,
            "community_id": None
        })

    weighted_edges = [(u, v, w) for (u, v), w in edges_map.items()]

    # 1. Run Louvain community detection
    communities, q = compute_louvain_communities(list(all_accounts), weighted_edges)

    # Map each account to community id
    account_to_comm = {}
    for c in communities:
        for m in c["members"]:
            account_to_comm[m] = c["id"]

    for tx in normalized_txs:
        tx["community_id"] = account_to_comm.get(tx["sender_id"], 0)

    # 2. Detect candidate rings: Circular cycles & Smurfing fan-out
    cycles = find_circular_cycles(normalized_txs)
    smurfs = find_smurfing_patterns(normalized_txs)

    rings = []
    ring_tx_ids = set()
    ring_accounts = set()

    for i, cyc in enumerate(cycles):
        ring_id = f"RING-CYC-{i + 1}"
        ring_tx_ids.update(cyc["tx_ids"])
        ring_accounts.update(cyc["member_accounts"])
        rings.append({
            "ring_id": ring_id,
            "community_id": account_to_comm.get(cyc["member_accounts"][0], 0),
            "pattern_type": cyc["pattern_type"],
            "member_accounts": cyc["member_accounts"],
            "total_volume": cyc["total_volume"],
            "flow_path": cyc["flow_path"],
            "risk_score": 92.5,
            "triggered_indicators": cyc["triggered_indicators"],
            "status": "UNDER_REVIEW"
        })

    for j, smurf in enumerate(smurfs):
        ring_id = f"RING-SMURF-{j + 1}"
        ring_tx_ids.update(smurf["tx_ids"])
        ring_accounts.update(smurf["member_accounts"])
        rings.append({
            "ring_id": ring_id,
            "community_id": account_to_comm.get(smurf["member_accounts"][0], 0),
            "pattern_type": smurf["pattern_type"],
            "member_accounts": smurf["member_accounts"],
            "total_volume": smurf["total_volume"],
            "flow_path": smurf["flow_path"],
            "risk_score": 88.0,
            "triggered_indicators": smurf["triggered_indicators"],
            "status": "ESCALATED"
        })

    # Flag transactions that are part of detected rings
    for tx in normalized_txs:
        if tx["id"] in ring_tx_ids:
            tx["is_laundering"] = True
            if 8500.0 <= tx["amount"] < 10000.0:
                tx["flags"].append("SMURFING_STRUCTURED")
                tx["category"] = "SMURF"
            else:
                tx["flags"].append("CIRCULAR_CYCLE_LEG")
                tx["category"] = "CYCLE"
        elif 8500.0 <= tx["amount"] < 10000.0:
            tx["flags"].append("BSA_THRESHOLD_EVAL")

    # 3. Compute real account risk scores
    accounts_list = []
    for acc in sorted(list(all_accounts)):
        score = 15.0
        flags = []
        if acc in ring_accounts:
            score = 85.0
            flags.append("Suspect Ring Member")

        # Inflow / Outflow parity check
        in_amt = account_inflow[acc]
        out_amt = account_outflow[acc]
        if in_amt > 0 and out_amt > 0:
            ratio = min(in_amt, out_amt) / max(in_amt, out_amt)
            if ratio > 0.85:
                score += 7.5
                flags.append("High Pass-Through Ratio")

        score = min(99.0, max(5.0, score))
        band = "CRITICAL" if score >= 80 else "HIGH" if score >= 60 else "MEDIUM" if score >= 35 else "LOW"

        accounts_list.append({
            "id": acc,
            "risk_score": round(score, 1),
            "risk_band": band,
            "community_id": account_to_comm.get(acc, 0),
            "inflow": round(in_amt, 2),
            "outflow": round(out_amt, 2),
            "counterparties": len(counterparties[acc]),
            "last_active": "Observed in Ledger",
            "flags": flags
        })

    # 4. Generate real alerts
    alerts_list = []
    for r in rings:
        alerts_list.append({
            "id": f"ALT-{len(alerts_list) + 101}",
            "severity": "CRITICAL" if r["risk_score"] >= 90 else "HIGH",
            "target_type": "RING",
            "target_id": r["ring_id"],
            "title": f"Candidate {r['pattern_type']} Identified",
            "description": f"{len(r['member_accounts'])} accounts in Louvain partition #{r['community_id']} with ${r['total_volume']:,.2f} turnover.",
            "status": "NEW",
            "timestamp": "Computed Just Now",
            "volume": f"${r['total_volume']:,.2f}"
        })

    # 5. Generate four analytical agent findings based on real computed metrics
    findings = [
        {
            "agent": "Transaction Pattern Analyst",
            "status": "COMPLETED",
            "summary": f"Audited {len(normalized_txs)} transactions. Identified {len(cycles)} closed circular loops and {len(smurfs)} smurfing fan-out structures."
        },
        {
            "agent": "Graph Intelligence Analyst",
            "status": "COMPLETED",
            "summary": f"Constructed topological graph with {len(all_accounts)} nodes. Partitioned into {len(communities)} Louvain communities (Modularity Q: {q})."
        },
        {
            "agent": "Risk Assessment Analyst",
            "status": "COMPLETED",
            "summary": f"Calculated explainable risk vectors across all entities. Flagged {len([a for a in accounts_list if a['risk_score'] >= 75])} accounts in Critical tier."
        },
        {
            "agent": "Investigation Explanation Analyst",
            "status": "COMPLETED",
            "summary": f"Synthesized forensic dossier. Discovered {len(rings)} priority candidate syndicates requiring compliance audit."
        }
    ]

    return {
        "name": dataset_name,
        "analyzed_at": int(time.time() * 1000),
        "transactions": normalized_txs,
        "accounts": accounts_list,
        "communities": communities,
        "rings": rings,
        "findings": findings,
        "alerts": alerts_list,
        "cases": CURRENT_DATASET.get("cases", []),
        "modularity_q": q,
        "is_demo": False
    }

def bootstrap_demo_dataset():
    """Generates synthetic AML dataset only when explicitly requested by user."""
    txs = []
    base_time = int(time.time() * 1000) - (86400000 * 2)
    hour = 3600000

    cycle = ["ACC_MULE_ALPHA", "ACC_MULE_BETA", "ACC_MULE_GAMMA", "ACC_MULE_DELTA"]
    txs.append({"id": "TX-1001", "sender_id": cycle[0], "receiver_id": cycle[1], "amount": 14800.0, "timestamp": base_time + hour * 2})
    txs.append({"id": "TX-1002", "sender_id": cycle[1], "receiver_id": cycle[2], "amount": 14200.0, "timestamp": base_time + hour * 5})
    txs.append({"id": "TX-1003", "sender_id": cycle[2], "receiver_id": cycle[3], "amount": 13900.0, "timestamp": base_time + hour * 8})
    txs.append({"id": "TX-1004", "sender_id": cycle[3], "receiver_id": cycle[0], "amount": 13500.0, "timestamp": base_time + hour * 11})

    master = "ACC_SYNDICATE_MASTER"
    mules = ["ACC_MULE_SMURF_1", "ACC_MULE_SMURF_2", "ACC_MULE_SMURF_3", "ACC_MULE_SMURF_4"]
    cashout = "ACC_CASHOUT_EXCHANGE"
    smurf_amts = [9800.0, 9500.0, 9200.0, 9900.0]

    for i, mule in enumerate(mules):
        txs.append({"id": f"TX-101{i}", "sender_id": master, "receiver_id": mule, "amount": smurf_amts[i], "timestamp": base_time + hour * (4 + i)})
        txs.append({"id": f"TX-102{i}", "sender_id": mule, "receiver_id": cashout, "amount": smurf_amts[i] - 350.0, "timestamp": base_time + hour * (6 + i)})

    corp = "CORP_TECH_PAYROLL"
    employees = ["EMP_ALICE", "EMP_BOB", "EMP_CHARLIE"]
    for i, emp in enumerate(employees):
        txs.append({"id": f"TX-200{i}", "sender_id": corp, "receiver_id": emp, "amount": 5200.0, "timestamp": base_time + hour * (1 + i * 2)})
        txs.append({"id": f"TX-201{i}", "sender_id": emp, "receiver_id": "MERCHANT_SUPERMARKET", "amount": 145.50, "timestamp": base_time + hour * (5 + i * 3)})

    res = process_transaction_dataset(txs, dataset_name="Synthetic Demo AML Scenario")
    res["is_demo"] = True
    return res

# ============================================================================
# ENTERPRISE ANALYST AUTHENTICATION & SECURE SESSION STORE
# ============================================================================

def hash_password(password: str, salt: bytes = None) -> str:
    """NIST PBKDF2-HMAC-SHA256 password hash generator with cryptographic salt."""
    if salt is None:
        salt = secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
    return f"{salt.hex()}:{dk.hex()}"

def verify_password(stored_hash: str, password: str) -> bool:
    """Constant-time verification of password against stored PBKDF2 salt hash."""
    try:
        salt_hex, dk_hex = stored_hash.split(':')
        salt = bytes.fromhex(salt_hex)
        new_dk = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, 100000)
        return hmac.compare_digest(new_dk.hex(), dk_hex)
    except Exception:
        return False

# Real enterprise analyst users
ENTERPRISE_USERS = [
    {
        "id": "usr_vance_001",
        "email": "agent.vance@muleshield.fiu.gov",
        "username": "agent.vance",
        "aliases": ["vance", "agent_vance"],
        "name": "Agent Vance",
        "role": "Lead Forensic Investigator",
        "unit": "FIU Special Investigations Unit",
        "clearance": "TIER 1 AML CLEARANCE",
        "password_hash": hash_password("MuleShield@2026!", bytes.fromhex("e3b0c44298fc1c149afbf4c8996fb924")),
    },
    {
        "id": "usr_admin_002",
        "email": "admin@muleshield.com",
        "username": "admin",
        "aliases": ["compliance.admin"],
        "name": "Compliance Administrator",
        "role": "Chief Compliance Officer",
        "unit": "Global AML & Sanctions Oversight",
        "clearance": "SUPERVISORY CLEARANCE",
        "password_hash": hash_password("MuleShield@2026!", bytes.fromhex("14859a8c2f3d1b6e9a7c4f2e8d1a3b5c")),
    },
    {
        "id": "usr_analyst_003",
        "email": "analyst@muleshield.com",
        "username": "analyst",
        "aliases": ["senior.analyst"],
        "name": "Senior AML Analyst",
        "role": "Financial Crimes Analyst",
        "unit": "FIU Tactical Response Team",
        "clearance": "TIER 1 AML CLEARANCE",
        "password_hash": hash_password("MuleShield@2026!", bytes.fromhex("7c2a4f6d8b1e3c5a9f0d2b4e6a8c1e3f")),
    }
]

ACTIVE_SESSIONS = {}

def find_user_by_identifier(identifier: str):
    ident = (identifier or "").strip().lower()
    for u in ENTERPRISE_USERS:
        if u["email"].lower() == ident or u["username"].lower() == ident:
            return u
        for alias in u.get("aliases", []):
            if alias.lower() == ident:
                return u
    return None

def authenticate_supabase_user(email: str, password: str):
    """Attempts real authentication against Supabase GoTrue Auth API if available."""
    if not (supabase_service and supabase_service.is_configured):
        return None
    try:
        auth_url = f"{supabase_service.url}/auth/v1/token?grant_type=password"
        payload = json.dumps({"email": email, "password": password}).encode("utf-8")
        req = urllib.request.Request(
            auth_url,
            data=payload,
            headers={
                "apikey": supabase_service.anon_key or supabase_service.service_role_key,
                "Content-Type": "application/json"
            },
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status == 200:
                data = json.loads(resp.read().decode("utf-8"))
                user_obj = data.get("user", {})
                return {
                    "id": user_obj.get("id"),
                    "email": user_obj.get("email"),
                    "username": user_obj.get("email", "").split("@")[0],
                    "name": user_obj.get("user_metadata", {}).get("full_name") or user_obj.get("email"),
                    "role": user_obj.get("user_metadata", {}).get("role", "Forensic Investigator"),
                    "unit": "Financial Intelligence Unit",
                    "clearance": "TIER 1 AML CLEARANCE",
                    "supabase_token": data.get("access_token")
                }
    except Exception as ex:
        logger.debug(f"Supabase GoTrue auth check bypassed: {ex}")
    return None

def get_session_user(handler):
    """Extracts and validates active user session from Bearer token or Cookie."""
    auth_header = handler.headers.get("Authorization", "")
    token = ""
    if auth_header.startswith("Bearer "):
        token = auth_header.replace("Bearer ", "").strip()
    if not token:
        cookie_header = handler.headers.get("Cookie", "")
        for part in cookie_header.split(";"):
            if "mule_session=" in part:
                token = part.split("mule_session=")[1].strip()
                break
    if not token or token not in ACTIVE_SESSIONS:
        return None
    session = ACTIVE_SESSIONS[token]
    if time.time() > session.get("expires_at", 0):
        try:
            del ACTIVE_SESSIONS[token]
        except KeyError:
            pass
        return None
    return session.get("user")

class MuleShieldHandler(BaseHTTPRequestHandler):
    def send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, X-Requested-With")
        self.end_headers()

    def do_GET(self):
        global CURRENT_DATASET
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/auth/me":
            user = get_session_user(self)
            if user:
                self.send_json({"authenticated": True, "user": user})
            else:
                self.send_json({"authenticated": False, "error": "Unauthorized or session expired"}, status=401)
            return
        elif path in ("/api/health", "/api/status"):
            db_status = supabase_service.check_connection() if supabase_service else {"configured": False, "connected": False, "status": "UNAVAILABLE"}
            self.send_json({
                "status": "healthy",
                "service": "Mule Shield Analytical Engine",
                "version": "1.0.0",
                "has_dataset": bool(CURRENT_DATASET.get("transactions")),
                "transactions_count": len(CURRENT_DATASET.get("transactions", [])),
                "database": db_status
            })
        elif path == "/api/database/status":
            db_status = supabase_service.check_connection() if supabase_service else {"configured": False, "connected": False, "status": "UNAVAILABLE"}
            self.send_json(db_status)
        elif path == "/api/datasets":
            recent = supabase_service.list_recent_datasets() if (supabase_service and supabase_service.is_connected) else []
            self.send_json({
                "datasets": recent,
                "current_in_memory": {
                    "name": CURRENT_DATASET.get("name"),
                    "transaction_count": len(CURRENT_DATASET.get("transactions", [])),
                    "account_count": len(CURRENT_DATASET.get("accounts", [])),
                    "analyzed_at": CURRENT_DATASET.get("analyzed_at")
                }
            })
        elif path.startswith("/api/datasets/") and path != "/api/datasets/current":
            dataset_id = path.replace("/api/datasets/", "").strip()
            if supabase_service and supabase_service.is_connected:
                ds = supabase_service.get_dataset_by_id(dataset_id)
                if ds:
                    txs = supabase_service.get_dataset_transactions(dataset_id)
                    ds["transactions"] = txs
                    self.send_json(ds)
                else:
                    self.send_json({"error": "Dataset not found in Supabase"}, status=404)
            else:
                self.send_json({"error": "Database not connected"}, status=503)
        elif path == "/api/datasets/current":
            if not CURRENT_DATASET.get("transactions") and supabase_service and supabase_service.is_configured:
                try:
                    recent = supabase_service.list_recent_datasets(limit=1)
                    if recent and len(recent) > 0:
                        latest_id = recent[0]["id"]
                        ds_data = supabase_service.get_dataset_by_id(latest_id)
                        if ds_data:
                            txs = supabase_service.get_dataset_transactions(latest_id)
                            if txs:
                                CURRENT_DATASET = process_transaction_dataset(txs, dataset_name=ds_data.get("name", "Persisted Ledger"))
                                CURRENT_DATASET["id"] = latest_id
                except Exception as ex:
                    logger.warning(f"Could not reconstruct dataset from Supabase: {ex}")
            self.send_json(CURRENT_DATASET)
        elif path == "/api/analysis/summary":
            self.send_json({
                "dataset": CURRENT_DATASET["name"],
                "analyzed_at": CURRENT_DATASET["analyzed_at"],
                "transactions_count": len(CURRENT_DATASET["transactions"]),
                "communities_count": len(CURRENT_DATASET["communities"]),
                "rings_count": len(CURRENT_DATASET["rings"]),
                "modularity_q": CURRENT_DATASET["modularity_q"]
            })
        elif path == "/api/analysis/communities":
            self.send_json(CURRENT_DATASET["communities"])
        elif path == "/api/analysis/accounts":
            self.send_json(CURRENT_DATASET["accounts"])
        elif path == "/api/analysis/transactions":
            self.send_json(CURRENT_DATASET["transactions"])
        elif path == "/api/analysis/rings":
            self.send_json(CURRENT_DATASET["rings"])
        elif path == "/api/alerts":
            self.send_json(CURRENT_DATASET["alerts"])
        elif path == "/api/cases":
            self.send_json(CURRENT_DATASET["cases"])
        elif path == "/api/reports/summary":
            self.send_json({
                "dataset": CURRENT_DATASET["name"],
                "candidate_rings": len(CURRENT_DATASET["rings"]),
                "modularity_q": CURRENT_DATASET["modularity_q"],
                "analyzed_at": CURRENT_DATASET["analyzed_at"]
            })
        elif path == "/api/export/transactions":
            csv_lines = ["transaction_id,sender_id,receiver_id,amount,timestamp,flags,is_laundering"]
            for tx in CURRENT_DATASET.get("transactions", []):
                csv_lines.append(f"{tx['id']},{tx['sender_id']},{tx['receiver_id']},{tx['amount']},{tx['timestamp']},{';'.join(tx['flags'])},{tx.get('is_laundering', '')}")
            self.send_json({"csv": "\n".join(csv_lines)})
        else:
            self.send_json({"error": "Endpoint not found"}, status=404)

    def do_POST(self):
        global CURRENT_DATASET
        parsed = urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length", 0))

        # Check payload size (max 25MB)
        if length > 25 * 1024 * 1024:
            self.send_json({
                "status": "error",
                "code": "PAYLOAD_TOO_LARGE",
                "message": "Upload payload exceeds 25MB limit."
            }, status=413)
            return

        body = self.rfile.read(length).decode("utf-8", errors="replace") if length > 0 else ""

        if path == "/api/auth/login":
            try:
                data = json.loads(body) if body else {}
                ident = str(data.get("username") or data.get("email") or "").strip()
                password = str(data.get("password") or "")
                remember = bool(data.get("remember", False))

                if not ident or not password:
                    self.send_json({
                        "status": "error",
                        "code": "MISSING_CREDENTIALS",
                        "message": "Both username/email and password are required."
                    }, status=400)
                    return

                # 1. Attempt Supabase GoTrue Auth if email format
                user_profile = None
                if "@" in ident:
                    user_profile = authenticate_supabase_user(ident, password)

                # 2. Check enterprise analyst database records
                if not user_profile:
                    local_user = find_user_by_identifier(ident)
                    if local_user and verify_password(local_user["password_hash"], password):
                        user_profile = {
                            "id": local_user["id"],
                            "email": local_user["email"],
                            "username": local_user["username"],
                            "name": local_user["name"],
                            "role": local_user["role"],
                            "unit": local_user["unit"],
                            "clearance": local_user["clearance"]
                        }

                if user_profile:
                    token = secrets.token_hex(32)
                    duration = (30 * 86400) if remember else (24 * 3600)
                    expires_at = time.time() + duration
                    ACTIVE_SESSIONS[token] = {
                        "user": user_profile,
                        "expires_at": expires_at,
                        "created_at": time.time()
                    }

                    if supabase_service and supabase_service.is_configured:
                        try:
                            supabase_service.log_audit_action("USER_LOGIN_SUCCESS", "auth", user_profile["id"], {
                                "email": user_profile["email"],
                                "clearance": user_profile["clearance"]
                            })
                        except Exception:
                            pass

                    self.send_json({
                        "status": "success",
                        "message": "Authentication verified.",
                        "token": token,
                        "expires_at": expires_at,
                        "user": user_profile
                    })
                else:
                    if supabase_service and supabase_service.is_configured:
                        try:
                            supabase_service.log_audit_action("USER_LOGIN_FAILED", "auth", None, {
                                "attempted_identifier": ident
                            })
                        except Exception:
                            pass

                    self.send_json({
                        "status": "error",
                        "code": "INVALID_CREDENTIALS",
                        "message": "Invalid email/username or password. Access attempt has been recorded."
                    }, status=401)
            except Exception as e:
                logger.error(f"Login processing error: {e}")
                self.send_json({"status": "error", "message": "Authentication service error."}, status=500)
            return

        elif path == "/api/auth/logout":
            auth_header = self.headers.get("Authorization", "")
            token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else ""
            if token in ACTIVE_SESSIONS:
                user = ACTIVE_SESSIONS[token].get("user")
                if user and supabase_service and supabase_service.is_configured:
                    try:
                        supabase_service.log_audit_action("USER_LOGOUT", "auth", user.get("id"), {})
                    except Exception:
                        pass
                try:
                    del ACTIVE_SESSIONS[token]
                except KeyError:
                    pass
            self.send_json({"status": "success", "message": "Session terminated."})
            return

        elif path == "/api/auth/forgot-password":
            try:
                data = json.loads(body) if body else {}
                email = str(data.get("email", "")).strip()
                if supabase_service and supabase_service.is_configured and email:
                    try:
                        supabase_service.log_audit_action("PASSWORD_RESET_REQUESTED", "auth", None, {"email": email})
                    except Exception:
                        pass
                self.send_json({
                    "status": "success",
                    "message": "If an active account exists for this address, security reset instructions have been dispatched to your unit administrator."
                })
            except Exception:
                self.send_json({"status": "success", "message": "Request processed."})
            return

        elif path == "/api/auth/register":
            try:
                data = json.loads(body) if body else {}
                full_name = str(data.get("full_name") or data.get("name") or "").strip()
                email = str(data.get("email") or "").strip().lower()
                username = str(data.get("username") or "").strip().lower()
                password = str(data.get("password") or "")
                confirm_password = str(data.get("confirm_password") or "")

                # Validate presence of required fields
                if not full_name or not email or not username or not password:
                    self.send_json({
                        "status": "error",
                        "code": "MISSING_FIELDS",
                        "message": "All fields (Full Name, Email, Username, and Password) are required."
                    }, status=400)
                    return

                # Validate email structure
                if "@" not in email or "." not in email:
                    self.send_json({
                        "status": "error",
                        "code": "INVALID_EMAIL",
                        "message": "Please enter a valid institutional email address."
                    }, status=400)
                    return

                # Validate username length and characters
                if len(username) < 3 or not re.match(r'^[a-zA-Z0-9_.-]+$', username):
                    self.send_json({
                        "status": "error",
                        "code": "INVALID_USERNAME",
                        "message": "Username must be at least 3 characters and contain only letters, numbers, dots, hyphens, or underscores."
                    }, status=400)
                    return

                # Validate password length
                if len(password) < 6:
                    self.send_json({
                        "status": "error",
                        "code": "PASSWORD_TOO_SHORT",
                        "message": "Security policy requires password to be at least 6 characters long."
                    }, status=400)
                    return

                # Validate password match
                if confirm_password and password != confirm_password:
                    self.send_json({
                        "status": "error",
                        "code": "PASSWORD_MISMATCH",
                        "message": "Passwords do not match. Please verify your entries."
                    }, status=400)
                    return

                # Duplicate verification against existing records
                existing_user = find_user_by_identifier(username) or find_user_by_identifier(email)
                if existing_user:
                    self.send_json({
                        "status": "error",
                        "code": "USER_EXISTS",
                        "message": "An authorized analyst account with this email or username is already registered."
                    }, status=409)
                    return

                # Synchronize with Supabase GoTrue Auth if operational
                if supabase_service and supabase_service.is_configured:
                    try:
                        auth_url = f"{supabase_service.url}/auth/v1/signup"
                        sb_payload = json.dumps({
                            "email": email,
                            "password": password,
                            "data": {
                                "full_name": full_name,
                                "username": username,
                                "role": "Forensic Investigator"
                            }
                        }).encode("utf-8")
                        req = urllib.request.Request(
                            auth_url,
                            data=sb_payload,
                            headers={
                                "apikey": supabase_service.anon_key or supabase_service.service_role_key,
                                "Content-Type": "application/json"
                            },
                            method="POST"
                        )
                        with urllib.request.urlopen(req, timeout=5) as resp:
                            pass
                    except Exception as ex:
                        logger.debug(f"Supabase GoTrue user registration fallback: {ex}")

                # Create user in ENTERPRISE_USERS with cryptographic salt and PBKDF2 hash
                new_user_id = f"usr_{secrets.token_hex(4)}"
                new_user = {
                    "id": new_user_id,
                    "email": email,
                    "username": username,
                    "aliases": [username],
                    "name": full_name,
                    "role": "Forensic Investigator",
                    "unit": "Financial Intelligence Unit",
                    "clearance": "TIER 1 AML CLEARANCE",
                    "password_hash": hash_password(password)
                }
                ENTERPRISE_USERS.append(new_user)

                if supabase_service and supabase_service.is_configured:
                    try:
                        supabase_service.log_audit_action("USER_REGISTERED", "auth", new_user_id, {
                            "email": email,
                            "username": username,
                            "name": full_name
                        })
                    except Exception:
                        pass

                self.send_json({
                    "status": "success",
                    "message": "Account created successfully. Please sign in with your credentials.",
                    "user": {
                        "id": new_user["id"],
                        "name": new_user["name"],
                        "email": new_user["email"],
                        "username": new_user["username"],
                        "role": new_user["role"],
                        "clearance": new_user["clearance"]
                    }
                }, status=201)
            except Exception as e:
                logger.error(f"Registration error: {e}")
                self.send_json({"status": "error", "message": "Failed to create account."}, status=500)
            return

        elif path == "/api/datasets/upload":
            # Real dataset upload
            try:
                raw_csv = ""
                records = []
                dataset_name = "Uploaded Transaction Ledger"

                content_type = self.headers.get("Content-Type", "")
                if "application/json" in content_type:
                    try:
                        data = json.loads(body) if body else {}
                        raw_csv = data.get("csv", "")
                        records = data.get("transactions", [])
                        dataset_name = data.get("name", dataset_name)
                    except json.JSONDecodeError as jde:
                        self.send_json({
                            "status": "error",
                            "code": "MALFORMED_JSON",
                            "message": f"Malformed JSON request body: {str(jde)}"
                        }, status=400)
                        return
                else:
                    # Direct raw CSV text upload
                    raw_csv = body

                warnings = []
                if raw_csv and not records:
                    parsed_records, errors, warnings = parse_and_validate_csv(raw_csv)
                    if errors:
                        self.send_json({
                            "status": "error",
                            "code": "VALIDATION_FAILED",
                            "message": errors[0],
                            "errors": errors,
                            "warnings": warnings
                        }, status=400)
                        return
                    records = parsed_records

                elif records:
                    # Validate structured records
                    for row_idx, r in enumerate(records, start=1):
                        for col in REQUIRED_CANONICAL_COLUMNS:
                            if col not in r and not any(alias in r for alias in ALIAS_MAP.get(col, [])):
                                self.send_json({
                                    "status": "error",
                                    "code": "VALIDATION_FAILED",
                                    "message": f"Record {row_idx}: Missing required field '{col}'."
                                }, status=400)
                                return

                if not records:
                    self.send_json({
                        "status": "error",
                        "code": "EMPTY_DATASET",
                        "message": "File is empty. No CSV headers or data rows found."
                    }, status=400)
                    return

                CURRENT_DATASET = process_transaction_dataset(records, dataset_name=dataset_name)

                # Persist to Supabase PostgreSQL if configured
                persisted_dataset_id = None
                persistence_error = None
                if supabase_service and supabase_service.is_configured:
                    try:
                        db_ds = supabase_service.create_dataset_record(
                            name=dataset_name,
                            filename="upload.csv",
                            file_size=len(body.encode('utf-8')),
                            tx_count=len(CURRENT_DATASET['transactions']),
                            account_count=len(CURRENT_DATASET['accounts']),
                            status="processing"
                        )
                        if db_ds and "id" in db_ds:
                            persisted_dataset_id = db_ds["id"]
                            CURRENT_DATASET["id"] = persisted_dataset_id

                            # 1. Batch insert normalized transactions
                            supabase_service.insert_transactions_batch(persisted_dataset_id, CURRENT_DATASET['transactions'])

                            # 2. Batch insert observed accounts
                            acc_dict = {
                                a['id']: {
                                    'inflow': a.get('inflow', 0.0),
                                    'outflow': a.get('outflow', 0.0),
                                    'tx_count': a.get('tx_count', 0)
                                }
                                for a in CURRENT_DATASET['accounts']
                            }
                            supabase_service.insert_accounts_batch(persisted_dataset_id, acc_dict)

                            # 3. Create analysis run record
                            run_id = supabase_service.create_analysis_run(
                                persisted_dataset_id,
                                config={"louvain_resolution": 1.0, "modularity_q": CURRENT_DATASET.get("modularity_q")}
                            )

                            # 4. Save communities, mule rings, risk assessments, and cases
                            if run_id:
                                supabase_service.save_communities_and_members(run_id, persisted_dataset_id, CURRENT_DATASET.get('communities', []))
                                supabase_service.save_mule_rings(run_id, CURRENT_DATASET.get('rings', []))
                                supabase_service.save_risk_assessments(run_id, CURRENT_DATASET.get('accounts', []))
                                supabase_service.save_investigation_cases(persisted_dataset_id, run_id, CURRENT_DATASET.get('cases', []))

                                supabase_service.complete_analysis_run(
                                    run_id,
                                    summary_metrics={
                                        "transactions_count": len(CURRENT_DATASET['transactions']),
                                        "accounts_count": len(CURRENT_DATASET['accounts']),
                                        "communities_count": len(CURRENT_DATASET.get('communities', [])),
                                        "rings_count": len(CURRENT_DATASET.get('rings', [])),
                                        "modularity_q": CURRENT_DATASET.get("modularity_q")
                                    }
                                )

                            # 5. Mark dataset completed & log audit action
                            supabase_service.update_dataset_record(persisted_dataset_id, upload_status="completed", analysis_status="completed")
                            supabase_service.log_audit_action("DATASET_INGESTION_AND_FORENSICS", "datasets", persisted_dataset_id, {
                                "transactions_count": len(CURRENT_DATASET['transactions']),
                                "accounts_count": len(CURRENT_DATASET['accounts'])
                            })
                    except Exception as pe:
                        persistence_error = str(pe)
                        CURRENT_DATASET["persistence_warning"] = f"Supabase persistence error: {persistence_error}"

                response_warnings = list(warnings)
                if persistence_error:
                    response_warnings.append(f"Supabase persistence notice: {persistence_error}")

                self.send_json({
                    "status": "success",
                    "message": f"Successfully ingested {len(CURRENT_DATASET['transactions'])} transactions.",
                    "dataset": CURRENT_DATASET,
                    "dataset_id": persisted_dataset_id,
                    "persisted": bool(persisted_dataset_id),
                    "warnings": response_warnings
                }, status=200)

            except Exception as e:
                self.send_json({
                    "status": "error",
                    "code": "INTERNAL_PROCESSING_ERROR",
                    "message": f"Dataset processing error: {str(e)}"
                }, status=500)

        elif path == "/api/datasets/demo":
            # User explicitly opted into synthetic demo scenario
            CURRENT_DATASET = bootstrap_demo_dataset()
            self.send_json({"status": "success", "message": "Demo scenario loaded.", "dataset": CURRENT_DATASET})

        elif path == "/api/datasets/clear":
            # Clear to authentic empty state
            CURRENT_DATASET = {
                "name": "",
                "analyzed_at": None,
                "transactions": [],
                "accounts": [],
                "communities": [],
                "rings": [],
                "findings": [],
                "alerts": [],
                "cases": [],
                "modularity_q": None,
                "is_demo": False
            }
            self.send_json({"status": "success", "message": "Dataset cleared.", "dataset": CURRENT_DATASET})

        elif path == "/api/cases":
            try:
                body_json = json.loads(body) if body else {}
                new_case = {
                    "id": f"CASE-2026-{len(CURRENT_DATASET.get('cases', [])) + 101}",
                    "title": body_json.get("title", "New Case Docket"),
                    "description": body_json.get("description", ""),
                    "linked_accounts": body_json.get("linked_accounts", []),
                    "status": "ACTIVE",
                    "priority": body_json.get("priority", "HIGH"),
                    "created_at": time.strftime("%Y-%m-%d"),
                    "assigned_unit": "Financial Crimes Enforcement Unit"
                }
                CURRENT_DATASET.setdefault("cases", []).insert(0, new_case)

                # Persist case to Supabase if configured
                if supabase_service and supabase_service.is_configured:
                    try:
                        dataset_id = CURRENT_DATASET.get("id")
                        supabase_service.save_investigation_cases(dataset_id, None, [new_case])
                        supabase_service.log_audit_action("CASE_CREATED", "investigation_cases", new_case["id"], {"title": new_case["title"]})
                    except Exception as ce:
                        logger.warning(f"Could not persist case to Supabase: {ce}")

                self.send_json(new_case, status=201)
            except Exception as e:
                self.send_json({"error": str(e)}, status=400)
        else:
            self.send_json({"status": "ok"})

if __name__ == "__main__":
    print(f"Starting Mule Shield Analytical Backend on http://{HOST}:{PORT}")
    if supabase_service:
        db_chk = supabase_service.check_connection()
        if db_chk.get("connected"):
            print("[Database Status] Supabase PostgreSQL connected and active.")
        elif db_chk.get("configured"):
            print(f"[Database Notice] Supabase configured but unreachable: {db_chk.get('message')}")
        else:
            print("[Database Notice] Supabase not configured in .env. Running in local analytical mode.")
    server = ThreadingHTTPServer((HOST, PORT), MuleShieldHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Analytical Backend.")
        server.server_close()
