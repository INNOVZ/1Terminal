"""Opt-in one-shot public evidence capture. Does not enroll cohorts or start workers."""

from __future__ import annotations

import argparse
import asyncio
import contextvars
import json
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from algotrader.adapters.binance.client import PUBLIC_PATHS, BinanceHttpClient
from algotrader.adapters.binance.futures import BinanceUsdmAdapter
from algotrader.adapters.external.macro_provider import MacroSentimentProvider
from algotrader.application.scan_replay import ReplayMarket, ReplayTape
from algotrader.domain.crypto_screener_models import ScreenerMode
from algotrader.domain.scan_observations import RecordedCall, ScanDecisionJournal
from scripts.replay_contract import ReplayUnavailable, digest
from scripts.research_immutable_capture import capture_control
from scripts.research_pipeline_comparison import FundingCarryScreener
from scripts.research_verified_replay import diagnostic_settings

SCHEMA = "atlas-public-evidence-v3"
_SCOPE = contextvars.ContextVar("atlas_evidence_scope", default=None)
_ALLOWED = PUBLIC_PATHS | {"/fapi/v1/fundingInfo"}


class EvidenceStopped(RuntimeError):
    pass


class EvidenceTransport(httpx.AsyncBaseTransport):
    """Bound wire attempts/bytes, stop on throttling, and retain public JSON receipts."""

    def __init__(self, inner, *, attempts=1500, seconds=180, byte_limit=64 * 1024 * 1024):
        if (
            not 1 <= attempts <= 3000
            or not 1 <= seconds <= 600
            or not 1 <= byte_limit <= 128 * 1024 * 1024
        ):
            raise ValueError("invalid evidence budget")
        self.inner, self.max_attempts, self.byte_limit = inner, attempts, byte_limit
        self.deadline = time.monotonic() + seconds
        self.attempts = self.bytes = 0
        self.stopped: str | None = None
        self.receipts: list[dict[str, Any]] = []

    async def handle_async_request(self, request):
        forbidden = {"signature", "apikey", "api_key", "secret", "timestamp", "recvwindow"}
        if (
            request.method != "GET"
            or request.url.scheme != "https"
            or request.url.host != "fapi.binance.com"
            or request.url.port not in (None, 443)
            or request.url.path not in _ALLOWED
            or any(k.lower() in forbidden for k in request.url.params)
            or any(
                k.lower() in {"authorization", "x-mbx-apikey", "cookie"} for k in request.headers
            )
        ):
            raise EvidenceStopped("public_scope_violation")
        if self.stopped or self.attempts >= self.max_attempts or time.monotonic() >= self.deadline:
            self.stopped = self.stopped or "capture_budget_exhausted"
            raise EvidenceStopped(self.stopped)
        self.attempts += 1
        row = {
            "scope": _SCOPE.get(),
            "path": request.url.path,
            "params": dict(request.url.params),
            "started_at": datetime.now(UTC),
        }
        self.receipts.append(row)
        response = None
        try:
            response = await self.inner.handle_async_request(request)
            row["status"] = response.status_code
            row["retry_after"] = response.headers.get("Retry-After")
            try:
                row["used_weight_1m"] = int(response.headers.get("x-mbx-used-weight-1m", "0"))
            except ValueError:
                row["used_weight_1m"] = None
            if (row["used_weight_1m"] or 0) >= 2150:
                self.stopped = "exchange_weight_ceiling"
            if response.status_code in (418, 429):
                self.stopped = "exchange_cooldown"
                raise EvidenceStopped(self.stopped)
            data = bytearray()
            async for chunk in response.aiter_bytes():
                self.bytes += len(chunk)
                if self.bytes > self.byte_limit or len(data) + len(chunk) > 8 * 1024 * 1024:
                    self.stopped = "capture_byte_limit"
                    raise EvidenceStopped(self.stopped)
                data.extend(chunk)
            payload = json.loads(
                data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite_json"))
            )
            row.update(payload=payload, payload_sha256=digest(payload))
            # Buffered replacement: the normal production client parses the same JSON body.
            return httpx.Response(
                response.status_code,
                headers={
                    k: v
                    for k, v in response.headers.items()
                    if k.lower() not in {"content-encoding", "content-length"}
                },
                content=bytes(data),
                request=request,
            )
        except BaseException as exc:
            row["error"] = type(exc).__name__
            raise
        finally:
            row["available_at"] = datetime.now(UTC)
            if response is not None:
                await response.aclose()

    async def aclose(self):
        await self.inner.aclose()


class EvidenceClient(BinanceHttpClient):
    async def request(self, method, path, *, params=None):
        if method == "GET" and path == "/fapi/v1/fundingInfo" and not params:
            return self._parse_response(await self._http.get(path))
        return await super().request(method, path, params=params)


class EvidencePort:
    """Observe high-level call bounds without changing the returned adapter values."""

    def __init__(self, adapter):
        self.adapter = adapter
        self.venue = adapter.venue
        self.frames = []
        self.counts = {}

    async def invoke(self, method, arguments):
        key = digest((method, arguments))
        index = self.counts.get(key, 0)
        self.counts[key] = index + 1
        frame = {
            "scope": f"{key}:{index}",
            "method": method,
            "arguments": arguments,
            "index": index,
            "started_at": datetime.now(UTC),
        }
        self.frames.append(frame)
        token = _SCOPE.set(frame["scope"])
        try:
            value = await getattr(self.adapter, method)(**arguments)
            frame["call_sha256"] = digest(RecordedCall(method, index, arguments, value))
            return value
        except BaseException as exc:
            frame["error"] = type(exc).__name__
            raise
        finally:
            frame["available_at"] = datetime.now(UTC)
            _SCOPE.reset(token)

    async def instruments(self):
        return await self.invoke("instruments", {})

    async def quotes(self, instruments):
        return await self.invoke("quotes", {"instruments": tuple(instruments)})

    async def candles(self, instrument, interval, limit):
        return await self.invoke(
            "candles", {"instrument": instrument, "interval": interval, "limit": limit}
        )

    async def derivatives_snapshot(self, instrument):
        return await self.invoke("derivatives_snapshot", {"instrument": instrument})


class MissingTape(ReplayTape):
    def __init__(self, calls):
        super().__init__(calls)
        self.requests = []

    def take(self, method, arguments):
        key = digest((method, arguments))
        try:
            return super().take(method, arguments)
        except ValueError:
            self.requests.append((key, method, arguments))
            raise


async def capture_evidence(
    port, macro, settings, *, max_symbols, max_supplemental=60, on_control=None,
    discovery_v2=False,
):
    if not 1 <= max_symbols <= 320 or not 0 <= max_supplemental <= 60:
        raise ValueError("invalid capture selection budget")
    captured = await capture_control(
        port,
        port,
        macro,
        settings,
        {"mode": ScreenerMode.ALL, "max_symbols": max_symbols, "enforce_quality": True},
    )
    record = captured.record
    if on_control is not None:
        on_control(record)
    supplemental = []
    if record.status == "complete":
        # Planning only. Discard these partial outputs; missing calls are never predictions.
        tape = MissingTape(record.inputs)
        market = ReplayMarket(tape)
        engine = FundingCarryScreener(
            settings, market, evaluated_at=record.started_at, journal=ScanDecisionJournal()
        )
        arguments = dict(record.request)
        arguments.pop("derivatives_enabled")
        await engine.screen(market, derivatives_port=market, **arguments)
        if any(method != "derivatives_snapshot" for _, method, _ in tape.requests):
            raise ReplayUnavailable("unexpected_challenger_input_request")
        for _, method, arguments in tape.requests[:max_supplemental]:
            index = port.counts.get(digest((method, arguments)), 0)
            try:
                value = await port.invoke(method, arguments)
                supplemental.append(RecordedCall(method, index, arguments, value))
            except Exception as exc:
                supplemental.append(
                    RecordedCall(method, index, arguments, error=type(exc).__name__)
                )
        if discovery_v2:
            from scripts.research_discovery import DiscoveryScreener

            # Resolve extended candle histories, then additional enrichment requests.
            for _ in range(3):
                tape = MissingTape((*record.inputs, *supplemental))
                market = ReplayMarket(tape)
                engine = DiscoveryScreener(settings, market, evaluated_at=record.started_at,
                                            journal=ScanDecisionJournal())
                arguments = dict(record.request)
                arguments.pop("derivatives_enabled")
                await engine.screen(market, derivatives_port=market, **arguments)
                pending = {key: (method, arguments) for key, method, arguments in tape.requests}
                if not pending:
                    break
                for method, arguments in pending.values():
                    if method not in ("candles", "derivatives_snapshot"):
                        raise ReplayUnavailable("unexpected_discovery_input_request")
                    if method == "candles" and (arguments["interval"], arguments["limit"]) not in (
                        ("1h", 360), ("4h", 480), ("1w", 40)
                    ):
                        raise ReplayUnavailable("unexpected_discovery_candle_request")
                    derivative_count = sum(c.method == "derivatives_snapshot" for c in supplemental)
                    if len(supplemental) >= 420 or (method == "derivatives_snapshot" and derivative_count >= max_supplemental):
                        raise ReplayUnavailable("discovery_supplemental_budget_exhausted")
                    index = port.counts.get(digest((method, arguments)), 0)
                    try:
                        value = await port.invoke(method, arguments)
                    except Exception as exc:
                        supplemental.append(RecordedCall(method, index, arguments, error=type(exc).__name__))
                        raise ReplayUnavailable("discovery_input_acquisition_failed") from exc
                    supplemental.append(RecordedCall(method, index, arguments, value))
            else:
                raise ReplayUnavailable("discovery_input_planning_did_not_converge")
    return record, tuple(supplemental)


async def execute(args, *, transport=None, macro_provider=None):
    discovery = getattr(args, "discovery_v2", False)
    if discovery and args.max_symbols != 320:
        raise ValueError("discovery-v2 requires the explicit 320-symbol ceiling")
    budget = EvidenceTransport(
        transport if transport is not None else httpx.AsyncHTTPTransport(),
        attempts=args.max_attempts,
        seconds=args.deadline_seconds,
        byte_limit=(128 if discovery else 64) * 1024 * 1024,
    )
    client = EvidenceClient(
        "https://fapi.binance.com",
        transport=budget,
        max_rate_limit_retries=0,
        max_transport_retries=0,
        timeout_seconds=8,
    )
    macro = macro_provider if macro_provider is not None else MacroSentimentProvider()
    port = EvidencePort(BinanceUsdmAdapter(client))
    bundle = {
        "schema": "atlas-public-evidence-v4" if discovery else SCHEMA,
        "status": "incomplete",
        "record": None,
        "supplemental": (),
        "metadata": [],
        "frames": port.frames,
        "receipts": budget.receipts,
        "performance_claims_permitted": False,
        "enrolled": False,
        "outcome_anchor": None,
    }
    try:
        async with asyncio.timeout(args.deadline_seconds):
            record, supplemental = await capture_evidence(
                port,
                macro,
                diagnostic_settings(),
                max_symbols=args.max_symbols,
                max_supplemental=args.max_supplemental,
                on_control=lambda r: bundle.update(record=r),
                discovery_v2=discovery,
            )
            bundle.update(record=record, supplemental=supplemental)
            # Explicit metadata; never treat an omitted fundingInfo row as an 8h interval.
            bundle["metadata"].append(await metadata_call(client, "/fapi/v1/fundingInfo", {}))
            selected = {
                e["symbol"]
                for e in record.decisions
                if e["stage"] == "selection" and e["reason"] == "selected"
            }
            if discovery:
                from scripts.research_discovery import POLICY as DISCOVERY_POLICY

                bundle["research_policy"] = dict(DISCOVERY_POLICY)
                selected.update(c.arguments["instrument"].id.symbol for c in supplemental)
                bundle["research_selected_symbols"] = tuple(sorted(selected))
            for symbol in sorted(selected):
                bundle["metadata"].append(
                    await metadata_call(
                        client, "/fapi/v1/fundingRate", {"symbol": symbol, "limit": 1}
                    )
                )
            bundle["status"] = "captured" if record.status == "complete" else "incomplete"
    except (Exception, asyncio.CancelledError) as exc:
        bundle["error"] = type(exc).__name__
    finally:
        await asyncio.gather(client.close(), macro.close(), return_exceptions=True)
    bundle.update(
        completed_at=datetime.now(UTC),
        attempts=budget.attempts,
        response_bytes=budget.bytes,
        stopped=budget.stopped,
    )
    return bundle


async def metadata_call(client, path, params):
    return {
        "path": path,
        "params": params,
        "payload": await client.request("GET", path, params=params),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--execute-public-capture", action="store_true", required=True)
    parser.add_argument("--max-symbols", type=int, required=True)
    parser.add_argument("--max-supplemental", type=int, default=60)
    parser.add_argument("--discovery-v2", action="store_true",
                        help="Capture corrected features and bounded discovery watch pool")
    parser.add_argument("--max-attempts", type=int)
    parser.add_argument("--deadline-seconds", type=int)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("output exists")
    if not 1 <= args.max_symbols <= 320 or not 0 <= args.max_supplemental <= 60:
        parser.error("invalid symbol budget")
    if args.discovery_v2 and args.max_symbols != 320:
        parser.error("discovery-v2 requires the explicit 320-symbol ceiling")
    if args.max_attempts is None:
        args.max_attempts = 3000 if args.discovery_v2 else 1500
    if args.deadline_seconds is None:
        args.deadline_seconds = 600 if args.discovery_v2 else 180
    bundle = asyncio.run(execute(args))
    from scripts.research_evidence_stream import write_bundle

    write_bundle(args.output, bundle)
    return 0 if bundle["status"] == "captured" else 3


if __name__ == "__main__":
    raise SystemExit(main())
