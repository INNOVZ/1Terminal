# Swing Confluence V10 Pine audit

Reviewed on 2 August 2026 using the TradingView quantitative workflow and a direct editor compile
attempt.

## Corrected

1. A stray line-continuation backslash split `groupTrend` in the `showRibbon` input and was a
   definite syntax error.
2. The higher-timeframe EMA slope was calculated after the 4h value had been projected onto chart
   bars. On a 1h chart, `htfEmaMid[slopeLen]` therefore meant five 1h bars rather than five 4h bars.
   The slope is now calculated inside `request.security`, so its lookback uses HTF bars.

## Repainting assessment

- The default `useConfirmedHtf=true` branch follows the established non-lookahead pattern: prior
  HTF values (`[1]`) requested with `barmerge.lookahead_on`.
- The optional `useConfirmedHtf=false` branch intentionally reads the developing HTF candle and can
  change until that candle closes. It should not be used for non-repainting alerts or automation.
- Fresh entry conditions and alerts are gated by `barstate.isconfirmed`.

## Remaining verification gate

TradingView accepted the corrected source into the Pine editor, but the account was signed out and
the site required authentication when `Add to chart` initiated compilation. Therefore the corrected
file has not yet received a successful server-side Pine v6 compile. Sign in to TradingView and rerun
`Add to chart` before treating it as release-verified.

## Strategy cautions

- TradingView sizing remains `10% of equity`; it does not reproduce Atlas's stop-distance-based
  0.25% portfolio risk sizing.
- Backtest fills use the configured commission and two ticks of slippage, but they do not model
  Binance funding, spread, partial fills, rate limits, or unknown execution state.
- Run 1h and 4h out-of-sample/walk-forward tests per asset class. Do not optimize one parameter set
  across crypto, equities, and index futures and assume transferability.
