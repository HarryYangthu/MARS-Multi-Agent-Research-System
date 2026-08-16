import type {
  DiscoveryHypothesis,
  DiscoveryStage,
  IdeaDiscoverySnapshot,
  MetaReviewView,
} from "./types";
import { ProximityMap } from "./ProximityMap";

export const DISCOVERY_STAGES: Array<{
  id: DiscoveryStage;
  label: string;
  short: string;
}> = [
  { id: "generation", label: "Generation", short: "Generate" },
  { id: "reflection", label: "Reflection", short: "Reflect" },
  { id: "elo", label: "Pairwise / Elo", short: "Elo" },
  { id: "debate", label: "Debate", short: "Debate" },
  { id: "proximity", label: "Proximity", short: "Proximity" },
  { id: "evolution", label: "Evolution", short: "Evolve" },
  { id: "meta-review", label: "Meta-review", short: "Meta" },
];

export function DiscoveryStagePanel({
  stage,
  snapshot,
}: {
  stage: DiscoveryStage;
  snapshot: IdeaDiscoverySnapshot;
}): JSX.Element {
  switch (stage) {
    case "generation":
      return <GenerationPanel hypotheses={snapshot.hypotheses} />;
    case "reflection":
      return <ReflectionPanel snapshot={snapshot} />;
    case "elo":
      return <EloPanel snapshot={snapshot} />;
    case "debate":
      return <DebatePanel snapshot={snapshot} />;
    case "proximity":
      return <ProximityPanel snapshot={snapshot} />;
    case "evolution":
      return <EvolutionPanel hypotheses={snapshot.hypotheses} />;
    case "meta-review":
      return <MetaReviewPanel reviews={snapshot.meta_reviews} />;
  }
}

function GenerationPanel({ hypotheses }: { hypotheses: DiscoveryHypothesis[] }): JSX.Element {
  const initial = hypotheses.filter((item) => item.round_index === 0);
  return (
    <Panel title="Initial hypothesis pool" count={initial.length}>
      <div className="grid gap-3 md:grid-cols-2">
        {initial.map((item) => (
          <RecordCard key={item.hypothesis_id} title={item.mechanism} tone={item.blocked ? "danger" : "neutral"}>
            <p>{item.statement}</p>
            <RecordFooter items={[`Elo ${item.elo.toFixed(1)}`, item.cluster_id || "unclustered"]} />
          </RecordCard>
        ))}
        {initial.length === 0 ? <Empty label="Generation records are not available yet." /> : null}
      </div>
    </Panel>
  );
}

function ReflectionPanel({ snapshot }: { snapshot: IdeaDiscoverySnapshot }): JSX.Element {
  const names = new Map(snapshot.hypotheses.map((item) => [item.hypothesis_id, item.mechanism]));
  return (
    <Panel title="Correctness · novelty · falsifiability" count={snapshot.reflections.length}>
      <div className="space-y-3">
        {snapshot.reflections.map((item) => (
          <RecordCard
            key={item.reflection_id}
            title={names.get(item.hypothesis_id) ?? item.hypothesis_id}
            tone={item.blockers.length ? "danger" : "neutral"}
          >
            <div className="grid gap-3 text-xs md:grid-cols-3">
              <LabeledText label="Correctness" text={item.correctness} />
              <LabeledText label="Novelty" text={item.novelty} />
              <LabeledText label="Falsifiability" text={item.falsifiability} />
            </div>
            {item.blockers.length ? <RecordFooter items={item.blockers} danger /> : null}
          </RecordCard>
        ))}
        {snapshot.reflections.length === 0 ? <Empty label="Reflection records are not available yet." /> : null}
      </div>
    </Panel>
  );
}

function EloPanel({ snapshot }: { snapshot: IdeaDiscoverySnapshot }): JSX.Element {
  const sorted = [...snapshot.hypotheses].sort((left, right) => right.elo - left.elo);
  const max = Math.max(...sorted.map((item) => item.elo), 1);
  const names = new Map(snapshot.hypotheses.map((item) => [item.hypothesis_id, item.mechanism]));
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <Panel title="Elo leaderboard" count={sorted.length}>
        <div className="space-y-3">
          {sorted.map((item, index) => (
            <div key={item.hypothesis_id} className="grid grid-cols-[2rem_minmax(0,1fr)_4rem] items-center gap-3 text-xs">
              <span className="font-mono text-slate-500">#{index + 1}</span>
              <div>
                <div className="mb-1 flex justify-between gap-3"><span className="truncate text-slate-200">{item.mechanism}</span></div>
                <div className="h-1.5 overflow-hidden rounded-full bg-mars-bg"><div className="h-full rounded-full bg-gradient-to-r from-indigo-500 to-violet-400" style={{ width: `${Math.max(5, (item.elo / max) * 100)}%` }} /></div>
              </div>
              <span className="text-right font-mono text-indigo-200">{item.elo.toFixed(1)}</span>
            </div>
          ))}
        </div>
      </Panel>
      <Panel title="Pairwise matches" count={snapshot.matches.length}>
        <div className="max-h-[28rem] space-y-2 overflow-auto pr-1">
          {snapshot.matches.map((match) => (
            <div key={match.match_id} className="rounded-lg border border-mars-border bg-mars-bg/55 p-3 text-xs">
              <div className="flex flex-wrap items-center gap-2 text-slate-300">
                <span>{names.get(match.left_id) ?? match.left_id}</span>
                <span className="rounded bg-indigo-500/15 px-2 py-0.5 font-mono text-indigo-200">{match.outcome}</span>
                <span>{names.get(match.right_id) ?? match.right_id}</span>
              </div>
              <p className="mt-2 leading-5 text-slate-500">{match.reason || "No judge rationale recorded."}</p>
            </div>
          ))}
          {snapshot.matches.length === 0 ? <Empty label="Pairwise matches are not available yet." /> : null}
        </div>
      </Panel>
    </div>
  );
}

function DebatePanel({ snapshot }: { snapshot: IdeaDiscoverySnapshot }): JSX.Element {
  const debate = snapshot.debate;
  return (
    <Panel title="Debate and disagreement surface" count={debate ? 1 : 0}>
      {debate ? (
        <div className="grid gap-4 md:grid-cols-2">
          <RecordCard title={`Status · ${debate.status}`}><p>{debate.summary || "No consensus summary."}</p></RecordCard>
          <RecordCard title="Open disagreements" tone={debate.disagreements.length ? "warning" : "neutral"}>
            <BulletList items={debate.disagreements} empty="No structured disagreements." />
          </RecordCard>
          <RecordCard title="Evidence gaps"><BulletList items={debate.evidence_gaps} empty="No evidence gaps recorded." /></RecordCard>
        </div>
      ) : (
        <div className="rounded-xl border border-dashed border-mars-border bg-mars-bg/40 p-5 text-sm leading-6 text-slate-400">
          Dedicated debate records were not returned by REST. Pairwise judge rationales remain visible in the Elo stage; the UI does not synthesize debate state locally.
        </div>
      )}
    </Panel>
  );
}

function ProximityPanel({ snapshot }: { snapshot: IdeaDiscoverySnapshot }): JSX.Element {
  const names = new Map(snapshot.hypotheses.map((item) => [item.hypothesis_id, item.mechanism]));
  return (
    <Panel title="Semantic neighborhoods" count={snapshot.proximity_graphs.length}>
      <div className="space-y-4">
        {snapshot.proximity_graphs.map((graph) => (
          <div key={graph.round_index} className="rounded-xl border border-mars-border bg-mars-bg/45 p-4">
            <div className="mb-3 flex items-center justify-between"><h3 className="text-sm font-medium">Round {graph.round_index}</h3><span className="text-xs text-slate-500">{graph.edges.length} edges</span></div>
            <ProximityMap graph={graph} hypotheses={snapshot.hypotheses} />
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {Object.entries(graph.clusters).map(([cluster, ids]) => (
                <div key={cluster} className="rounded-lg border border-mars-border bg-mars-panel p-3">
                  <p className="font-mono text-[11px] text-indigo-300">{cluster}</p>
                  <ul className="mt-2 space-y-1 text-xs text-slate-400">{ids.map((id) => <li key={id} className="truncate">{names.get(id) ?? id}</li>)}</ul>
                </div>
              ))}
            </div>
            {graph.edges.some((edge) => edge.exact_duplicate) ? <p className="mt-3 text-xs text-rose-300">Exact duplicates are blocked from Top-K.</p> : null}
          </div>
        ))}
        {snapshot.proximity_graphs.length === 0 ? <Empty label="Proximity graphs are not available yet." /> : null}
      </div>
    </Panel>
  );
}

function EvolutionPanel({ hypotheses }: { hypotheses: DiscoveryHypothesis[] }): JSX.Element {
  const evolved = hypotheses.filter((item) => item.parent_ids.length > 0);
  const names = new Map(hypotheses.map((item) => [item.hypothesis_id, item.mechanism]));
  return (
    <Panel title="Evolved hypothesis lineage" count={evolved.length}>
      <div className="grid gap-3 md:grid-cols-2">
        {evolved.map((item) => (
          <RecordCard key={item.hypothesis_id} title={`${item.operator ?? "evolve"} · ${item.mechanism}`} tone={item.blocked ? "danger" : "neutral"}>
            <p>{item.statement}</p>
            <RecordFooter items={[`Round ${item.round_index}`, ...item.parent_ids.map((id) => `← ${names.get(id) ?? id}`)]} />
          </RecordCard>
        ))}
        {evolved.length === 0 ? <Empty label="Evolution records are not available yet." /> : null}
      </div>
    </Panel>
  );
}

function MetaReviewPanel({ reviews }: { reviews: MetaReviewView[] }): JSX.Element {
  return (
    <Panel title="Cross-round meta review" count={reviews.length}>
      <div className="space-y-4">
        {reviews.map((review) => (
          <div key={review.meta_review_id || review.round_index} className="grid gap-3 rounded-xl border border-mars-border bg-mars-bg/45 p-4 md:grid-cols-2 xl:grid-cols-3">
            <div className="md:col-span-2 xl:col-span-3"><span className="rounded bg-violet-500/15 px-2 py-1 text-xs text-violet-200">Round {review.round_index}</span></div>
            <MetaList title="Recurring errors" items={review.recurring_errors} />
            <MetaList title="Successful patterns" items={review.successful_patterns} />
            <MetaList title="Evidence gaps" items={review.evidence_gaps} />
            <MetaList title="Unexplored regions" items={review.unexplored_regions} />
            <div className="md:col-span-2"><MetaList title="Next-round guidance" items={review.next_round_guidance} /></div>
          </div>
        ))}
        {reviews.length === 0 ? <Empty label="Meta-review records are not available yet." /> : null}
      </div>
    </Panel>
  );
}

function Panel({ title, count, children }: { title: string; count: number; children: React.ReactNode }): JSX.Element {
  return <section className="rounded-2xl border border-mars-border bg-mars-panel p-5"><div className="mb-4 flex items-center justify-between"><h2 className="text-base font-semibold text-slate-100">{title}</h2><span className="rounded-full bg-mars-bg px-2.5 py-1 font-mono text-[11px] text-slate-500">{count}</span></div>{children}</section>;
}

function RecordCard({ title, tone = "neutral", children }: { title: string; tone?: "neutral" | "warning" | "danger"; children: React.ReactNode }): JSX.Element {
  const toneClass = tone === "danger" ? "border-rose-500/35" : tone === "warning" ? "border-amber-400/35" : "border-mars-border";
  return <article className={`rounded-xl border ${toneClass} bg-mars-bg/55 p-4`}><h3 className="mb-2 text-sm font-medium text-slate-200">{title}</h3><div className="text-xs leading-5 text-slate-400">{children}</div></article>;
}

function LabeledText({ label, text }: { label: string; text: string }): JSX.Element {
  return <div><p className="mb-1 font-semibold uppercase tracking-wide text-slate-500">{label}</p><p className="leading-5 text-slate-300">{text || "Not recorded"}</p></div>;
}

function RecordFooter({ items, danger = false }: { items: string[]; danger?: boolean }): JSX.Element {
  return <div className={`mt-3 flex flex-wrap gap-2 ${danger ? "text-rose-300" : "text-slate-500"}`}>{items.filter(Boolean).map((item) => <span key={item} className="rounded bg-mars-panel px-2 py-0.5 font-mono text-[10px]">{item}</span>)}</div>;
}

function BulletList({ items, empty }: { items: string[]; empty: string }): JSX.Element {
  return items.length ? <ul className="space-y-1.5">{items.map((item) => <li key={item}>• {item}</li>)}</ul> : <p>{empty}</p>;
}

function MetaList({ title, items }: { title: string; items: string[] }): JSX.Element {
  return <div><h3 className="mb-2 text-xs font-semibold uppercase tracking-wide text-violet-300">{title}</h3><BulletList items={items} empty="None recorded." /></div>;
}

function Empty({ label }: { label: string }): JSX.Element {
  return <div className="rounded-xl border border-dashed border-mars-border p-5 text-center text-xs text-slate-500">{label}</div>;
}
