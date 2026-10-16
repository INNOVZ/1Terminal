# API research notes — 2 August 2026

Only official venue documentation is treated as authoritative for endpoint and execution behavior.

## Strategy research update — 11 August 2026

- A crypto study of eleven highly traded assets found excess returns for a simple 20-day moving
  average rule in its 2016–2018 sample. This supports retaining a simple trend definition, not
  assuming every EMA parameter remains profitable in later intraday data.
  <https://doi.org/10.1016/j.frl.2019.101396>
- A broader Bitcoin study that evaluated 75,360 technical rules, transaction costs, multiple
  testing and out-of-sample portfolios found that some simple rules can improve risk-adjusted
  performance. Its methodology reinforces the need to count every attempted rule and test OOS.
  <https://doi.org/10.1016/j.iref.2024.05.003>
- A data-snooping-controlled crypto reality check found statistically significant excess returns
  were rare after market frictions and multiple-testing controls. Indicator familiarity is not
  evidence of incremental alpha.
  <https://doi.org/10.1016/j.frl.2020.101655>
- Bitcoin trend-rule evidence favors channel breakouts particularly in strongly trending markets;
  other technical rules did not consistently outperform buy-and-hold in that study.
  <https://doi.org/10.1016/j.frl.2019.08.011>
- ADX measures trend magnitude while DI supplies direction. Research using Bitcoin ADX studies
  trend/volume relationships but explicitly does not establish a profitable ADX trading strategy.
  <https://doi.org/10.1007/s40822-021-00166-5>
- The Deflated Sharpe Ratio corrects for selection bias, multiple testing and non-normal returns.
  Atlas must record all tried variants before a performance statistic can support promotion.
  <https://www.davidhbailey.com/dhbpapers/deflated-sharpe.pdf>

Design consequence: EMA can define the market regime; MACD, RSI and ADX/DMI can provide bounded
confirmation and diagnostics. A MACD cross is a lagged timing observation, RSI 50/overextension is
a regime description, and confirmed RSI divergence is a delayed warning. None receives independent
authority to create or exit a trade. The local v3 replay in `docs/research-baseline.md` rejected the
2-of-4 indicator consensus for promotion.

## Binance

- The Spot API uses signed endpoints for account and trade operations, enforces request weights and
  order limits, and warns that timeouts/5xx responses can leave execution status unknown.
  <https://developers.binance.com/en/docs/products/spot/rest-api>
- Binance provides REST, WebSocket, WebSocket Streams, FIX, and SBE interfaces. Non-production
  availability varies by product and must be verified per integration.
  <https://developers.binance.com/en/docs/introduction>
- Spot Testnet streams disconnect periodically and require ping/pong, reconnect, and sequence-aware
  recovery. Order-book streams require a REST snapshot followed by ordered delta application.
  <https://developers.binance.com/zh-CN/docs/products/spot/testnet/web-socket-streams>
- Binance's product catalog covers crypto Spot, USD-M/COIN-M Futures, Options, and Margin; it does
  not list ordinary stock brokerage.
  <https://developers.binance.com/en/docs/catalog>

## Traditional assets

- IBKR Paper supports the Web API and TWS API with execution-simulator limitations.
  <https://ibkrcampus.com/campus/glossary-terms/paper-trading-account/>
- The TWS API supports stocks, ETFs, futures, options, forex, and other contract types.
  <https://ibkrcampus.com/campus/ibkr-api-page/twsapi-doc/>
- An `IND` contract represents an index for identification/market data; tradable exposure normally
  uses an ETF, future, or option contract.
  <https://ibkrcampus.com/campus/ibkr-api-page/twsapi-ref/>
- Individual Web API access requires a fully open, funded IBKR Pro live account even when the
  associated paper account is used. Retail authentication runs through the Client Portal Gateway,
  and `/iserver` trading endpoints require an active brokerage session.
  <https://ibkrcampus.com/campus/ibkr-api-page/web-api-trading/>
- IBKR enforces 10 requests/second globally per authenticated username, limits concurrent historical
  requests, and allocates a default of 100 market-data lines. Relevant exchange subscriptions are
  still required in Paper.
  <https://ibkrcampus.com/campus/ibkr-api-page/web-api-trading/>
- New Web API orders use `POST /iserver/account/{accountId}/orders`. Some responses are warnings,
  not acknowledgements; the client must resolve the `/iserver/reply/{messageId}` flow before
  submitting another order.
  <https://ibkrcampus.com/campus/ibkr-api-page/web-api-trading/>

## Design consequences

- Binance Spot and Futures require distinct adapters and account semantics.
- Order requests are not generally safe to retry; client IDs and post-timeout reconciliation are mandatory.
- Market-data WebSockets need lifecycle and gap recovery rather than naive reconnect loops.
- A separate regulated-broker adapter is required for stocks and index products.
- IBKR integration must be explicitly enabled only after the account ID, authentication mode,
  subscribed data, and conid-based universe are configured; silently accepting broker warnings is
  prohibited.
- Paper/Testnet fills do not prove live execution quality; slippage and partial-fill stress tests remain mandatory.
