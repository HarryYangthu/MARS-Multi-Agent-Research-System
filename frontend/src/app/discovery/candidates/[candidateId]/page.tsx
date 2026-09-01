import { CandidateWorkbench } from "@/features/discovery/components/CandidateWorkbench";

export default async function DiscoveryCandidatePage({
  params,
  searchParams,
}: {
  params: Promise<{ candidateId: string }>;
  searchParams: Promise<{ run?: string | string[] }>;
}): Promise<JSX.Element> {
  const [{ candidateId }, query] = await Promise.all([params, searchParams]);
  const run = Array.isArray(query.run) ? query.run[0] ?? "" : query.run ?? "";
  return <CandidateWorkbench candidateId={candidateId} runId={run} />;
}
