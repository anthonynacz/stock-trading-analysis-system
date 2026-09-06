import { BRAND, DocTable, IV_BUCKETS, PlaybookCard, SectionHeading, type Playbook } from './shared';

/* ── Trading Guide ──────────────────────────────────────────────────────── */

function GuideCard({ title, children, accent = '#58a6ff' }: { title: string; children: React.ReactNode; accent?: string }) {
  return (
    <div className="bg-card border border-border rounded-lg p-5" style={{ borderLeftColor: accent, borderLeftWidth: 3 }}>
      <h4 className="text-sm font-semibold text-text-primary mb-3">{title}</h4>
      {children}
    </div>
  );
}

const WEEKLY_SIGNAL_ROWS = [
  { signal: 'T1/T2 Upgrade', value: 'Excellent', color: '#2ea043', why: 'Institutional repricing happens within 1-3 days. Price gap + follow-through.' },
  { signal: 'Earnings Beat + Raised', value: 'Excellent', color: '#2ea043', why: 'Post-earnings drift is strongest in the first 5 trading days.' },
  { signal: 'Unusual Call Volume', value: 'Excellent', color: '#2ea043', why: 'Smart money positioning before a known catalyst. Acts fast.' },
  { signal: 'Reversal Setup', value: 'Good', color: '#56d364', why: 'Oversold bounces are violent and fast — but need RSI + support confirmation.' },
  { signal: 'Short Squeeze Setup', value: 'Good', color: '#56d364', why: 'Squeezes are explosive. High reward but timing is unpredictable.' },
  { signal: 'Positive News Sentiment', value: 'Moderate', color: '#d29922', why: 'News drives 1-2 day moves but fades. Only strong if sentiment > 0.5.' },
  { signal: 'Above 50d SMA', value: 'Moderate', color: '#d29922', why: 'Trend confirmation, but doesn\'t create urgency. Better as a filter.' },
  { signal: 'Near 52-Week High', value: 'Low', color: '#f85149', why: 'Breakout potential exists but moves are small. Poor risk/reward for weeklies.' },
  { signal: 'Below Consensus PT', value: 'Low', color: '#f85149', why: 'Value convergence takes weeks/months. Not a weekly catalyst.' },
  { signal: 'Sector Tailwind', value: 'Low', color: '#f85149', why: 'Diffuse. Sector rotation is a multi-week theme, not a weekly trigger.' },
];

const FEATURE_ROWS = [
  { feature: 'Recommendation Cards', use: 'Trade selection. Filter by STRONG_BUY/STRONG_SELL for weeklies. Read the signal bullets — they tell you WHY.' },
  { feature: 'Day-by-Day View', use: 'Entry timing. Rising conviction = enter. Stable = hold. Declining = exit or skip.' },
  { feature: 'Strike Recommender', use: 'Contract selection. Use the budget filter to find affordable contracts. Conservative for weeklies, Moderate for 2–7 week swings.' },
  { feature: 'Strike Scanner', use: 'Batch scanning. Run "Scan Watchlist" to see strike recommendations for all tickers at once. Saves time vs clicking each one.' },
  { feature: 'Options Flow (Ticker Detail)', use: 'IV assessment. Check IV Rank before entering. High IV → use spreads. Also check Put/Call ratio for sentiment confirmation.' },
  { feature: 'Trends', use: 'Visual confirmation. Price/conviction chart shows the relationship between price action and signal quality. Divergences are informative.' },
  { feature: 'News', use: 'Catalyst verification. When a stock jumps to STRONG_BUY, check the news for the driver. Event-driven signals are the highest-quality for weeklies.' },
  { feature: 'Upcoming Catalysts', use: 'Earnings avoidance/targeting. Know when earnings are. Either play the run-up and exit before, or use straddles if you want to play the event.' },
  { feature: 'Research Page', use: 'Off-watchlist analysis. Test any ticker before committing capital. Good for stocks from your own screening or tips.' },
  { feature: 'Options Lab', use: 'Deep strategy verdict + hidden risks (IV crush, theta, term shape) for one ticker. See the Options Lab tab.' },
  { feature: 'Industries', use: 'Sector-level BUY/HOLD/SELL. Use as a tailwind/headwind filter before taking a single-name trade.' },
  { feature: 'Scanner', use: 'Multi-bagger candidates on a 3–12 month horizon. A separate, longer-dated thesis — not a weekly options input.' },
  { feature: 'Positions', use: 'Log a trade via "Open Position" on the ticker detail panel. Tracks P&L against the recommendation that triggered it.' },
  { feature: 'Performance', use: 'Hit rates per signal at T+1/5/20. Use it to tune Signal Weights in Settings toward what actually works.' },
  { feature: 'Rotate out', use: 'Swap weak watchlist names for fresh candidates. Names with strong imminent potential (e.g. PRE_POSITION, imminent catalyst) are hard-blocked.' },
];

type WeeklySignalRow = (typeof WEEKLY_SIGNAL_ROWS)[number];
type FeatureRow = (typeof FEATURE_ROWS)[number];

interface EntryRow {
  pill: string;
  label: string;
  /** Small note rendered under the pill (e.g. "overrides"). */
  note?: string;
  condition: string;
  meaning: string;
}

const ENTRY_ROWS: EntryRow[] = [
  {
    pill: 'bg-blue-900/40 text-blue-400',
    label: 'PRE_POSITION',
    condition: 'Conviction ≥ 30 AND inside the pre-earnings ACTIVE window (T-14 to T-0)',
    meaning: "Get in before the catalyst fires. The earnings event hasn't happened yet — you're positioning for the anticipated move.",
  },
  {
    pill: 'bg-blue-900/40 text-blue-400',
    label: 'PRE_POSITION',
    condition: 'Conviction ≥ 25 AND a leading positioning signal fired (Analyst Revision Cluster, Smart Money Positioning, Insider Buy Cluster) — checked after the catalyst branch',
    meaning: 'Smart money is positioning ahead of a catalyst that may be weeks out. This is the branch that fires PRE_POSITION well before earnings.',
  },
  {
    pill: 'bg-amber-900/40 text-amber-400',
    label: 'REACTIVE',
    condition: 'Conviction ≥ 30 AND stock already moved > 3%',
    meaning: "The catalyst already triggered a move — you'd be chasing. Entry is still valid but use smaller size and tighter stops.",
  },
  {
    pill: 'bg-accent-900/40 text-accent-400',
    label: 'WAIT',
    condition: "Conviction 15-29, or conviction ≥ 30 but stock hasn't moved yet",
    meaning: 'Signals are positive but need confirmation — a pullback entry, volume breakout, or another signal stacking before committing.',
  },
  {
    pill: 'bg-zinc-800 text-text-secondary',
    label: 'HOLD',
    condition: 'Conviction < 15',
    meaning: 'Not actionable. Mixed or weak signals — no edge for entry. Sit tight or look elsewhere.',
  },
  {
    pill: 'bg-red-900/40 text-red-400',
    label: 'STALE MOVE GATE',
    note: 'overrides',
    condition: "> 5% move today in the rec's own direction (a >5% drop does not gate a BUY)",
    meaning: 'BUY and SELL are downgraded to HOLD and entry is forced to WAIT regardless of conviction — the move is already priced in.',
  },
];

const SIZING_ROWS = [
  { level: 'LOW', levelClass: 'text-green-400', perTrade: '3-5%', open: '15-20%' },
  { level: 'MEDIUM', levelClass: 'text-amber-400', perTrade: '1-2%', open: '8-10%' },
  { level: 'HIGH', levelClass: 'text-red-400', perTrade: '0.5-1%', open: '3-5%' },
];
type SizingRow = (typeof SIZING_ROWS)[number];

type DecisionCell = [text: string, className?: string];
interface DecisionRow {
  action: string;
  color: string;
  low: DecisionCell;
  mid: DecisionCell;
  high: DecisionCell;
}

const DECISION_ROWS: DecisionRow[] = [
  { action: 'STRONG BUY', color: '#2ea043', low: ['Buy Calls'], mid: ['Buy Calls (smaller)'], high: ['Bull Call Spread'] },
  { action: 'BUY', color: '#56d364', low: ['Buy Calls (small)'], mid: ['Sell CSP or Skip', 'text-amber-400'], high: ['Skip', 'text-red-400'] },
  { action: 'HOLD', color: '#d29922', low: ['No trade', 'text-red-400'], mid: ['No trade', 'text-red-400'], high: ['No trade', 'text-red-400'] },
  { action: 'SELL', color: '#f85149', low: ['Buy Puts (small) or Skip', 'text-amber-400'], mid: ['Skip', 'text-red-400'], high: ['Skip', 'text-red-400'] },
  { action: 'STRONG SELL', color: '#da3633', low: ['Buy Puts'], mid: ['Bear Put Spread'], high: ['Call Credit Spread'] },
];

const decisionCell = (cell: DecisionCell) => <span className={cell[1] ?? 'text-text-primary'}>{cell[0]}</span>;

const PLAYBOOKS: Playbook[] = [
  {
    badge: { kind: 'action', action: 'STRONG_BUY' },
    iv: 'LOW',
    risk: 'LOW',
    title: 'Buy Calls (Directional)',
    fields: [
      ['Strategy', 'Buy ATM or slightly OTM calls. This is the highest-conviction, lowest-risk setup — strong signals with cheap premium.'],
      ['Strike', 'Use the Conservative profile (7–30 DTE) from Strike Recommender and pick the nearest expiry; Moderate starts at 14 DTE so it will not return true weeklies. Delta 0.50-0.60 for weeklies (the Conservative band is 0.50-0.80).'],
      ['DTE', "7–12 days. Next Friday expiry (Conservative's floor is 7 DTE)."],
      ['Position size', 'Up to 3-5% of portfolio per trade.'],
      ['Exit', 'Take profit at 50-100% gain. Stop loss at 40-50% of premium paid. Exit by Wednesday if no movement.'],
    ],
  },
  {
    badge: { kind: 'action', action: 'STRONG_BUY' },
    iv: 'HIGH',
    risk: 'MEDIUM',
    title: 'Bull Call Spread (Defined Risk)',
    fields: [
      ['Strategy', "Buy ATM call, sell OTM call 3-5% above. High IV makes naked calls expensive — the spread neutralizes vega so IV crush doesn't hurt you."],
      ['Width', '$2-5 wide on stocks under $150, $5-10 on higher-priced names. Wider = more profit potential but costs more.'],
      ['DTE', '7-14 days. Gives time for the move without excessive theta bleed.'],
      ['Max risk', 'Net debit paid (difference between premiums). Target 2:1 reward-to-risk.'],
      ['Exit', "Close at 50-70% of max profit. Don't hold to expiry — gamma risk accelerates."],
    ],
  },
  {
    badge: { kind: 'action', action: 'BUY' },
    iv: 'LOW',
    risk: 'MEDIUM',
    title: 'Buy Calls (Smaller Size)',
    fields: [
      ['Strategy', 'Same as directional calls but with reduced conviction. Signals are aligned but not overwhelming — one headwind could flip it.'],
      ['Strike', "ATM only (delta 0.45-0.55). Don't go OTM with moderate conviction — you need the move to be smaller to profit."],
      ['Position size', '1-2% of portfolio. Half the size of a STRONG_BUY play.'],
      ['Confirmation', 'Check Day-by-Day — only enter if conviction is rising or stable. Skip if conviction dropped today.'],
      ['Exit', 'Take profit at 30-50% gain. Tighter stop at 30% loss. Exit by Tuesday if flat.'],
    ],
  },
  {
    badge: { kind: 'action', action: 'BUY' },
    iv: 'HIGH',
    risk: 'HIGH',
    title: 'Cash-Secured Put / Skip',
    fields: [
      ['Strategy', "Moderate conviction + expensive premiums = poor setup for buying options. Consider selling instead: sell a cash-secured put below support to collect inflated premium. If you don't sell puts, skip this trade."],
      ['Strike', 'Sell put at 5-7% below current price (OTM). You want the stock but at a discount.'],
      ['DTE', 'Current week. Maximum theta decay works in your favor.'],
      ['Requirement', "Must have cash/margin to cover assignment. Only on stocks you'd own at that price."],
      ['Exit', 'Let expire worthless for full premium, or buy back at 80% profit.'],
    ],
  },
  {
    badge: { kind: 'action', action: 'STRONG_SELL' },
    iv: 'LOW',
    risk: 'MEDIUM',
    title: 'Buy Puts (Bearish Directional)',
    fields: [
      ['Strategy', 'Strong bearish conviction with cheap puts. Mirror of the bullish playbook. Works best when drawdown signals are stacking.'],
      ['Strike', 'ATM or slightly OTM puts (delta -0.50 to -0.55). Use the Conservative profile from Strike Recommender for current/next-week expiries; Moderate starts at 14 DTE.'],
      ['Best when', 'Multiple drawdown signals active, negative news sentiment, unusual put volume, and the stock is below both 50d and 200d SMA.'],
      ['Caution', 'Check for reversal signals. If "Oversold Bounce Setup" or "Strong Reversal Setup" is also firing, the stock may be bottoming — skip.'],
      ['Exit', 'Take profit at 50-80% gain. Stop at 40% loss.'],
    ],
  },
  {
    badge: { kind: 'action', action: 'STRONG_SELL' },
    iv: 'HIGH',
    risk: 'HIGH',
    title: 'Bear Put Spread / Call Credit Spread',
    fields: [
      ['Strategy', 'Bearish conviction but IV is elevated — naked puts are overpriced. Use a bear put spread (buy ATM put, sell lower put) or a call credit spread (sell ATM call, buy higher call) to collect premium while IV is rich.'],
      ['Advantage', 'Credit spreads profit from both the move AND IV crush. If the stock stays flat or drops, you win.'],
      ['Width', '$2-5 wide. Max loss = width minus credit received.'],
      ['DTE', '7-14 days. Let theta work for you on the credit side.'],
      ['Exit', 'Buy back at 50% of max profit. Never hold credit spreads to expiry — assignment risk.'],
    ],
  },
  {
    badge: { kind: 'class', className: 'bg-accent-900/30 text-accent-400', label: 'REVERSAL SETUP' },
    note: 'Any IV',
    title: 'Oversold Bounce Play',
    fields: [
      ['Trigger', '"Strong Reversal Setup" or "Oversold Bounce Setup" signal active. RSI < 30, price near 200d SMA or 52-week low, selling exhaustion.'],
      ['Strategy', "Buy slightly OTM calls (delta 0.30-0.40) for 10-14 DTE. The bounce usually happens within 2-3 days but needs time to develop. IV is usually elevated after a crash, so use a call debit spread if IV rank ≥ 70 (the engine's HIGH bucket)."],
      ['Size', '1-2% of portfolio. Reversal plays are high-reward but binary — the stock either bounces or keeps falling.'],
      ['Confirmation', "Wait for a green day with volume. Don't front-run the bounce — let the stock prove it's turning."],
      ['Exit', 'Take profit at 80-150% gain (bounces are fast). Hard stop at 50% loss.'],
    ],
  },
  {
    badge: { kind: 'class', className: 'bg-amber-900/30 text-amber-400', label: 'EARNINGS CATALYST' },
    note: 'Pre-Earnings',
    title: 'Earnings Run-Up / Straddle',
    fields: [
      ['Trigger', '"Active Catalyst Window" signal firing, earnings date in 1-5 days. Check Upcoming Catalysts for exact date and time (BMO/AMC).'],
      ['Run-up play', "If conviction is BUY or higher AND the stock hasn't run up yet, buy calls 5-7 DTE. Sell BEFORE earnings — don't hold through the event. You're playing the anticipation, not the result."],
      ['Straddle play', 'If you want to play the earnings event itself, buy a straddle (ATM call + ATM put) for the weekly expiry right after earnings. You need a big move in either direction to overcome the IV crush. Only do this if historical earnings moves for the ticker are > the implied move (check options flow section for current IV rank).'],
      ['Caution', 'IV peaks right before earnings. Buying options pre-earnings means paying maximum premium. The stock must move more than the "expected move" priced into options for you to profit.'],
    ],
  },
];

export default function TradingGuideTab() {
  return (
    <div className="space-y-8">
      {/* ── Morning Routine ──────────────────────────────────────── */}
      <div>
        <SectionHeading
          size="lg"
          title="Daily Workflow"
          blurb={
            <>
              {BRAND}'s pipeline runs automatically before market open. Here's how to use its output for weekly options.
            </>
          }
        />
        <div className="space-y-3">
          <GuideCard title="1. Check Pipeline Status (Pre-Market)" accent="#58a6ff">
            <p className="text-sm text-text-secondary">
              The pipeline finishes by ~10:30 ET. Open the Dashboard and hover the status pill (top right of the bar).
              OK means database and scheduler are up; the tooltip shows when recommendations were last updated and
              when the last news scan ran. If it's stale, Refresh is tier-gated: Pro+ runs the intraday phases;
              Premium/Admin run the full pipeline.
            </p>
          </GuideCard>

          <GuideCard title="2. Review Watchlist Changes" accent="#58a6ff">
            <p className="text-sm text-text-secondary">
              Look at the green (NEW_ENTRANT) and red (REMOVED) badges at the top. New entrants just scored
              high enough to join — they often have fresh catalysts. Removed tickers may have deteriorating
              signals. Don't chase removed tickers; focus attention on new entrants and existing holdings.
            </p>
          </GuideCard>

          <GuideCard title="3. Scan Recommendations" accent="#58a6ff">
            <div className="text-sm text-text-secondary space-y-2">
              <p>
                Use the <span className="font-semibold text-text-primary">Top conviction / Recently revised</span> toggle in the Recommendations header.
                REV chips mark rows rescored in the last 4h — hover one to see the headline that caused it.
                For weekly options, focus on (conviction bands per the Classification tab):
              </p>
              <ul className="list-disc list-inside space-y-1 ml-2">
                <li><span className="font-semibold" style={{ color: '#2ea043' }}>STRONG_BUY</span> — Primary trade candidates. Multiple signals aligned.</li>
                <li><span className="font-semibold" style={{ color: '#56d364' }}>BUY</span> — Secondary candidates. May need confirmation from Day-by-Day trend.</li>
                <li><span className="font-semibold" style={{ color: '#f85149' }}>STRONG_SELL</span> — Put candidates if you trade bearish.</li>
              </ul>
              <p>
                Ignore HOLD — insufficient edge for weekly options where theta decay is aggressive.
              </p>
            </div>
          </GuideCard>

          <GuideCard title="4. Use Day-by-Day for Confirmation" accent="#58a6ff">
            <div className="text-sm text-text-secondary space-y-2">
              <p>
                Click a ticker to open the detail panel. The Day-by-Day view shows conviction trajectory.
                Look for:
              </p>
              <ul className="list-disc list-inside space-y-1 ml-2">
                <li><span className="font-semibold text-green-400">Rising conviction</span> — Score increasing over 2-3 days. Strong entry signal.</li>
                <li><span className="font-semibold text-green-400">Stable high conviction</span> — Consistently 50+. Reliable for weeklies.</li>
                <li><span className="font-semibold text-amber-400">Spike from nowhere</span> — Jumped from HOLD to STRONG_BUY in one day. Could be a catalyst event — verify with the signal list before entering.</li>
                <li><span className="font-semibold text-red-400">Declining conviction</span> — Score dropping day over day. Avoid even if still BUY.</li>
              </ul>
            </div>
          </GuideCard>
        </div>
      </div>

      {/* ── Reading the Signals ──────────────────────────────────── */}
      <div>
        <SectionHeading
          size="lg"
          title="Signal Quality for Weeklies"
          blurb={
            <>
              Not all signals are equal for short-dated positions. Weekly options need catalysts that move price <span className="font-semibold text-text-primary">this week</span>.
            </>
          }
        />
        <DocTable<WeeklySignalRow>
          rows={WEEKLY_SIGNAL_ROWS}
          rowKey={(row) => row.signal}
          columns={[
            { key: 'signal', header: 'Signal Type', className: 'text-text-primary' },
            {
              key: 'value',
              header: 'Weekly Value',
              align: 'center',
              headerClass: 'w-24',
              className: 'font-semibold text-xs',
              render: (row) => <span style={{ color: row.color }}>{row.value}</span>,
            },
            { key: 'why', header: 'Why', className: 'text-text-secondary' },
          ]}
        />
      </div>

      {/* ── Scenario Playbooks ───────────────────────────────────── */}
      <div>
        <SectionHeading
          size="lg"
          title="Scenario Playbooks"
          blurb={
            <>
              Match {BRAND}'s output to the right options strategy. Each scenario maps a signal + IV combination to a specific play.
            </>
          }
        />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {PLAYBOOKS.map((p) => (
            <PlaybookCard key={p.title} {...p} />
          ))}
        </div>
      </div>

      {/* ── Entry Strategies ────────────────────────────────────── */}
      <div>
        <SectionHeading
          size="lg"
          title="Entry Strategies"
          blurb={
            <>
              Each recommendation includes an entry strategy that tells you <span className="font-semibold text-text-primary">when and how</span> to enter,
              not just <span className="font-semibold text-text-primary">what</span> to trade. The strategy is determined by conviction strength, whether the stock has already moved, and earnings proximity.
            </>
          }
        />

        <DocTable<EntryRow>
          className="mb-6"
          rows={ENTRY_ROWS}
          rowKey={(r) => `${r.label}-${r.condition}`}
          columns={[
            {
              key: 'label',
              header: 'Strategy',
              headerClass: 'w-36',
              render: (r) => (
                <>
                  <span className={`px-2 py-0.5 rounded text-xs font-bold ${r.pill}`}>{r.label}</span>
                  {r.note && <div className="text-[10px] text-text-secondary mt-1">{r.note}</div>}
                </>
              ),
            },
            { key: 'condition', header: 'Condition', className: 'text-text-secondary' },
            { key: 'meaning', header: 'What It Means', className: 'text-text-secondary' },
          ]}
        />
        <p className="text-[10px] text-text-secondary -mt-4 mb-6">
          Tickers whose latest rec is PRE_POSITION (among other strong-imminent-potential reasons) cannot be rotated out of the watchlist.
        </p>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GuideCard title="How to Use Entry Strategies" accent="#58a6ff">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">PRE_POSITION:</span> Set your limit orders the night before or pre-market. Queue them to fire at the open. Best entry for weeklies — you're ahead of the catalyst.</p>
              <p><span className="font-semibold text-text-primary">REACTIVE:</span> Don't market-order at the open — spreads are wide in the first 10 minutes. Wait until 9:45-10:00 AM for spreads to tighten. Size down to 50-75% of normal. Use tighter stops (30% vs 50%).</p>
              <p><span className="font-semibold text-text-primary">WAIT:</span> Add to your watchlist and monitor. Enter only when conviction rises above 30 on a subsequent day, or when a confirming signal fires (e.g., unusual call volume, T1 upgrade). Don't force it.</p>
              <p><span className="font-semibold text-text-primary">HOLD:</span> No action. Check back tomorrow — conviction may shift as new data arrives.</p>
            </div>
          </GuideCard>

          <GuideCard title="Entry Strategy + Action Matrix" accent="#58a6ff">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-green-400">STRONG_BUY + PRE_POSITION</span> — Best possible setup. High conviction before an earnings catalyst. Enter aggressively with full size.</p>
              <p><span className="font-semibold text-green-400">STRONG_BUY + REACTIVE</span> — Strong signals but the stock has moved. Still tradeable — use spreads or smaller size to manage the chase risk.</p>
              <p><span className="font-semibold text-green-400">BUY + WAIT</span> — Moderate conviction, stock is flat. The signal exists but isn't screaming. Wait for confirmation or a pullback to support before entering.</p>
              <p><span className="font-semibold text-amber-400">BUY + REACTIVE</span> — Moderate conviction and already moved. Worst risk/reward of the actionable setups. Consider skipping unless Day-by-Day shows rising trend.</p>
              <p><span className="font-semibold text-red-400">SELL/STRONG_SELL + HOLD</span> — Bearish but not actionable for entry. Only trade these if you actively play puts and conviction is below -30.</p>
            </div>
          </GuideCard>
        </div>
      </div>

      {/* ── Risk Management ──────────────────────────────────────── */}
      <div>
        <SectionHeading size="lg" title="Risk Management Rules" blurb="Weekly options are high-leverage instruments. These rules keep drawdowns survivable." />
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <GuideCard title="Position Sizing" accent="#d29922">
            <div className="text-xs text-text-secondary space-y-2">
              <DocTable<SizingRow>
                size="xs-dense"
                rows={SIZING_ROWS}
                rowKey={(r) => r.level}
                columns={[
                  { key: 'level', header: 'Risk Level', render: (r) => <span className={`${r.levelClass} font-medium`}>{r.level}</span> },
                  { key: 'perTrade', header: 'Max Per Trade', align: 'center', className: 'text-text-primary' },
                  { key: 'open', header: 'Max Open', align: 'center', className: 'text-text-primary' },
                ]}
              />
              <p>"Max Open" = total portfolio allocation in that risk category at once. Never have more than 25% of portfolio in weekly options total.</p>
            </div>
          </GuideCard>

          <GuideCard title="The Greeks for Weeklies" accent="#d29922">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">Theta (time decay):</span> Accelerates dramatically in the last 5 days. Monday calls lose ~20% of remaining extrinsic value per day. Enter early in the week, exit by Wednesday/Thursday unless momentum is strong.</p>
              <p><span className="font-semibold text-text-primary">Delta (directional exposure):</span> Stay 0.35-0.60 for weeklies. Below 0.30, you need a huge move. The Strike Recommender's Conservative profile (7–30 DTE) returns the 0.50-0.60 end; for 0.35-0.49 you need Moderate, whose 14-DTE floor means next-week expiry at the earliest.</p>
              <p><span className="font-semibold text-text-primary">Gamma (delta acceleration):</span> Extremely high for ATM weeklies. Works for you when the stock moves in your direction, violently against you when it reverses. This is why stop losses matter.</p>
              <p><span className="font-semibold text-text-primary">Vega (IV sensitivity):</span> Check IV rank in the ticker detail panel. Rule of thumb using the engine's buckets: IV rank &ge; 70 — use spreads; &lt; 30 — naked calls/puts are fine; 30–69 — judgment call, check the Options Lab verdict.</p>
            </div>
          </GuideCard>

          <GuideCard title="Stop Loss & Profit Taking" accent="#d29922">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">Hard stop:</span> Exit at 40-50% loss of premium. Weekly options can go to zero — don't "average down" on weeklies. Ever.</p>
              <p><span className="font-semibold text-text-primary">Time stop:</span> If the position is flat by Wednesday and you hold Friday expiry, close. Theta will eat the rest. Roll to next week if conviction is still high.</p>
              <p><span className="font-semibold text-text-primary">Profit taking:</span> Scale out. Sell half at 50% gain, let the rest run with a trailing stop. On a 100%+ gain, sell 75% and let a small piece ride.</p>
              <p><span className="font-semibold text-text-primary">Never hold to expiry.</span> Close or roll by Thursday afternoon. Gamma risk at expiry creates unpredictable P&L swings.</p>
            </div>
          </GuideCard>

          <GuideCard title="When NOT to Trade" accent="#f85149">
            <div className="text-xs text-text-secondary space-y-2">
              <p><span className="font-semibold text-text-primary">HOLD recommendations:</span> No edge. The system sees mixed signals. Wait.</p>
              <p><span className="font-semibold text-text-primary">Declining conviction trend:</span> Even if today is BUY, a 3-day downtrend in conviction means the setup is deteriorating.</p>
              <p><span className="font-semibold text-text-primary">Friday entries:</span> Never buy weekly options on Friday. You pay for 2 days of theta (weekend) with zero trading time. Enter Monday or Tuesday.</p>
              <p><span className="font-semibold text-text-primary">Major macro events:</span> FOMC days, CPI releases, jobs reports. These override individual stock signals. Sit out or hedge with index puts.</p>
              <p><span className="font-semibold text-text-primary">IV rank &ge; 80 (single-name):</span> Even HIGH-bucket credit strategies get thin; sit out. Correct directional bets can still lose money from IV crush.</p>
            </div>
          </GuideCard>
        </div>
      </div>

      {/* ── Using Vela Features ──────────────────────────────── */}
      <div>
        <SectionHeading size="lg" title="Feature Usage for Trading" blurb="How each dashboard feature maps to a trading decision." />
        <DocTable<FeatureRow>
          rows={FEATURE_ROWS}
          rowKey={(row) => row.feature}
          columns={[
            { key: 'feature', header: 'Feature', className: 'text-text-primary font-medium whitespace-nowrap' },
            { key: 'use', header: 'Trading Use', className: 'text-text-secondary' },
          ]}
        />
      </div>

      {/* ── Quick Reference ──────────────────────────────────────── */}
      <div>
        <SectionHeading size="lg" title="Quick Decision Matrix" blurb="Fast reference for the most common scenarios." />
        <DocTable<DecisionRow>
          size="xs"
          rows={DECISION_ROWS}
          rowKey={(r) => r.action}
          columns={[
            { key: 'action', header: 'Action', className: 'font-semibold', render: (r) => <span style={{ color: r.color }}>{r.action}</span> },
            { key: 'low', header: `IV Low (${IV_BUCKETS.low})`, align: 'center', render: (r) => decisionCell(r.low) },
            { key: 'mid', header: `IV Mid (${IV_BUCKETS.mid})`, align: 'center', render: (r) => decisionCell(r.mid) },
            { key: 'high', header: `IV High (${IV_BUCKETS.high})`, align: 'center', render: (r) => decisionCell(r.high) },
          ]}
        />
        <p className="text-[10px] text-text-secondary mt-2">
          CSP = Cash-Secured Put. "Skip" means the risk/reward doesn't justify a weekly options position.
          All strategies assume delta-neutral IV management — use spreads when IV is elevated to avoid paying inflated premium.
          The IV columns are the same engine buckets the Options Lab uses (LOW {IV_BUCKETS.low}, MID {IV_BUCKETS.mid}, HIGH {IV_BUCKETS.high}); the plays in each cell are a rule of thumb, not an engine verdict.
        </p>
      </div>
    </div>
  );
}
