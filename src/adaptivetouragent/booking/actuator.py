"""Sandboxed booking actuation.

Closes the loop on booking actuation: travel-planning demos and
benchmarks (Xie 2024 TravelPlanner, Chen 2024 TravelAgent, Ning 2025
DeepTravel, Karmakar 2025 TripTide) all stop at plan emission. This module
records bookings to an audit log with synthetic confirmation IDs; no
external API calls. A real deployment would swap `_call_provider` for
actual Booking.com / Klook / Skyscanner integrations.

The actuator is rate-limited and budget-capped at the same layer as the
LLM client (cost cap is enforced by the caller, see ui/server.py).
"""

import json
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Literal

from adaptivetouragent.accommodations.types import Accommodation
from adaptivetouragent.itinerary.types import POIVisit

BookingKind = Literal["poi_ticket", "accommodation", "transit"]
BookingStatus = Literal["pending", "confirmed", "cancelled", "failed"]


@dataclass
class BookingRecord:
    """One booking: single source of truth that hits the audit log."""

    booking_id: str
    kind: BookingKind
    target_id: str           # poi_id, accommodation_id, edge id
    target_name: str
    when: datetime           # arrival / check-in time
    party_size: int
    amount_usd: float
    status: BookingStatus
    confirmation_code: str | None
    created_at: datetime = field(default_factory=datetime.now)
    notes: str = ""


class BookingActuator:
    """Records bookings to a JSONL audit log.

    Defaults to dry-run mode: bookings are marked `confirmed` with a
    synthetic confirmation code, and no external service is contacted.
    Set `dry_run=False` only when a real provider implementation is wired
    via `_call_provider`.
    """

    def __init__(
        self,
        *,
        audit_log_path: str | Path | None = None,
        dry_run: bool = True,
        max_bookings_per_session: int = 20,
    ):
        self.audit_log_path = Path(audit_log_path) if audit_log_path else None
        if self.audit_log_path is not None:
            self.audit_log_path.parent.mkdir(parents=True, exist_ok=True)
        self.dry_run = dry_run
        self.max_bookings_per_session = max_bookings_per_session
        self._records: list[BookingRecord] = []

    @property
    def records(self) -> list[BookingRecord]:
        return list(self._records)

    @property
    def total_amount_usd(self) -> float:
        return sum(
            r.amount_usd for r in self._records if r.status == "confirmed"
        )

    def book_poi_visit(self, visit: POIVisit, party_size: int = 1) -> BookingRecord:
        amount = visit.entry_fee_usd  # already party-multiplied by the optimiser
        return self._book(
            kind="poi_ticket",
            target_id=visit.poi_id,
            target_name=visit.name,
            when=visit.arrive,
            party_size=party_size,
            amount_usd=amount,
        )

    def book_accommodation(
        self,
        acc: Accommodation,
        check_in: datetime,
        nights: int,
        party_size: int = 1,
    ) -> BookingRecord:
        amount = acc.price_per_night_usd * nights
        return self._book(
            kind="accommodation",
            target_id=acc.accommodation_id,
            target_name=acc.name,
            when=check_in,
            party_size=party_size,
            amount_usd=amount,
            notes=f"{nights} night(s)",
        )

    def _book(
        self,
        *,
        kind: BookingKind,
        target_id: str,
        target_name: str,
        when: datetime,
        party_size: int,
        amount_usd: float,
        notes: str = "",
    ) -> BookingRecord:
        if len(self._records) >= self.max_bookings_per_session:
            return self._record_failure(kind, target_id, target_name, when, party_size, amount_usd, notes,
                                        reason="rate_limit")

        booking_id = secrets.token_hex(6)
        confirmation: str | None
        if self.dry_run:
            confirmation = f"DRYRUN-{secrets.token_hex(4).upper()}"
            status: BookingStatus = "confirmed"
        else:
            ok, confirmation = self._call_provider(kind, target_id, when, party_size, amount_usd)
            status = "confirmed" if ok else "failed"

        record = BookingRecord(
            booking_id=booking_id,
            kind=kind,
            target_id=target_id,
            target_name=target_name,
            when=when,
            party_size=party_size,
            amount_usd=amount_usd,
            status=status,
            confirmation_code=confirmation,
            notes=notes,
        )
        self._append(record)
        return record

    def _record_failure(self, kind, target_id, target_name, when, party_size, amount_usd, notes, *, reason: str) -> BookingRecord:
        record = BookingRecord(
            booking_id=secrets.token_hex(6),
            kind=kind,
            target_id=target_id,
            target_name=target_name,
            when=when,
            party_size=party_size,
            amount_usd=amount_usd,
            status="failed",
            confirmation_code=None,
            notes=f"{notes} | reason={reason}".strip(" |"),
        )
        self._append(record)
        return record

    def _append(self, record: BookingRecord) -> None:
        self._records.append(record)
        if self.audit_log_path is not None:
            payload = asdict(record)
            payload["when"] = record.when.isoformat()
            payload["created_at"] = record.created_at.isoformat()
            with self.audit_log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(payload) + "\n")

    def _call_provider(
        self,
        kind: BookingKind,  # noqa: ARG002
        target_id: str,  # noqa: ARG002
        when: datetime,  # noqa: ARG002
        party_size: int,  # noqa: ARG002
        amount_usd: float,  # noqa: ARG002
    ) -> tuple[bool, str | None]:
        """Hook for real-provider integration. Returns (ok, confirmation_code)."""
        raise NotImplementedError("Real-provider booking is intentionally out of scope. Use dry_run=True.")
