/* ── Options Lab section ──────────────────────────────────────────────── */

import { DTE_BUCKETS, IV_BUCKETS } from './shared';

const IV_BUCKET_TEXT = `LOW ${IV_BUCKETS.low}, MID ${IV_BUCKETS.mid}, HIGH ${IV_BUCKETS.high}`;

// Mirrors deep_options_analyzer.py `_select_strategy` branches; not a full 3×3×2 grid.
const VERDICT_MATRIX: { bias: string; bucket: string; earnings: string; verdict: string }[] = [
  { bias: 'BULLISH', bucket: 'LOW', earnings: 'any', verdict: 'BUY_CALL' },
  { bias: 'BULLISH', bucket: 'MID', earnings: 'no', verdict: 'BUY_CALL' },
  { bias: 'BULLISH', bucket: 'MID', earnings: 'yes', verdict: 'BUY_CALL_SPREAD' },
  { bias: 'BULLISH', bucket: 'HIGH', earnings: 'any', verdict: 'SELL_PUT_SPREAD' },
  { bias: 'BEARISH', bucket: 'LOW', earnings: 'any', verdict: 'BUY_PUT' },
  { bias: 'BEARISH', bucket: 'MID', earnings: 'no', verdict: 'BUY_PUT' },
  { bias: 'BEARISH', bucket: 'MID', earnings: 'yes', verdict: 'BUY_PUT_SPREAD' },
  { bias: 'BEARISH', bucket: 'HIGH', earnings: 'any', verdict: 'SELL_CALL_SPREAD' },
  { bias: 'NEUTRAL', bucket: 'HIGH', earnings: 'any', verdict: 'SELL_IRON_CONDOR' },
  { bias: 'NEUTRAL', bucket: 'LOW', earnings: 'yes', verdict: 'BUY_STRADDLE' },
  { bias: 'NEUTRAL', bucket: 'LOW', earnings: 'no', verdict: 'NO_TRADE' },
  { bias: 'NEUTRAL', bucket: 'MID', earnings: 'any', verdict: 'NO_TRADE' },
];

function VerdictMatrixTable() {
  return (
    <div className="bg-card border border-border rounded-lg p-3 overflow-x-auto">
      <h4 className="text-sm font-semibold text-text-primary mb-1">Verdict lookup</h4>
      <p className="text-xs text-text-secondary mb-2">
        Bias × IV bucket ({IV_BUCKET_TEXT}) × earnings within 14 days. Where a row says "any",
        the engine ignores earnings proximity for that combination.
      </p>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-text-secondary text-left">
            <th className="py-1 pr-3 font-semibold">Bias</th>
            <th className="py-1 pr-3 font-semibold">IV bucket</th>
            <th className="py-1 pr-3 font-semibold">Earnings ≤14d</th>
            <th className="py-1 font-semibold">Verdict</th>
          </tr>
        </thead>
        <tbody>
          {VERDICT_MATRIX.map((r) => (
            <tr key={`${r.bias}-${r.bucket}-${r.earnings}`} className="border-t border-border">
              <td className="py-1 pr-3 text-text-primary">{r.bias}</td>
              <td className="py-1 pr-3 text-text-primary">{r.bucket}</td>
              <td className="py-1 pr-3 text-text-primary">{r.earnings}</td>
              <td className="py-1 font-mono text-accent-300">{r.verdict}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

interface LabElement {
  name: string;
  definition: string;
  effect: string;
  watchOut: string;
  values?: string;
}

function LabElementCard({ el }: { el: LabElement }) {
  return (
    <div className="bg-card border border-border rounded-lg p-3">
      <div className="flex items-baseline gap-2 mb-2 flex-wrap">
        <h4 className="text-sm font-semibold text-text-primary">{el.name}</h4>
        {el.values && (
          <span className="text-[10px] font-mono text-accent-300 bg-accent-900/30 px-1.5 py-px rounded">
            {el.values}
          </span>
        )}
      </div>
      <div className="space-y-1.5 text-xs leading-snug">
        <p>
          <span className="text-text-secondary font-semibold">Definition: </span>
          <span className="text-text-primary">{el.definition}</span>
        </p>
        <p>
          <span className="text-text-secondary font-semibold">Effect: </span>
          <span className="text-text-primary">{el.effect}</span>
        </p>
        <p>
          <span className="text-amber-400 font-semibold">Watch out: </span>
          <span className="text-text-primary">{el.watchOut}</span>
        </p>
      </div>
    </div>
  );
}

function LabSubsection({
  title,
  description,
  elements,
}: {
  title: string;
  description: string;
  elements: LabElement[];
}) {
  return (
    <div>
      <h3 className="text-base font-semibold text-text-primary mb-1">{title}</h3>
      <p className="text-xs text-text-secondary mb-3">{description}</p>
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
        {elements.map((el) => (
          <LabElementCard key={el.name} el={el} />
        ))}
      </div>
    </div>
  );
}

const LAB_HEADER_ELEMENTS: LabElement[] = [
  {
    name: 'Directional Bias',
    values: 'BULLISH / BEARISH / NEUTRAL',
    definition:
      "The engine's read on which way the stock is likely to move next, derived from the full signal stack (same score used on the Dashboard) mapped into three buckets.",
    effect:
      'Drives strategy selection together with IV bucket and earnings proximity. A BULLISH + MID IV read inside 14 days of earnings produces BUY_CALL_SPREAD; BULLISH + HIGH IV produces SELL_PUT_SPREAD; NEUTRAL + HIGH IV produces SELL_IRON_CONDOR. See the verdict lookup table below.',
    watchOut:
      'Bias is not certainty. A BULLISH tag with conviction just above 0 is a weak lean, not a green light. Always cross-check with the Dashboard signal list before trading.',
  },
  {
    name: 'Price',
    definition: 'Last traded spot from yfinance at the time of analysis.',
    effect:
      'Anchors every moneyness calculation — ATM strike, expected move percentage, distance from pin magnets, max pain distance.',
    watchOut:
      'yfinance prices are stale on weekends and off-hours. If you re-run an analysis Monday morning the whole report can shift because spot finally repriced — the analysis is a snapshot, not a live feed.',
  },
  {
    name: 'IV Rank',
    values: '0 – 100',
    definition:
      'Where current 30-day ATM implied vol sits between its 52-week high and low, expressed 0–100. 0 = at 52-week low, 100 = at 52-week high.',
    effect:
      `Buckets into ${IV_BUCKET_TEXT}. Long premium strategies (BUY_CALL, BUY_PUT, BUY_STRADDLE) only fire in LOW/MID; short premium (SELL spreads, SELL_IRON_CONDOR) only fire in HIGH.`,
    watchOut:
      'IV rank is range-bound — a stock that never moves can sit at rank 80 on tiny absolute vol. Use alongside expected move percentage to gauge real dollar risk.',
  },
  {
    name: 'IV Percentile',
    values: '0 – 100',
    definition:
      'Percentage of trading days over the last year where IV was below the current reading.',
    effect:
      "Better than IV rank for distributions with outliers — if one earnings spike sets the 52w high, IV rank understates how elevated today really is. Percentile captures the shape of the distribution.",
    watchOut:
      'When rank and percentile diverge sharply (e.g. rank 40, percentile 80), there is a recent outlier distorting rank. Treat percentile as the truer "is vol rich or cheap" read.',
  },
];

const LAB_STRATEGY_ELEMENTS: LabElement[] = [
  {
    name: 'Verdict',
    values:
      'BUY_CALL / BUY_PUT / BUY_CALL_SPREAD / BUY_PUT_SPREAD / SELL_PUT_SPREAD / SELL_CALL_SPREAD / SELL_IRON_CONDOR / BUY_STRADDLE / NO_TRADE',
    definition:
      'The single strategy the engine recommends, selected from a lookup of (directional bias) × (IV bucket) × (near-earnings flag).',
    effect:
      'Dictates everything downstream: leg structure, target expiry, risk profile. A BUY_* verdict means you pay premium; SELL_* means you collect it; NO_TRADE means no edge was found.',
    watchOut:
      'Verdict is the engine\'s single best answer, not the only viable one. BUY_STRADDLE only fires NEUTRAL + LOW IV + near earnings — a niche setup. NO_TRADE usually means conflicting signals, not "safe to ignore."',
  },
  {
    name: 'Target Expiry / DTE',
    definition:
      'The expiration bucket the engine picked for the trade. DTE = days to expiry.',
    effect:
      `Maps to the DTE spread (${DTE_BUCKETS}): weeklies are pure gamma plays, 7–21d is the earnings window, 21–45d the directional swing, 45–90d positioning, 90d+ LEAPS-like. The engine targets 21–45d by default (shifted later when earnings are near) for both long and short premium.`,
    watchOut:
      "If target DTE is <10 and verdict is long premium, theta burn is about to eat you — cross-check the THETA_BURN hidden risk. If DTE is >60 on a short-premium trade, you are tying up capital with minimal daily decay.",
  },
  {
    name: 'IV Bucket',
    values: 'LOW / MID / HIGH',
    definition: `Categorical bin of IV rank: ${IV_BUCKET_TEXT}.`,
    effect:
      'Filters which verdicts are eligible. Long premium gets blocked in HIGH IV (paying rich vol); short premium gets blocked in LOW IV (collecting nothing). MID allows directional singles in both directions.',
    watchOut:
      "The bucket edges are sharp — a rank of 69 vs 70 routes you to completely different strategies. When you are near a boundary, read the rationale to see whether the pick is robust or a coin-flip.",
  },
  {
    name: 'Earnings Tag / Earnings DTE',
    definition:
      'Flag that fires when earnings are within 14 days, with days-to-earnings count. Shown as a purple "Earnings Nd" pill.',
    effect:
      'Forces the strategy selector to prefer spreads over naked long premium (to limit IV crush) and opens up BUY_STRADDLE when bias is NEUTRAL and IV is LOW. Also triggers the IV_CRUSH hidden risk.',
    watchOut:
      'Earnings tag is not "buy the event." Most retail earnings long-premium trades lose to IV crush even when direction is right. If you see this tag, spread structure is nearly always safer than naked calls/puts.',
  },
  {
    name: 'Legs',
    definition:
      'The individual contracts that make up the trade. Each row shows action (BUY/SELL), side (C/P), strike, expiry, quantity, and a per-leg rationale.',
    effect:
      'Spreads have 2+ legs that must be entered together; singles have 1. BUY legs are debit; SELL legs are credit. Net cost = sum of debits − sum of credits.',
    watchOut:
      "When entering a spread manually, always submit as a single spread order — not two market orders — or you'll pay extra slippage and may only get half filled. Check each leg's expiry matches; calendars and diagonals look similar but have different risk.",
  },
  {
    name: 'Notes',
    definition: 'Free-text caveats the engine attaches: entry guidance, management rules, profit targets, risks.',
    effect:
      'Typical notes: "Take profit at 50% max gain," "Close before earnings if IV rank doubles," "Requires $X buying power per spread."',
    watchOut:
      "Notes are not scored — they are the engine's qualitative commentary. Don't substitute them for reading the Hidden Risks panel; they can miss edge cases the structured risk checks catch.",
  },
];

const LAB_HIDDEN_RISKS: LabElement[] = [
  {
    name: 'IV_CRUSH',
    values: 'HIGH',
    definition:
      'Fires when earnings are within 14 days AND IV rank >60 AND the strategy is long-premium (BUY_*). IV crush uses its own >60 IV-rank threshold, distinct from the HIGH bucket (>=70).',
    effect:
      'Post-event IV typically collapses 30–50% overnight. Long ATM premium can lose 15–30% even if the directional thesis plays out and the stock moves in your favor.',
    watchOut:
      'This is the single most common way retail loses money on earnings trades. If you see this, convert to a debit spread (caps cost), close the day before earnings, or sit out.',
  },
  {
    name: 'THETA_BURN',
    values: 'HIGH if >3%/d, MEDIUM if >2%/d',
    definition:
      'Fires when the ATM near-term option is losing more than 2% of its premium per calendar day to time decay, on a long-premium trade.',
    effect:
      'You need a fast directional move or a vol expansion just to break even. Holding through a flat weekend can wipe 4–6% before Monday open.',
    watchOut:
      "Theta accelerates non-linearly into expiry — the last 7 DTE decay more than the prior 21. If theta_pct is already >3 on day 1, don't plan to \"give it time.\"",
  },
  {
    name: 'VEGA_EXPOSURE',
    values: 'MEDIUM',
    definition:
      'Fires when ATM vega exceeds 10% of premium per 1 IV point, on a long-premium trade.',
    effect:
      'A 5-point IV drop would shave ~50% off the premium regardless of direction. Matters most on ATM long options right before a known vol-crush event (earnings, FDA, Fed).',
    watchOut:
      "Vega cuts both ways — you benefit from IV expansion, hurt by contraction. If IV rank is already high AND vega is high, you are paying peak price for peak vol sensitivity.",
  },
  {
    name: 'PIN_RISK',
    values: 'MEDIUM',
    definition:
      'Fires when a near-dated strike carries a large share of near-spot open interest, flagging it as a "magnet" price will gravitate toward at expiration.',
    effect:
      'Dealers short that strike hedge dynamically, creating mean-reversion to the pin. Your long premium can decay through expiration sitting near the magnet strike.',
    watchOut:
      "Pin risk only matters 1–3 days pre-expiration. If your DTE is >7 the magnet has time to dissolve. But for weeklies it's real — don't buy ATM the day before monthly OPEX.",
  },
  {
    name: 'LIQUIDITY',
    values: 'MEDIUM',
    definition:
      'Fires when average bid-ask spread exceeds 15% of premium across expirations (Liquidity rating = WIDE).',
    effect:
      'Round-trip slippage eats 5–10% of the trade — you pay the spread going in and coming out. Limit orders may sit unfilled or only get partial fills.',
    watchOut:
      "Never market-order in wide-spread names. Use limits at mid, walk the price in small increments. If you can't get a reasonable fill within 10 minutes, the trade isn't meant for you.",
  },
  {
    name: 'NEGATIVE_GEX',
    values: 'MEDIUM if short-premium, LOW otherwise',
    definition:
      'Fires when net dealer gamma exposure is negative, meaning dealers are short gamma and must hedge with-the-trend.',
    effect:
      'Dealer hedging amplifies realized vol — up moves get chased higher, down moves get pressed lower. Short premium (selling condors, strangles) faces fatter tails.',
    watchOut:
      'A negative GEX regime is fragile. It reverses quickly when dealers buy back hedges, often causing violent mean-reversion — bad for trend-following, dangerous for overnight short vol.',
  },
  {
    name: 'BACKWARDATION',
    values: 'LOW',
    definition:
      'Fires when front-month IV sits above back-month IV (term shape = BACKWARDATION).',
    effect:
      'The market is pricing a specific near-term event (earnings, product launch, Fed). Expect front-month IV to normalize sharply after the event — harmful to long front-month premium.',
    watchOut:
      'Backwardation is a tell that "something is priced in." If your long-call thesis was "the event will be good," the event being good may not be enough — it has to exceed the priced-in expectation.',
  },
  {
    name: 'PUT_SKEW',
    values: 'LOW',
    definition:
      'Fires when 25-delta put IV exceeds 25-delta call IV by more than 8 IV points (average across expirations).',
    effect:
      'Market is paying up for downside hedges. Protective puts are expensive; short puts carry larger-than-usual left-tail risk. Call overwrites harvest less premium than the skew suggests.',
    watchOut:
      'Elevated put skew is structural in indexes (SPY) but notable in single names. A sudden skew spike often precedes or accompanies a macro de-risking event — treat as a sentiment gauge, not just a pricing anomaly.',
  },
  {
    name: 'ASSIGNMENT_RISK',
    values: 'LOW',
    definition:
      'Fires whenever the strategy has a short leg (SELL action) — American options can be assigned any time.',
    effect:
      'Short ITM calls are most vulnerable before ex-dividend dates. Deep-ITM short puts can be assigned late in life when extrinsic value approaches 0. Early assignment flips your risk profile and ties up buying power.',
    watchOut:
      'Monitor intrinsic vs extrinsic on short legs: when extrinsic drops below the next dividend, assignment is likely. Roll or close before extrinsic disappears.',
  },
];

const LAB_VOL_ELEMENTS: LabElement[] = [
  {
    name: 'Term Shape',
    values: 'CONTANGO / BACKWARDATION / FLAT / UNKNOWN',
    definition:
      'Relationship between front-month and back-month ATM IV. CONTANGO = back > front (normal). BACKWARDATION = front > back (stressed). FLAT = within 2 IV pts.',
    effect:
      'Contango favors calendar spreads (sell front, buy back — collect front-month theta). Backwardation favors the opposite. Flat is neutral.',
    watchOut:
      "Contango is the default state. Backwardation is your flag that something is up — earnings, macro event, or panic. Don't short vol into backwardation unless you know what's driving it.",
  },
  {
    name: 'Front − Back IV (pts)',
    definition:
      'Numeric spread between front-month and back-month ATM IV, in IV percentage points.',
    effect:
      'Quantifies term-shape intensity. +5 pts backwardation is mild; +15 pts is severe and usually means a specific event is in the front contract.',
    watchOut:
      'Large negative values (-8+) = deep contango, signals vol is expected to expand — common in low-vol regimes before a catalyst. Large positive values = event risk loaded into the front contract.',
  },
  {
    name: '25-Δ Skew',
    definition:
      'Average (25-delta put IV − 25-delta call IV) across expirations, in IV points. Positive = puts richer than calls.',
    effect:
      'Measures demand for crash hedges vs upside bets. In single names, >8 pts is elevated; >15 pts is extreme. Indexes run chronically higher than single names.',
    watchOut:
      "Trades against skew (selling expensive puts, buying cheap calls) can print consistently in a range — until they don't. Skew exists because tail events happen; don't harvest skew naked.",
  },
  {
    name: 'Term Structure (per-expiry chips)',
    definition: `ATM IV for each expiration bucket (${DTE_BUCKETS}).`,
    effect:
      'Lets you see whether a specific expiry is mispriced relative to its neighbors. A spike in one bucket usually marks a known event landing in that window.',
    watchOut:
      'Look for "kinks" — one DTE bucket way above its neighbors. That is the event. Trade calendars around the kink; avoid naked long premium in the kink month.',
  },
  {
    name: 'Expected Moves',
    definition:
      'One-standard-deviation expected price range by expiry, derived from ATM straddle price. Shown as ±% and ±$.',
    effect:
      'Represents what the options market is pricing as a 68% likely range by that expiry. Useful for strike selection (OTM strikes outside the expected move are low-probability) and for position sizing.',
    watchOut:
      'Expected move is not a ceiling — by definition 32% of the time the stock moves further. And it assumes normal distribution; real returns have fat tails. Use as a budget, not a guarantee.',
  },
];

const LAB_POSITIONING_ELEMENTS: LabElement[] = [
  {
    name: 'Dealer Gamma (GEX)',
    values: 'POSITIVE / NEGATIVE / NEUTRAL + signed total',
    definition:
      'Net signed gamma exposure held by options market makers, aggregated across the chain. POSITIVE = dealers long gamma (hedge against-trend, dampens vol). NEGATIVE = dealers short gamma (hedge with-trend, amplifies vol).',
    effect:
      'POSITIVE GEX produces sticky, range-bound action — good for short-premium theta harvesting. NEGATIVE GEX produces trending, violent action — bad for short-premium, okay for directional longs.',
    watchOut:
      "GEX is computed from a model (assumed delta/gamma per contract), not observed. It is directionally useful, not precise. Regime flips (positive → negative) matter more than absolute values.",
  },
  {
    name: 'Max Pain (per expiry)',
    definition:
      'The strike where total option holder loss would be maximized at expiration — equivalently, where dealers would owe the least. Shown with distance from spot in %.',
    effect:
      'Classic "pin" theory says price gravitates toward max pain into expiration as dealers hedge. Useful for strike selection near OPEX.',
    watchOut:
      'Max pain is a weak signal on its own — think of it as a magnet, not a destination. Only material within ~3 days of expiration and only when spot is already within ~3%. Ignore for longer-dated trades.',
  },
  {
    name: 'Pin Magnets',
    definition:
      'Near-dated strikes holding a high share of near-spot open interest. Each row shows strike, DTE, call OI, put OI, and % share of near-spot OI.',
    effect:
      'These strikes act as mean-reversion magnets in the final days of their expiry — options dealers hedging the clustered OI pull spot toward the strike.',
    watchOut:
      "If you are long an ATM option and a pin magnet sits at the same strike with 20%+ share, your extrinsic decays while price gets pulled in. Consider rolling out one expiry or switching to a vertical spread.",
  },
  {
    name: 'P/C OI by Expiry',
    definition:
      'Put-to-call open interest ratio for each expiration. Values >1.2 lean bearish (more put positioning); <0.8 lean bullish.',
    effect:
      "Shows where positioning is concentrated across the term structure. A front-month P/C of 1.8 with back-months near 1.0 tells you there is a short-term hedge stack, not a structural bearish bet.",
    watchOut:
      "OI ratio measures accumulated positioning, not today's flow. A stock can have high put OI because of an old protective hedge that is actually bullish (someone buying puts to protect a long equity position). Read alongside unusual options volume.",
  },
];

const LAB_LIQUIDITY_ELEMENTS: LabElement[] = [
  {
    name: 'Liquidity Rating',
    values: 'TIGHT / MODERATE / WIDE / UNKNOWN',
    definition:
      'Categorical rating based on average bid-ask spread as % of premium across the chain. TIGHT <5%, MODERATE 5–15%, WIDE >15%.',
    effect:
      'Drives execution cost. TIGHT names fill at mid easily; WIDE names often need limit-order patience or see 5–10% round-trip slippage.',
    watchOut:
      'Weekend data can show spreads wider than real market-hours spreads because quotes are stale. Re-check spreads live before placing an order. If a name rates WIDE even during the day, size smaller or skip.',
  },
  {
    name: 'Average Spread %',
    definition: 'Mean bid-ask spread as a percentage of option premium, averaged across expirations.',
    effect:
      'Your round-trip cost floor. A 10% average spread means 10% of your premium is lost to the spread over one entry + one exit, independent of directional P&L.',
    watchOut:
      "Spreads are worse at the open (9:30–9:45 ET) and into the close, and wider on expiring contracts. Factor a 1.5× buffer for execution timing if you don't trade mid-day.",
  },
  {
    name: 'By-Expiry Breakdown',
    definition:
      'Per-expiration median spread % and total OI. Thin-OI expiries flagged in amber; wide-spread expiries flagged in red.',
    effect:
      'Lets you pick the most liquid expiry even when the overall name is "MODERATE." Often a weekly is tight while a monthly two months out is wide, or vice versa.',
    watchOut:
      "If your strategy's target DTE lands on a thin-OI expiry, consider rolling to the next standard expiration (third-Friday monthly) — liquidity concentrates there even if DTE moves by a week.",
  },
];

const LAB_GREEKS_ELEMENTS: LabElement[] = [
  {
    name: 'Delta (Δ)',
    values: '-1 to +1',
    definition:
      'Rate of change of option price per $1 move in the underlying. Also interpretable as approximate probability of expiring ITM.',
    effect:
      '0.50 = ATM; 0.30 = slightly OTM; 0.70 = ITM. Directional exposure scales linearly near ATM. The strike recommender filters by delta for each risk profile.',
    watchOut:
      'Delta is dynamic — a 0.30 delta call at entry can become 0.60 if the stock rallies. Position delta + gamma = your real directional exposure. Re-check delta before adding to winners.',
  },
  {
    name: 'Gamma (Γ)',
    definition:
      'Rate of change of delta per $1 move in the underlying. Highest ATM and peaks as expiration approaches.',
    effect:
      'High gamma = delta accelerates fast. Long gamma benefits from big moves in either direction (curvature pays). Short gamma (selling ATM) exposes you to runaway losses.',
    watchOut:
      'Gamma spikes in the last 7 DTE. Short-gamma positions entering that window without hedges can explode — this is why iron condors are closed at ~21 DTE by mechanical sellers.',
  },
  {
    name: 'Theta (Θ/d)',
    definition:
      'Dollar decay per calendar day, all else equal. Shown as $/day; also see theta_pct (percentage of premium per day).',
    effect:
      'Long options pay theta (negative); short options collect it (positive). ATM theta is highest. The greeks row highlights theta in red when it exceeds ~3% of premium/day.',
    watchOut:
      'Theta burns weekends and holidays too. A Friday-bought weekly loses 3 days of decay by Monday even though markets were closed. Factor this into weekend hold decisions.',
  },
  {
    name: 'Vega (V)',
    definition:
      'Dollar change in option price per 1-point change in IV (per 1% IV). Higher for longer-dated options and ATM strikes.',
    effect:
      'Long options are long vega (benefit from IV rising); short options are short vega. Vega is your IV P&L.',
    watchOut:
      'Vega dominates P&L on longer-dated positions — a 20-point IV drop on a 90-DTE long call can wipe the trade even with favorable price action. Match vega to your view on vol.',
  },
  {
    name: 'Rho (ρ)',
    definition:
      'Dollar change in option price per 1-point change in risk-free rate. Higher for longer-dated options; calls positive, puts negative.',
    effect:
      'In normal-rate regimes, essentially ignorable. Matters on LEAPS (365+ DTE) or when rate expectations shift violently.',
    watchOut:
      "Fed pivot days can move rho materially on long-dated positions. If you are holding LEAPS through FOMC, know which direction rho helps you.",
  },
  {
    name: 'Vanna (Vn)',
    definition: 'Rate of change of delta per 1-point change in IV. Captures cross-sensitivity between direction and vol.',
    effect:
      'High positive vanna means rising IV pushes delta up (your call gets more bullish as vol expands). Critical for dealer hedging flows — "vanna rallies" happen when vol drops and dealers buy to rebalance.',
    watchOut:
      'Vanna is second-order — small absolute values. Matters most for structurally short-vol books (dealers, vol funds). Retail traders rarely need to trade it directly, but it explains why vol-crush days sometimes see paradoxical rallies.',
  },
  {
    name: 'Charm (Ch)',
    definition: 'Rate of change of delta per day (delta decay). Shows how your directional exposure drifts as time passes.',
    effect:
      'OTM options lose delta over time (charm negative); ITM options gain it. Explains why a 0.25 delta OTM call can still look OTM even after a small rally if time passed too.',
    watchOut:
      "Pronounced in the last 2 weeks. A weekly OTM call bought Monday can have materially less delta Friday even if the stock didn't move — you have lost both price and participation rate.",
  },
  {
    name: 'Vomma (Vm)',
    definition: 'Rate of change of vega per 1-point change in IV. Second derivative of option price with respect to vol.',
    effect:
      'Positive vomma means vega expands as IV rises — long premium positions benefit more from each additional IV point in a vol spike. Captures the convexity of vol exposure.',
    watchOut:
      'Mostly a vol-trader metric. For single-name directional trading, watch vega first; vomma only bites on large vol moves (>10 IV pts), typically during crashes or earnings.',
  },
];

const LAB_GREEKS_COLUMNS: LabElement[] = [
  {
    name: 'Exp / DTE',
    definition: 'Expiration date and days to expiry for each row in the greeks table.',
    effect:
      `Each row corresponds to one expiration bucket from the DTE spread (up to 8 expirations: ${DTE_BUCKETS}).`,
    watchOut:
      "Greeks change dramatically across the term — don't compare a 7-DTE gamma to a 90-DTE gamma directly. Pick the row that matches your target DTE.",
  },
  {
    name: 'ATM Strike / ATM IV',
    definition:
      'The at-the-money strike for that expiry and its implied volatility. ATM IV falls back to the median of near-ATM strikes when direct-ATM contract quotes are stale.',
    effect:
      'Anchors the greeks row — all greeks are computed at the ATM strike, which represents the most liquid / most-used reference contract.',
    watchOut:
      'On weekends or illiquid names, ATM IV can look absurdly low or high if a single stale quote sneaks through the fallback. Compare to IV rank and neighboring expirations to sanity-check.',
  },
  {
    name: 'EM% (Expected Move)',
    definition: 'One-standard-deviation expected move by this expiry, as a % of spot, derived from the ATM straddle.',
    effect:
      'Your probability-weighted price range. OTM strikes outside the EM% are low-probability; ATM straddles are priced for exactly EM% moves to break even.',
    watchOut:
      "EM% grows with √time — a 30-DTE EM of 8% doesn't mean the stock moves 8% in 30 days; it means 68% of scenarios end within ±8%. Sizing a trade on EM requires matching your thesis timeframe to the expiry.",
  },
];

const LAB_RATIONALE_ELEMENT: LabElement = {
  name: 'Rationale',
  definition:
    'A narrative paragraph generated by the engine that walks through why this verdict was chosen given the bias, IV regime, earnings proximity, and structure of the chain.',
  effect:
    "Bridges the numeric dashboards into plain English. Useful for sanity-checking that the verdict matches the raw inputs you would expect.",
  watchOut:
    'Rationale is a summary, not an independent signal. If it contradicts what you see in the Hidden Risks panel, trust the structured risks — those are rule-based and exhaustive; rationale is a generated overview.',
};

export default function OptionsLabTab() {
  return (
    <div className="space-y-8">
      <div>
        <p className="text-sm text-text-secondary">
          The Options Lab runs a per-ticker deep analysis combining greeks, volatility structure,
          dealer positioning, and liquidity — then picks a single strategy verdict. This reference
          walks through every element in the detail panel: what it is, how it affects the verdict,
          and what to watch out for when acting on it.
        </p>
      </div>

      <LabSubsection
        title="Header & Summary Tiles"
        description="Top of the detail panel: ticker, directional bias, and the three summary tiles (Price / IV Rank / IV %tile) that drive strategy selection."
        elements={LAB_HEADER_ELEMENTS}
      />

      <LabSubsection
        title="Strategy Recommendation"
        description="The verdict card — the engine's single best strategy, target expiry, IV bucket, earnings tag, legs, and notes."
        elements={LAB_STRATEGY_ELEMENTS}
      />

      <VerdictMatrixTable />

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-1">Hidden Risks</h3>
        <p className="text-xs text-text-secondary mb-3">
          Rule-based structural risks attached to every analysis. Each risk has a severity (HIGH / MEDIUM / LOW),
          a short code, and a reason. Not every risk fires on every trade — they only appear when their trigger conditions are met.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          {LAB_HIDDEN_RISKS.map((r) => (
            <LabElementCard key={r.name} el={r} />
          ))}
        </div>
      </div>

      <LabSubsection
        title="Volatility Structure"
        description="How the options market is pricing forward volatility across the term structure and skew curve."
        elements={LAB_VOL_ELEMENTS}
      />

      <LabSubsection
        title="Positioning"
        description="Where the open interest sits — dealer gamma exposure, max pain, pin magnets, and put/call OI ratios by expiry."
        elements={LAB_POSITIONING_ELEMENTS}
      />

      <LabSubsection
        title="Liquidity"
        description="Execution quality metrics. Tells you whether you can actually trade the contracts the engine picked without giving up alpha to spreads."
        elements={LAB_LIQUIDITY_ELEMENTS}
      />

      <LabSubsection
        title="ATM Call Greeks by Expiry — Columns"
        description="Structure of the table: each row is one expiration; each column is a greek or reference value at the ATM strike."
        elements={LAB_GREEKS_COLUMNS}
      />

      <LabSubsection
        title="ATM Call Greeks by Expiry — Greeks"
        description="The greeks themselves. First-order (Δ, Γ, Θ, V, ρ) drive most of your P&L; second-order (Vanna, Charm, Vomma) describe how the first-order greeks evolve."
        elements={LAB_GREEKS_ELEMENTS}
      />

      <div>
        <h3 className="text-base font-semibold text-text-primary mb-1">Rationale</h3>
        <p className="text-xs text-text-secondary mb-3">The narrative block at the bottom of the panel.</p>
        <LabElementCard el={LAB_RATIONALE_ELEMENT} />
      </div>

      <div className="bg-card border border-border rounded-lg p-4">
        <h3 className="text-sm font-semibold text-text-primary mb-2">How to read the panel in order</h3>
        <ol className="list-decimal pl-5 text-xs text-text-secondary space-y-1.5">
          <li><strong className="text-text-primary">Start with Bias + IV Rank + IV %tile</strong> — they set the strategy universe.</li>
          <li><strong className="text-text-primary">Read the Verdict and Target DTE</strong> — that is the engine's pick.</li>
          <li><strong className="text-text-primary">Scan Hidden Risks</strong> — a single HIGH risk is often reason enough to decline the trade.</li>
          <li><strong className="text-text-primary">Check Volatility Structure</strong> — is vol priced for an event? Is it contango or backwardation? Does that match the verdict's IV bucket?</li>
          <li><strong className="text-text-primary">Check Positioning</strong> — negative GEX or nearby pin magnets can invalidate an otherwise-clean setup.</li>
          <li><strong className="text-text-primary">Verify Liquidity</strong> — if WIDE, shrink your size or skip.</li>
          <li><strong className="text-text-primary">Only then read the Greeks table</strong> — confirm the target DTE row has tolerable theta, reasonable vega, and a delta that matches your risk profile.</li>
          <li><strong className="text-text-primary">Rationale last</strong> — as a sanity check, not a decision driver.</li>
        </ol>
      </div>
    </div>
  );
}
