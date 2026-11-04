"""
MULE SHIELD Supabase Client & PostgreSQL Integration Service
Provides production-grade, zero-dependency REST integration with Supabase PostgREST & Storage APIs.
Adheres to strict financial security: service role kept on backend, zero secret logging,
validated batch inserts, and seamless offline/in-memory fallback when unconfigured.
"""

import os
import json
import logging
import urllib.request
import urllib.error
from urllib.parse import urlparse
from datetime import datetime, timezone

logger = logging.getLogger("mule_shield.supabase")

def load_env_file():
    """Lightweight .env loader with zero third-party dependencies."""
    candidate_paths = [
        os.path.join(os.getcwd(), ".env"),
        os.path.join(os.path.dirname(__file__), "..", ".env"),
        os.path.join(os.path.dirname(__file__), ".env")
    ]
    for env_path in candidate_paths:
        if os.path.isfile(env_path):
            try:
                with open(env_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if "=" in line:
                            k, v = line.split("=", 1)
                            k = k.strip()
                            v = v.strip().strip("'").strip('"')
                            if k:
                                os.environ[k] = v
                break
            except Exception as e:
                logger.warning(f"Failed to read .env file at {env_path}: {e}")

# Attempt to load .env on module import
load_env_file()


class SupabaseService:
    """
    Direct HTTPS PostgREST and Storage API client for Supabase.
    """

    def __init__(self):
        self.url = None
        self.service_role_key = None
        self.anon_key = None
        self._validate_configuration()

    def _validate_configuration(self):
        """Validates configuration presence without logging secrets."""
        if self.url is None:
            load_env_file()
            self.url = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
        if self.service_role_key is None:
            self.service_role_key = (
                os.environ.get("SUPABASE_SERVICE_ROLE_KEY") or
                os.environ.get("SUPABASE_SECRET_KEY") or
                ""
            ).strip()
        if self.anon_key is None:
            self.anon_key = (
                os.environ.get("SUPABASE_PUBLISHABLE_KEY") or
                os.environ.get("SUPABASE_ANON_KEY") or
                ""
            ).strip()

        self.is_configured = False
        self.is_connected = False
        self.last_connection_check = None
        self.last_error = None

        if not self.url or not self.service_role_key:
            self.last_error = "SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY missing"
            self.is_configured = False
            return

        # Check placeholder strings
        if "YOUR_" in self.url.upper() or "YOUR-" in self.url.upper() or "YOUR_" in self.service_role_key.upper():
            self.last_error = "Placeholder credentials detected in environment"
            self.is_configured = False
            return

        parsed = urlparse(self.url)
        if not parsed.scheme or not parsed.netloc:
            self.last_error = "Invalid SUPABASE_URL scheme or domain format"
            self.is_configured = False
            return

        self.is_configured = True
        self.last_error = None

        self.is_configured = True
        self.last_error = None

    def _get_headers(self, prefer=None, content_type="application/json"):
        headers = {
            "apikey": self.service_role_key,
            "Authorization": f"Bearer {self.service_role_key}",
            "Content-Type": content_type
        }
        if prefer:
            headers["Prefer"] = prefer
        return headers

    def _request(self, method, endpoint, payload=None, prefer=None, timeout=12):
        """Executes HTTP request to Supabase PostgREST API."""
        if not self.is_configured:
            raise RuntimeError(f"Supabase not configured: {self.last_error}")

        url = f"{self.url}/rest/v1/{endpoint.lstrip('/')}"
        data = None
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url,
            data=data,
            headers=self._get_headers(prefer=prefer),
            method=method
        )

        try:
            with urllib.request.urlopen(req, timeout=timeout) as response:
                body = response.read().decode("utf-8")
                if body:
                    try:
                        return json.loads(body)
                    except json.JSONDecodeError:
                        return body
                return None
        except urllib.error.HTTPError as he:
            err_body = he.read().decode("utf-8", errors="replace")
            # Sanitize error output so secrets or tokens are never exposed
            safe_err = f"Supabase HTTP {he.code} on {endpoint}: {err_body[:300]}"
            self.last_error = safe_err
            raise RuntimeError(safe_err) from None
        except urllib.error.URLError as ue:
            safe_err = f"Supabase Network Error on {endpoint}: {ue.reason}"
            self.last_error = safe_err
            raise RuntimeError(safe_err) from None

    def check_connection(self):
        """
        Executes a lightweight query against public.datasets to verify database health.
        Returns a dict summarizing connectivity and status.
        """
        self._validate_configuration()
        if not self.is_configured:
            return {
                "configured": False,
                "connected": False,
                "status": "UNCONFIGURED",
                "message": self.last_error or "Supabase environment variables not set."
            }

        try:
            # Query 1 dataset record to verify credentials and table existence
            self._request("GET", "datasets?select=id&limit=1", timeout=5)
            self.is_connected = True
            self.last_connection_check = datetime.now(timezone.utc).isoformat()
            self.last_error = None
            return {
                "configured": True,
                "connected": True,
                "status": "HEALTHY",
                "message": "Connected to Supabase PostgreSQL analytical schema.",
                "checked_at": self.last_connection_check
            }
        except Exception as e:
            self.is_connected = False
            self.last_connection_check = datetime.now(timezone.utc).isoformat()
            return {
                "configured": True,
                "connected": False,
                "status": "DISCONNECTED",
                "message": str(e),
                "checked_at": self.last_connection_check
            }

    # =========================================================================
    # DATASET INGESTION & LIFECYCLE
    # =========================================================================

    def create_dataset_record(self, name, filename="", file_size=0, content_hash="",
                              tx_count=0, account_count=0, status="validating"):
        """Initializes a dataset row in public.datasets."""
        payload = {
            "name": name or "Uploaded Transaction Ledger",
            "original_filename": filename or "upload.csv",
            "file_size_bytes": file_size,
            "content_hash": content_hash,
            "upload_status": status,
            "transaction_count": tx_count,
            "account_count": account_count,
            "analysis_status": "pending"
        }
        res = self._request("POST", "datasets", payload=[payload], prefer="return=representation")
        if res and isinstance(res, list) and len(res) > 0:
            return res[0]
        return None

    def update_dataset_record(self, dataset_id, **fields):
        """Updates metadata on an existing dataset row."""
        return self._request(
            "PATCH",
            f"datasets?id=eq.{dataset_id}",
            payload=fields,
            prefer="return=representation"
        )

    def insert_transactions_batch(self, dataset_id, transactions, batch_size=500):
        """
        Batches normalized transaction records into public.transactions.
        Ensures atomic batch processing.
        """
        total_inserted = 0
        if not transactions:
            return 0

        for i in range(0, len(transactions), batch_size):
            chunk = transactions[i:i + batch_size]
            payload = []
            for tx in chunk:
                # Format timestamp as ISO string for PostgreSQL TIMESTAMPTZ
                ts_val = tx.get("timestamp")
                if isinstance(ts_val, (int, float)):
                    # Epoch millis to ISO
                    dt = datetime.utcfromtimestamp(ts_val / 1000.0)
                    ts_iso = dt.isoformat() + "Z"
                elif isinstance(ts_val, str) and ts_val:
                    ts_iso = ts_val
                else:
                    ts_iso = datetime.utcnow().isoformat() + "Z"

                payload.append({
                    "dataset_id": dataset_id,
                    "transaction_id": str(tx.get("id") or tx.get("transaction_id")),
                    "sender_id": str(tx.get("sender_id") or tx.get("sender")),
                    "receiver_id": str(tx.get("receiver_id") or tx.get("receiver")),
                    "amount": round(float(tx.get("amount") or 0.0), 2),
                    "currency": tx.get("currency", "USD"),
                    "transaction_timestamp": ts_iso,
                    "flags": tx.get("flags", []),
                    "is_laundering": bool(tx.get("is_laundering", False)),
                    "category": tx.get("category", "TRANSFER")
                })

            self._request(
                "POST",
                "transactions?on_conflict=dataset_id,transaction_id",
                payload=payload,
                prefer="resolution=merge-duplicates,return=minimal"
            )
            total_inserted += len(payload)

        return total_inserted

    def insert_accounts_batch(self, dataset_id, accounts_dict, batch_size=500):
        """
        Inserts observed account profiles into public.accounts.
        accounts_dict maps account_identifier to { first_seen, last_seen, inflow, outflow, tx_count }.
        """
        if not accounts_dict:
            return 0

        acc_list = list(accounts_dict.items())
        total_inserted = 0
        for i in range(0, len(acc_list), batch_size):
            chunk = acc_list[i:i + batch_size]
            payload = []
            for acc_id, stats in chunk:
                payload.append({
                    "dataset_id": dataset_id,
                    "account_identifier": str(acc_id),
                    "total_inflow": round(float(stats.get("inflow", 0.0)), 2),
                    "total_outflow": round(float(stats.get("outflow", 0.0)), 2),
                    "tx_count": int(stats.get("tx_count", 0))
                })

            self._request(
                "POST",
                "accounts?on_conflict=dataset_id,account_identifier",
                payload=payload,
                prefer="resolution=merge-duplicates,return=minimal"
            )
            total_inserted += len(payload)

        return total_inserted

    # =========================================================================
    # FORENSIC PERSISTENCE (LOUVAIN, AGENTS, RINGS, CASES)
    # =========================================================================

    def create_analysis_run(self, dataset_id, algorithm_version="1.0.0-louvain-multiagent", config=None):
        """Records an execution of the analytical forensic pipeline."""
        payload = [{
            "dataset_id": dataset_id,
            "status": "processing",
            "algorithm_version": algorithm_version,
            "configuration": config or {}
        }]
        res = self._request("POST", "analysis_runs", payload=payload, prefer="return=representation")
        if res and isinstance(res, list) and len(res) > 0:
            return res[0]["id"]
        return None

    def save_communities_and_members(self, analysis_run_id, dataset_id, communities):
        """Persists Louvain community detection clusters and account membership."""
        if not communities:
            return

        for comm in communities:
            c_payload = [{
                "analysis_run_id": analysis_run_id,
                "community_identifier": int(comm.get("id", 0)),
                "modularity_score": round(float(comm.get("modularity", 0.0)), 6) if comm.get("modularity") is not None else None,
                "account_count": len(comm.get("members", [])),
                "aggregate_transaction_volume": round(float(comm.get("volume", 0.0)), 2),
                "community_metrics": {
                    "risk_level": comm.get("risk_level", "LOW"),
                    "dominant_pattern": comm.get("dominant_pattern", "Normal Flow")
                }
            }]
            c_res = self._request("POST", "communities?on_conflict=analysis_run_id,community_identifier", payload=c_payload, prefer="resolution=merge-duplicates,return=representation")
            if c_res and isinstance(c_res, list) and len(c_res) > 0:
                comm_db_id = c_res[0]["id"]
                members = comm.get("members", [])
                if members:
                    m_payload = [{
                        "community_id": comm_db_id,
                        "dataset_id": dataset_id,
                        "account_identifier": str(m),
                        "role": "core" if idx == 0 else "member"
                    } for idx, m in enumerate(members)]
                    self._request("POST", "community_members?on_conflict=community_id,account_identifier", payload=m_payload, prefer="resolution=merge-duplicates,return=minimal")

    def save_mule_rings(self, analysis_run_id, rings):
        """Persists detected candidate mule rings and forensic flow topologies."""
        if not rings:
            return

        payload = []
        for r in rings:
            payload.append({
                "analysis_run_id": analysis_run_id,
                "candidate_identifier": str(r.get("ring_id")),
                "pattern_type": str(r.get("pattern_type", "Circular Cycle")),
                "member_accounts": r.get("member_accounts", []),
                "relevant_transaction_references": r.get("relevant_transactions", []),
                "detection_indicators": r.get("triggered_indicators", []),
                "risk_score": round(float(r.get("risk_score", 85.0)), 2),
                "investigation_status": r.get("status", "UNDER_REVIEW"),
                "flow_path": r.get("flow_path", ""),
                "total_volume": round(float(r.get("total_volume", 0.0)), 2)
            })

        self._request("POST", "mule_ring_candidates?on_conflict=analysis_run_id,candidate_identifier", payload=payload, prefer="resolution=merge-duplicates,return=minimal")

    def save_risk_assessments(self, analysis_run_id, accounts):
        """Persists scored risk assessments and categorization."""
        if not accounts:
            return

        payload = []
        for acc in accounts:
            score = round(float(acc.get("risk_score", 10.0)), 2)
            if score >= 80:
                band = "CRITICAL"
            elif score >= 60:
                band = "HIGH"
            elif score >= 35:
                band = "MEDIUM"
            else:
                band = "LOW"

            payload.append({
                "analysis_run_id": analysis_run_id,
                "account_identifier": str(acc.get("id") or acc.get("account_id")),
                "risk_score": score,
                "risk_band": band,
                "detection_indicators": acc.get("flags", []),
                "evidence_references": {
                    "inflow": acc.get("inflow", 0),
                    "outflow": acc.get("outflow", 0),
                    "counterparties": acc.get("counterparties_count", 0)
                }
            })

        self._request("POST", "risk_assessments?on_conflict=analysis_run_id,account_identifier", payload=payload, prefer="resolution=merge-duplicates,return=minimal")

    def complete_analysis_run(self, run_id, summary_metrics):
        """Marks analysis run as successfully completed with summary metrics."""
        self._request(
            "PATCH",
            f"analysis_runs?id=eq.{run_id}",
            payload={
                "status": "completed",
                "completed_at": datetime.utcnow().isoformat() + "Z",
                "summary_metrics": summary_metrics
            }
        )

    def save_investigation_cases(self, dataset_id, analysis_run_id, cases):
        """Persists investigation case dockets."""
        if not cases:
            return

        payload = []
        for c in cases:
            payload.append({
                "case_reference": str(c.get("id")),
                "dataset_id": dataset_id,
                "analysis_run_id": analysis_run_id,
                "title": str(c.get("title", "New Investigation")),
                "description": str(c.get("description", "")),
                "priority": str(c.get("priority", "HIGH")),
                "status": str(c.get("status", "ACTIVE")),
                "linked_accounts": c.get("linked_accounts", []),
                "assigned_unit": str(c.get("assigned_unit", "Financial Crimes Enforcement Unit"))
            })

        self._request("POST", "investigation_cases?on_conflict=case_reference", payload=payload, prefer="resolution=merge-duplicates,return=minimal")

    def verify_dataset_records(self, dataset_id):
        """Queries PostgreSQL to confirm stored count of transactions and accounts."""
        if not self.is_configured:
            return {"persisted": False, "transactions_count": 0, "accounts_count": 0}
        try:
            txs = self._request("GET", f"transactions?dataset_id=eq.{dataset_id}&select=id") or []
            accs = self._request("GET", f"accounts?dataset_id=eq.{dataset_id}&select=id") or []
            return {
                "persisted": True,
                "transactions_count": len(txs),
                "accounts_count": len(accs)
            }
        except Exception as e:
            logger.warning(f"Verification query failed: {e}")
            return {"persisted": False, "error": str(e), "transactions_count": 0, "accounts_count": 0}

    # =========================================================================
    # CASE MANAGEMENT & AUDITING
    # =========================================================================


    def log_audit_action(self, action, entity_type, entity_id=None, metadata=None):
        """Records an immutable audit trail entry."""
        if not self.is_configured:
            return
        try:
            payload = [{
                "action": str(action),
                "entity_type": str(entity_type),
                "entity_id": str(entity_id) if entity_id else None,
                "metadata": metadata or {}
            }]
            self._request("POST", "audit_logs", payload=payload, prefer="return=minimal")
        except Exception as e:
            logger.warning(f"Failed to record audit log: {e}")

    # =========================================================================
    # QUERY & RETRIEVAL HELPERS
    # =========================================================================

    def list_recent_datasets(self, limit=20):
        """Lists historical datasets ordered by upload timestamp."""
        if not self.is_configured:
            return []
        try:
            return self._request("GET", f"datasets?select=*&order=uploaded_at.desc&limit={limit}") or []
        except Exception:
            return []

    def get_dataset_by_id(self, dataset_id):
        """Fetches a specific dataset record with metadata."""
        if not self.is_configured:
            return None
        try:
            res = self._request("GET", f"datasets?id=eq.{dataset_id}&limit=1")
            return res[0] if res and len(res) > 0 else None
        except Exception:
            return None

    def get_dataset_transactions(self, dataset_id, limit=2000):
        """Fetches normalized transactions associated with a dataset."""
        if not self.is_configured:
            return []
        try:
            return self._request("GET", f"transactions?dataset_id=eq.{dataset_id}&limit={limit}") or []
        except Exception:
            return []


# Global singleton instance for application use
supabase_service = SupabaseService()
