from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Annotated, Literal, cast

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, ConfigDict, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import Response

from algotrader.application.screener import candidate_payload
from algotrader.config import Environment, ExecutionMode, Settings, load_settings
from algotrader.domain.errors import TradingError
from algotrader.infrastructure.runtime import Runtime, build_runtime

bearer = HTTPBearer(auto_error=False)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class KillSwitchRequest(StrictModel):
    active: bool
    reason: str = Field(min_length=3, max_length=300)


class AbandonLifecycleRequest(StrictModel):
    # A free-text reason is required so the audit trail records why a human overrode an
    # interlock, not merely that they did.
    reason: str = Field(min_length=10, max_length=300)


class TradeCloseReconciliationRequest(StrictModel):
    exit_price: Decimal = Field(gt=0)
    fees: Decimal = Field(default=Decimal("0"), ge=0)
    exit_reason: Literal["stop", "target", "manual", "liquidation"]


def create_app(settings: Settings | None = None) -> FastAPI:
    app_settings = settings or load_settings()
    # Fail at process startup instead of discovering broken authentication on
    # the first privileged request.
    app_settings.require_operator_token()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        runtime = await build_runtime(app_settings)
        app.state.runtime = runtime
        runtime.start_automation()
        try:
            yield
        finally:
            await runtime.close()

    production = app_settings.environment is Environment.PRODUCTION
    app = FastAPI(
        title="Atlas AlgoTrader Operations API",
        version="0.1.2",
        debug=False,
        docs_url=None if production else "/docs",
        redoc_url=None,
        openapi_url=None if production else "/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(app_settings.allowed_hosts))

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Permissions-Policy"] = (
            "accelerometer=(), camera=(), geolocation=(), microphone=()"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def runtime(request: Request) -> Runtime:
        return cast(Runtime, request.app.state.runtime)

    async def require_operator(
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    ) -> None:
        expected = app_settings.require_operator_token()
        supplied = credentials.credentials if credentials else ""
        if not hmac.compare_digest(supplied, expected):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid operator credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )

    operator_api = APIRouter(
        prefix="/v1",
        dependencies=[Depends(require_operator)],
    )

    @app.exception_handler(TradingError)
    async def trading_error_handler(request: Request, exc: TradingError) -> JSONResponse:
        del request
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content={"error": type(exc).__name__, "detail": str(exc)},
        )

    @app.get("/healthz", include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/readyz", include_in_schema=False)
    async def readyz(request: Request) -> Response:
        ready, _ = await runtime(request).readiness()
        return JSONResponse(
            status_code=status.HTTP_200_OK if ready else status.HTTP_503_SERVICE_UNAVAILABLE,
            # This endpoint is intentionally unauthenticated for orchestrator probes. Keep
            # component and exception details on the protected status endpoint and logs.
            content={"status": "ready" if ready else "not_ready"},
        )

    @operator_api.get("/status")
    async def get_status(active_runtime: Annotated[Runtime, Depends(runtime)]) -> dict[str, object]:
        return await active_runtime.system.status()

    @operator_api.post("/screen")
    async def screen(active_runtime: Annotated[Runtime, Depends(runtime)]) -> dict[str, object]:
        candidates = await active_runtime.system.screen()
        return {"candidates": [candidate_payload(candidate) for candidate in candidates[:20]]}

    @operator_api.get("/screen/diagnostics")
    async def screen_diagnostics(
        active_runtime: Annotated[Runtime, Depends(runtime)],
    ) -> dict[str, object]:
        """Per-direction eligibility and blocking-reason counts for the current universe."""
        return await active_runtime.system.screen_diagnostics()

    @operator_api.get("/reconcile")
    async def reconcile(
        active_runtime: Annotated[Runtime, Depends(runtime)],
    ) -> dict[str, object]:
        """What is blocking the cycle, and what the venue believes about each item."""
        return await active_runtime.system.reconciliation_report()

    @operator_api.get("/signals")
    async def signals(active_runtime: Annotated[Runtime, Depends(runtime)]) -> dict[str, object]:
        active = await active_runtime.system.active_signals()
        return {
            "active_signals": [
                {
                    "tracked_signal_id": str(signal.signal_id),
                    "instrument": str(signal.instrument_id),
                    "direction": signal.direction.value,
                    "first_seen_at": signal.first_seen_at.isoformat(),
                    "last_seen_at": signal.last_seen_at.isoformat(),
                    "signal_bar_closed_at": signal.signal_bar_closed_at.isoformat(),
                    "expires_at": signal.expires_at.isoformat(),
                    "observations": signal.observations,
                    "score": str(signal.score),
                    "entry_price": str(signal.entry_price),
                    "status": signal.status.value,
                }
                for signal in active
            ]
        }

    @operator_api.get("/trades")
    async def trades(active_runtime: Annotated[Runtime, Depends(runtime)]) -> dict[str, object]:
        open_trades = await active_runtime.repository.list_open_trades()
        return {
            "open_trades": [
                {
                    "signal_id": str(trade.signal_id),
                    "instrument": str(trade.instrument_id),
                    "direction": trade.direction.value,
                    "strategy": trade.strategy,
                    "quantity": str(trade.quantity),
                    "entry_price": str(trade.entry_price),
                    "stop_price": str(trade.stop_price),
                    "target_price": str(trade.target_price),
                    "opened_at": trade.opened_at.isoformat(),
                }
                for trade in open_trades
            ]
        }

    @operator_api.post("/cycle")
    async def cycle(active_runtime: Annotated[Runtime, Depends(runtime)]) -> dict[str, object]:
        report = await active_runtime.system.run_cycle()
        if not report.executed or report.report is None:
            return {
                "executed": False,
                "reason": report.reason.value,
                "candidate_count": report.candidate_count,
                "detail": report.detail,
                "next_eligible_at": (
                    report.next_eligible_at.isoformat() if report.next_eligible_at else None
                ),
            }
        execution = report.report
        return {
            "executed": True,
            "reason": report.reason.value,
            "signal_id": str(execution.signal.signal_id),
            "instrument": str(execution.signal.instrument.id),
            "direction": execution.signal.direction.value,
            "entry_order_id": execution.entry.exchange_order_id,
            "protection_order_ids": [order.exchange_order_id for order in execution.protection],
        }

    @operator_api.post("/trades/{signal_id}/reconcile-close")
    async def reconcile_trade_close(
        signal_id: str,
        payload: TradeCloseReconciliationRequest,
        active_runtime: Annotated[Runtime, Depends(runtime)],
    ) -> dict[str, object]:
        if active_runtime.settings.execution_mode is ExecutionMode.LIVE:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="manual close reconciliation is disabled in live mode",
            )
        trade, gross_realized_pnl = await active_runtime.system.reconcile_trade_close(
            signal_id,
            exit_price=payload.exit_price,
            fees=payload.fees,
            exit_reason=payload.exit_reason,
        )
        return {
            "reconciled": True,
            "signal_id": str(trade.signal_id),
            "gross_realized_pnl": str(gross_realized_pnl),
            "net_realized_pnl": str(gross_realized_pnl - payload.fees),
            "kill_switch_cleared": False,
        }

    @operator_api.post("/execution-lifecycles/{signal_id}/abandon")
    async def abandon_execution_lifecycle(
        signal_id: str,
        payload: AbandonLifecycleRequest,
        active_runtime: Annotated[Runtime, Depends(runtime)],
    ) -> dict[str, object]:
        """Retire a lifecycle whose fill the operator has confirmed is no longer open."""
        return await active_runtime.system.abandon_execution_lifecycle(
            signal_id, reason=payload.reason
        )

    @operator_api.put("/kill-switch")
    async def set_kill_switch(
        payload: KillSwitchRequest,
        active_runtime: Annotated[Runtime, Depends(runtime)],
    ) -> dict[str, object]:
        await active_runtime.system.set_kill_switch(payload.active, payload.reason)
        return {"active": payload.active, "reason": payload.reason}

    app.include_router(operator_api)
    return app


app = create_app()
