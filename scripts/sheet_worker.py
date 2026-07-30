import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from crm.config import settings
from crm.database import SessionLocal
from crm.google_sheets import google_sheets_client
from crm.models import (
    MontageTask,
    Project,
    Publication,
    Scenario,
    SheetEventStatus,
    SheetInboundEvent,
    SheetSource,
    SheetWritebackEvent,
    SheetWritebackStatus,
)
from crm.sheet_mapping import canonical_layout_enabled
from crm.sheet_sync import (
    crm_owned_writeback_snapshot,
    dequeue_redis,
    enqueue_sheet_writeback,
    process_inbound_event,
    process_writeback_event,
)

logger = logging.getLogger(__name__)
CANONICAL_BACKFILL_VERSION = "canonical-role-writeback-v1"
BACKFILL_BATCH_SIZE = 200


async def enqueue_canonical_backfill() -> int:
    """Queue one full snapshot for every existing row in the approved layout."""
    queued_count = 0
    async with SessionLocal() as session:
        sources = list(
            (
                await session.scalars(
                    select(SheetSource).where(SheetSource.enabled.is_(True))
                )
            ).all()
        )
        canonical_source_ids = {
            source.id for source in sources if canonical_layout_enabled(source)
        }
        if not canonical_source_ids:
            return 0
        scenario_ids = list(
            (
                await session.scalars(
                    select(Scenario.id)
                    .where(Scenario.sheet_source_id.in_(canonical_source_ids))
                    .order_by(Scenario.id)
                )
            ).all()
        )
        for offset in range(0, len(scenario_ids), BACKFILL_BATCH_SIZE):
            batch_ids = scenario_ids[offset : offset + BACKFILL_BATCH_SIZE]
            correlations = {
                scenario_id: f"{CANONICAL_BACKFILL_VERSION}:{scenario_id}"
                for scenario_id in batch_ids
            }
            existing = set(
                (
                    await session.scalars(
                        select(SheetWritebackEvent.correlation_id).where(
                            SheetWritebackEvent.correlation_id.in_(
                                correlations.values()
                            )
                        )
                    )
                ).all()
            )
            scenarios = list(
                (
                    await session.scalars(
                        select(Scenario)
                        .where(Scenario.id.in_(batch_ids))
                        .options(
                            selectinload(Scenario.project).selectinload(Project.client),
                            selectinload(Scenario.assigned_scenarist),
                            selectinload(Scenario.research),
                            selectinload(Scenario.content),
                            selectinload(Scenario.approvals),
                            selectinload(Scenario.montage).selectinload(
                                MontageTask.assigned_editor
                            ),
                            selectinload(Scenario.publication).selectinload(
                                Publication.assigned_publisher
                            ),
                            selectinload(Scenario.final_revision_gate),
                        )
                    )
                ).all()
            )
            for scenario in scenarios:
                correlation_id = correlations[scenario.id]
                if correlation_id in existing:
                    continue
                event = await enqueue_sheet_writeback(
                    session,
                    scenario,
                    crm_owned_writeback_snapshot(scenario),
                    correlation_id=correlation_id,
                )
                queued_count += event is not None
            await session.commit()
    return queued_count


async def run_once() -> bool:
    async with SessionLocal() as session:
        inbound_id = await session.scalar(
            select(SheetInboundEvent.id)
            .where(
                or_(
                    SheetInboundEvent.status == SheetEventStatus.RECEIVED,
                    (
                        (SheetInboundEvent.status == SheetEventStatus.PROCESSING)
                        & (
                            SheetInboundEvent.updated_at
                            <= datetime.now(UTC) - timedelta(minutes=5)
                        )
                    ),
                ),
                SheetInboundEvent.attempts < 10,
            )
            .order_by(SheetInboundEvent.created_at)
            .limit(1)
        )
        if inbound_id is not None:
            await process_inbound_event(session, inbound_id)
            await session.commit()
            return True

        writeback_id = await session.scalar(
            select(SheetWritebackEvent.id)
            .where(
                SheetWritebackEvent.status.in_(
                    [SheetWritebackStatus.PENDING, SheetWritebackStatus.FAILED]
                ),
                SheetWritebackEvent.attempts < 10,
                or_(
                    SheetWritebackEvent.next_attempt_at.is_(None),
                    SheetWritebackEvent.next_attempt_at <= datetime.now(UTC),
                ),
            )
            .order_by(SheetWritebackEvent.created_at)
            .limit(1)
        )
        if writeback_id is not None:
            if not settings.google_service_account_json:
                return False
            await process_writeback_event(
                session,
                writeback_id,
                google_sheets_client(settings),
                max_retries=settings.sheet_google_max_retries,
            )
            await session.commit()
            return True
    return False


async def main() -> None:
    queued_backfills = await enqueue_canonical_backfill()
    if queued_backfills:
        logger.info("Queued %s canonical Google Sheets backfills", queued_backfills)
    while True:
        try:
            queued = await dequeue_redis(
                settings,
                ("crm:sheet:inbound", "crm:sheet:writeback"),
                timeout_seconds=2,
            )
            if queued is not None:
                queue, event_id = queued
                async with SessionLocal() as session:
                    if queue == "crm:sheet:inbound":
                        await process_inbound_event(session, event_id)
                    else:
                        await process_writeback_event(
                            session,
                            event_id,
                            google_sheets_client(settings),
                            max_retries=settings.sheet_google_max_retries,
                        )
                    await session.commit()
                continue
            # PostgreSQL is authoritative. Polling recovers missed Redis
            # notifications and processes events created before/without Redis.
            if not await run_once():
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            raise
        except Exception:
            # The API container must not silently lose its background worker
            # after one malformed row or transient database/network failure.
            logger.exception("Sheets worker iteration failed")
            await asyncio.sleep(2)


if __name__ == "__main__":
    asyncio.run(main())
