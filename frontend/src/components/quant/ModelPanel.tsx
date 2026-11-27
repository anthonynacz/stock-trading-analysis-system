import { useState } from 'react';
import axios from 'axios';
import type { QuantEvaluation, QuantJob, QuantJobRun, QuantModelScore, QuantStatus, QuantVerdict } from '../../types';
import { getApiErrorMessage } from '../../utils/api';
import { fmtInt, formatRelativeTime } from '../../utils/format';
import { Spinner } from '../ui/feedback';
import { runQuantJob } from './quantApi';
import { READING_CFG, fmtDate, fmtDateTime } from './model';

const MODEL_NAMES: Record<string, { name: string; what: string }> = {
  trailing_21d: { name: 'Naive baseline', what: 'the last 21 sessions’ volatility carried forward ("next month looks like last month")' },
  long_run_mean: { name: 'Long-run average', what: 'each stock’s own long-run average volatility' },
  har_pooled: { name: 'HAR, all stocks', what: 'the HAR model (daily, weekly and monthly volatility combined) fitted across all stocks' },
  har_ticker: { name: 'HAR, per stock', what: 'the same HAR model fitted separately for each stock' },
};
const MODEL_ORDER = ['har_pooled', 'har_ticker', 'long_run_mean', 'trailing_21d'];
const BASELINE = 'trailing_21d';

const SOURCE_TEXT: Record<string, string> = {
  quant_daily_bars: 'daily price bars collected by this server (FMP, Yahoo as fallback)',
  csv: 'a file of daily price bars loaded by hand',
};

const JOBS: { job: QuantJob; name: string; when: string }[] = [
  { job: 'chains', name: 'Option chain snapshot', when: 'weekdays 15:40 ET, during the session' },
  { job: 'bars', name: 'Daily price bars', when: 'weekdays 17:05 ET, after the close' },
  { job: 'forecast', name: 'Forecast + readings', when: 'right after the price bars' },
];

const modelName = (m: string) => MODEL_NAMES[m]?.name ?? m;

function Section({ title, children, aside }: { title: string; children: React.ReactNode; aside?: React.ReactNode }) {
  return (
    <section className="bg-card border border-border rounded-lg p-4 min-w-0">
      <div className="flex items-center justify-between gap-2 mb-3 flex-wrap">
        <h2 className="text-base font-semibold">{title}</h2>
        {aside}
      </div>
      {children}
    </section>
  );
}

/* ── Scorecard ───────────────────────────────────────────────────────────── */

function ScoreTable({ models, inUse }: { models: Record<string, QuantModelScore>; inUse: string }) {
  const keys = [...MODEL_ORDER.filter((k) => models[k]), ...Object.keys(models).filter((k) => !MODEL_ORDER.includes(k))];
  return (
    <div className="overflow-x-auto border border-border rounded">
      <table className="w-full text-xs">
        <thead>
          <tr className="bg-border/40 text-text-secondary">
            <th className="text-left px-2.5 py-1.5 font-medium">Forecast</th>
            <th className="text-right px-2.5 py-1.5 font-medium" title="QLIKE: the standard loss for volatility forecasts. Lower is better.">
              Error score
            </th>
            <th className="text-right px-2.5 py-1.5 font-medium" title="Typical miss, in volatility points (5 = forecast 25% when it turned out 30%).">
              Typical miss
            </th>
            <th className="text-right px-2.5 py-1.5 font-medium" title="Error score against the naive baseline. Negative = smaller error.">
              vs baseline
            </th>
            <th className="text-right px-2.5 py-1.5 font-medium" title="Share of tickers where this forecast had the smaller error.">
              Tickers better
            </th>
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => {
            const m = models[k];
            const rel = m.qlike_vs_trailing;
            return (
              <tr key={k} className="border-t border-border/60">
                <td className="px-2.5 py-1.5 whitespace-nowrap">
                  <span className="text-text-primary">{modelName(k)}</span>
                  {k === inUse && <span className="ml-1.5 text-[10px] text-accent-300">in use</span>}
                  {k === BASELINE && <span className="ml-1.5 text-[10px] text-text-secondary">baseline</span>}
                </td>
                <td className="px-2.5 py-1.5 text-right tabular-nums">{m.qlike.toFixed(3)}</td>
                <td className="px-2.5 py-1.5 text-right tabular-nums">{(m.rmse_vol * 100).toFixed(1)} pts</td>
                <td className="px-2.5 py-1.5 text-right tabular-nums">
                  {rel === undefined ? '—' : `${rel <= 1 ? '−' : '+'}${Math.abs((1 - rel) * 100).toFixed(0)}%`}
                </td>
                <td className="px-2.5 py-1.5 text-right tabular-nums">
                  {m.ticker_win_share === undefined || m.ticker_win_share === null ? '—' : `${Math.round(m.ticker_win_share * 100)}%`}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/** The recorded three-state verdict, else the same rule applied to the scores (an older backend). */
function verdictOf(ev: QuantEvaluation): QuantVerdict | null {
  if (ev.har_verdict !== undefined) return ev.har_verdict ?? null;
  const best = ev.best_har ? ev.models?.[ev.best_har] : undefined;
  const base = ev.models?.[BASELINE];
  if (!best || !base) return null;
  if (!(best.qlike < base.qlike)) return 'not_better';
  const t = best.nw_t_vs_trailing;
  return t !== undefined && t !== null && Number.isFinite(t) && t < -2 ? 'better_significant' : 'lower_not_significant';
}

const VERDICT_HEAD: Record<QuantVerdict, string> = {
  better_significant: 'Smaller error than the naive baseline out of sample, beyond what luck explains.',
  lower_not_significant: 'Not established: a smaller error than the naive baseline, but not distinguishable from luck.',
  not_better: 'Did not beat the naive baseline out of sample.',
};

function Scorecard({ ev, inUse }: { ev: QuantEvaluation | null; inUse: string }) {
  if (!ev || !ev.models || Object.keys(ev.models).length === 0) {
    return (
      <div className="text-sm text-text-secondary space-y-2">
        <p>
          <span className="text-text-primary font-semibold">Not scored on this server yet.</span> The scorecard comes from an
          out-of-sample test that runs after the first daily close job once price history is stored, and again every 30 days, so until then there is no
          measured evidence here that the model beats the naive guess.
        </p>
        <p className="text-xs">
          The design was tested before shipping on Yahoo price data (2026-11, 56 large US stocks and ETFs, 2018–2026): the
          HAR forecast had a smaller error than the naive baseline on that sample. That result is recorded in the project
          notes, not re-measured on this data, and is not proof that it holds here.
        </p>
      </div>
    );
  }
  const used = ev.models[inUse] ? inUse : ev.best_har ?? inUse;
  const m = ev.models[used];
  const base = ev.models[BASELINE];
  const verdict = verdictOf(ev);
  const lr = ev.models.long_run_mean;
  const bestScore = ev.best_har ? ev.models[ev.best_har] : undefined;
  const lrBeats = ev.long_run_beats_har ?? (lr && bestScore ? lr.qlike < bestScore.qlike : false);
  const good = verdict === 'better_significant' && !lrBeats;
  const pctLower = m?.qlike_vs_trailing !== undefined ? Math.round((1 - m.qlike_vs_trailing) * 100) : null;
  const t = m?.nw_t_vs_trailing;
  return (
    <div className="space-y-3">
      <div
        className={`rounded border p-3 text-sm ${
          good ? 'border-emerald-400/40 bg-emerald-500/10 text-emerald-100' : 'border-amber-400/40 bg-amber-500/10 text-amber-100'
        }`}
      >
        <span className="font-semibold">{verdict ? VERDICT_HEAD[verdict] : 'No verdict recorded.'}</span>{' '}
        {m && base && pctLower !== null && (
          <>
            {modelName(used)}: {pctLower >= 0 ? `${pctLower}% smaller` : `${-pctLower}% larger`} forecast error than the baseline
            {m.ticker_win_share !== undefined && m.ticker_win_share !== null && `, better on ${Math.round(m.ticker_win_share * 100)}% of tickers`}
            {t !== undefined && t !== null && (
              <>
                {' '}
                (t = {t.toFixed(1)};{' '}
                {t <= -2 ? 'below −2, so unlikely to be luck' : 'not below −2, so it could be luck'})
              </>
            )}
            .
          </>
        )}
        {lrBeats && (
          <p className="mt-1.5">
            The long-run average, another naive guess, scored a smaller error than the model on the same test, so this
            result is not evidence that the model adds anything.
          </p>
        )}
      </div>
      <dl className="grid grid-cols-1 sm:grid-cols-[max-content_1fr] gap-x-4 gap-y-0.5 sm:gap-y-1.5 text-xs">
        <dt className="text-text-secondary">What was compared</dt>
        <dd className="text-text-primary">
          Each forecast of the next 21 sessions&rsquo; volatility against what then happened, and against{' '}
          {MODEL_NAMES[BASELINE].what}.
        </dd>
        <dt className="text-text-secondary">How</dt>
        <dd className="text-text-primary">
          Walk-forward: every forecast used only data available on its date, never the test period itself.
        </dd>
        <dt className="text-text-secondary">Sample</dt>
        <dd className="text-text-primary">
          {fmtInt(ev.n_obs)} ticker-days, {fmtInt(ev.n_tickers)} tickers, {fmtInt(ev.days)} trading days
        </dd>
        <dt className="text-text-secondary">Window</dt>
        <dd className="text-text-primary">
          {fmtDate(ev.window_start)} – {fmtDate(ev.window_end)}
        </dd>
        <dt className="text-text-secondary">Data</dt>
        <dd className="text-text-primary">{SOURCE_TEXT[ev.data_source ?? ''] ?? ev.data_source ?? '—'}</dd>
        {ev.cleaning && ev.cleaning.rules.length > 0 && (
          <>
            <dt className="text-text-secondary">Cleaning</dt>
            <dd className="text-text-primary">
              {fmtInt(ev.cleaning.bars_removed)} no-trade price bars (a repeated price, no trading) in{' '}
              {fmtInt(ev.cleaning.tickers_affected)} tickers counted as missing, not as zero volatility
              {ev.cleaning.by_rule?.shell &&
                `; history before a cash-shell merger cut in ${fmtInt(ev.cleaning.by_rule.shell.tickers)} tickers`}
              {ev.cleaning.by_rule?.splits &&
                `; ${fmtInt(ev.cleaning.by_rule.splits.repaired + ev.cleaning.by_rule.splits.adj_only)} unadjusted splits repaired, ${fmtInt(ev.cleaning.by_rule.splits.ambiguous_missing)} unclear days left out`}
              {ev.cleaning.by_rule?.gaps &&
                `; ${fmtInt(ev.cleaning.by_rule.gaps.sessions_inserted)} missing sessions kept as gaps`}
              {ev.cleaning.oos_ticker_days_removed !== undefined &&
                `; ${fmtInt(ev.cleaning.oos_ticker_days_removed)} ticker-days left out of the test for every forecast alike`}
            </dd>
          </>
        )}
        <dt className="text-text-secondary">Scored</dt>
        <dd className="text-text-primary">
          {fmtDate(ev.created_at)} · model {ev.version ?? '—'}
        </dd>
      </dl>
      <ScoreTable models={ev.models} inUse={used} />
      <p className="text-[11px] text-text-secondary">
        Error score: QLIKE, the usual yardstick for volatility forecasts (lower is better; it punishes forecasting too
        little volatility more than too much). Typical miss: root-mean-square error in volatility points.
      </p>
    </div>
  );
}

/* ── Coverage ───────────────────────────────────────────────────────────── */

function Coverage({ st }: { st: QuantStatus }) {
  const perTicker = st.bars.tickers ? Math.round(st.bars.rows / st.bars.tickers) : 0;
  const years = perTicker / 252;
  return (
    <div className="grid grid-cols-1 sm:grid-cols-3 gap-2">
      <div className="bg-page/60 border border-border rounded p-3">
        <div className="text-[10px] uppercase tracking-wider text-text-secondary">Price history</div>
        <div className="text-lg font-bold tabular-nums">{st.bars.tickers ? `${fmtInt(perTicker)} days` : 'none yet'}</div>
        <div className="text-[11px] text-text-secondary">
          {st.bars.tickers
            ? `average per ticker (~${years.toFixed(1)} yr), ${fmtInt(st.bars.tickers)} tickers, ${fmtDate(st.bars.first_date)} – ${fmtDate(st.bars.last_date)}`
            : 'filled by the daily price bars job'}
        </div>
      </div>
      <div className="bg-page/60 border border-border rounded p-3">
        <div className="text-[10px] uppercase tracking-wider text-text-secondary">Option chain snapshots</div>
        <div className="text-lg font-bold tabular-nums">
          {st.chains.days_collected} day{st.chains.days_collected === 1 ? '' : 's'}
        </div>
        <div className="text-[11px] text-text-secondary">
          {st.chains.days_collected
            ? `${fmtInt(st.chains.tickers_last_day)} tickers on the last day, ${fmtDate(st.chains.first_date)} – ${fmtDate(st.chains.last_date)}`
            : 'one snapshot per trading day; none collected yet'}
        </div>
      </div>
      <div className="bg-page/60 border border-border rounded p-3">
        <div className="text-[10px] uppercase tracking-wider text-text-secondary">Forecast days</div>
        <div className="text-lg font-bold tabular-nums">{st.forecasts.days}</div>
        <div className="text-[11px] text-text-secondary">
          {st.forecasts.days
            ? `${fmtInt(st.forecasts.tickers)} tickers, ${fmtDate(st.forecasts.first_date)} – ${fmtDate(st.forecasts.last_date)}`
            : 'no forecast yet'}
        </div>
      </div>
    </div>
  );
}

/** Honest note while there is too little history for own-history readings. */
export function EarlyDaysNote({ st, counts }: { st: QuantStatus; counts: { total: number; insufficient: number } }) {
  // No forecast at all: the page's empty state explains it. Enough days: nothing to explain.
  if (st.forecasts.days === 0) return null;
  if (st.forecasts.days >= READING_CFG.ownMinObs && st.chains.days_collected >= READING_CFG.ownMinObs) return null;
  return (
    <div className="rounded border border-sky-400/30 bg-sky-500/10 p-3 text-xs text-sky-100 leading-relaxed">
      <span className="font-semibold">Early days, by design.</span> A reading compares each ticker with its own last{' '}
      {READING_CFG.ownMinObs} daily readings (about 3 months of trading days); there are {st.forecasts.days} forecast day
      {st.forecasts.days === 1 ? '' : 's'} and {st.chains.days_collected} option chain snapshot day
      {st.chains.days_collected === 1 ? '' : 's'} so far. Until then a name is compared with today&rsquo;s other names, which
      needs at least {READING_CFG.xsMinNames} names with a 30-day implied vol; when fewer are available the reading says
      &ldquo;not enough history&rdquo;
      {counts.total > 0 && ` (${counts.insufficient} of ${counts.total} today)`}. The forecasts themselves are complete.
    </div>
  );
}

/* ── Jobs ───────────────────────────────────────────────────────────────── */

const STATUS_CLASS: Record<string, string> = {
  SUCCESS: 'bg-emerald-500/15 text-emerald-200 border-emerald-400/40',
  FAILED: 'bg-red-500/15 text-red-200 border-red-400/50',
  RUNNING: 'bg-sky-500/15 text-sky-200 border-sky-400/40',
};
const STATUS_TEXT: Record<string, string> = { SUCCESS: 'OK', FAILED: 'Failed', RUNNING: 'Running' };

function resultSummary(job: QuantJob, r: Record<string, unknown> | null): string | null {
  if (!r) return null;
  if (r.status === 'skipped') return `skipped: ${String(r.reason ?? '')}`;
  if (job === 'forecast' && typeof r.forecasts === 'number') return `${r.forecasts} forecasts${r.as_of ? ` for ${fmtDate(String(r.as_of))}` : ''}`;
  const parts = Object.entries(r)
    .filter(([k, v]) => typeof v === 'number' && !/duration|_id$/.test(k))
    .slice(0, 3)
    .map(([k, v]) => `${k.replace(/_/g, ' ')} ${fmtInt(v as number)}`);
  return parts.length ? parts.join(' · ') : null;
}

function JobRow({
  meta,
  run,
  running,
  isAdmin,
  onStarted,
}: {
  meta: (typeof JOBS)[number];
  run: QuantJobRun | null;
  running: boolean;
  isAdmin: boolean;
  onStarted: () => void;
}) {
  const [sending, setSending] = useState(false);
  const [msg, setMsg] = useState<{ text: string; tone: string } | null>(null);
  const status = running ? 'RUNNING' : run?.status ?? null;
  const when = run?.finished_at ?? run?.started_at;
  const summary = resultSummary(meta.job, run?.result ?? null);

  const start = async () => {
    setSending(true);
    setMsg(null);
    try {
      await runQuantJob(meta.job);
      setMsg({ text: 'Started.', tone: 'text-sky-200' });
      onStarted();
    } catch (e) {
      if (axios.isAxiosError(e) && e.response?.status === 409) {
        setMsg({ text: 'Already running; wait for it to finish.', tone: 'text-amber-200' });
        onStarted();
      } else if (axios.isAxiosError(e) && e.response?.status === 403) {
        setMsg({ text: 'Admins only.', tone: 'text-red-300' });
      } else {
        setMsg({ text: getApiErrorMessage(e, 'Could not start the job'), tone: 'text-red-300' });
      }
    } finally {
      setSending(false);
    }
  };

  return (
    <li className="py-2.5 border-t border-border/60 first:border-t-0 flex flex-wrap items-start gap-x-3 gap-y-1">
      <div className="min-w-0 flex-1 basis-56">
        <div className="text-sm text-text-primary">{meta.name}</div>
        <div className="text-[11px] text-text-secondary">Scheduled {meta.when}</div>
      </div>
      <div className="min-w-0 basis-56 flex-1 text-xs">
        <div className="flex items-center gap-2 flex-wrap">
          {status ? (
            <span className={`inline-flex items-center gap-1 rounded border px-1.5 py-px text-[11px] font-semibold ${STATUS_CLASS[status] ?? 'border-border text-text-secondary'}`}>
              {status === 'RUNNING' && <Spinner size={3} tone="muted" />}
              {STATUS_TEXT[status] ?? status}
            </span>
          ) : (
            <span className="text-text-secondary">Never run</span>
          )}
          {when && (
            <span className="text-text-secondary" title={fmtDateTime(when)}>
              {formatRelativeTime(when)}
              {run?.duration_ms ? ` · ${(run.duration_ms / 1000).toFixed(0)} s` : ''}
            </span>
          )}
        </div>
        {summary && <div className="text-[11px] text-text-secondary mt-0.5">{summary}</div>}
        {run?.status === 'FAILED' && run.error_message && !running && (
          <div className="text-[11px] text-red-300 mt-0.5 break-words">{run.error_message}</div>
        )}
      </div>
      {isAdmin && (
        <div className="flex items-center gap-2 basis-full sm:basis-auto">
          <button
            type="button"
            onClick={start}
            disabled={sending || running}
            title={`Run "${meta.name}" now (admin). Skips the holiday and session checks of the schedule.`}
            className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded border border-accent-500/50 bg-accent-500/10 text-accent-200 text-xs font-semibold hover:bg-accent-500/20 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <span aria-hidden="true" className={sending || running ? 'inline-block animate-spin' : ''}>
              ⟳
            </span>
            {running ? 'Running…' : sending ? 'Starting…' : 'Run now'}
          </button>
          <span role="status" aria-live="polite" className={`text-[11px] ${msg?.tone ?? ''}`}>
            {msg?.text}
          </span>
        </div>
      )}
    </li>
  );
}

/** "How good is this model": the out-of-sample scorecard, data coverage and the jobs (admin: run now). */
export default function ModelPanel({
  st,
  isAdmin,
  onJobStarted,
}: {
  st: QuantStatus;
  isAdmin: boolean;
  onJobStarted: () => void;
}) {
  const inUse = st.model.pooled ? 'har_pooled' : 'har_ticker';
  return (
    <div className="space-y-4">
      <Section
        title="How good is this model?"
        aside={
          <span className="text-[11px] text-text-secondary">
            {st.model.name} · {st.model.version}
          </span>
        }
      >
        <Scorecard ev={st.evaluation} inUse={inUse} />
        <p className="mt-3 text-[11px] text-amber-200/90 leading-relaxed">
          <span className="font-semibold">Survivorship caveat:</span> the tested stocks are today&rsquo;s names, the ones that
          survived. Companies that collapsed or were delisted are missing, which usually makes any forecast look somewhat
          better than it would have been in real time.
        </p>
      </Section>

      <Section title="Data collected">
        <Coverage st={st} />
        <ul className="mt-3">
          {JOBS.map((j) => (
            <JobRow
              key={j.job}
              meta={j}
              run={st.jobs?.[j.job] ?? null}
              running={!!st.running?.[j.job]}
              isAdmin={isAdmin}
              onStarted={onJobStarted}
            />
          ))}
        </ul>
      </Section>
    </div>
  );
}
