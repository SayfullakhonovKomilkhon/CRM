import asyncio
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select

from crm.config import settings
from crm.database import SessionLocal
from crm.google_sheets import google_sheets_client
from crm.models import (
    SheetEventStatus,
    SheetInboundEvent,
    SheetWritebackEvent,
    SheetWritebackStatus,
)
from crm.sheet_sync import dequeue_redis, process_inbound_event, process_writeback_event

logger = logging.getLogger(__name__)


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
