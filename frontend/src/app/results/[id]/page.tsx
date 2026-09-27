import { TopBar } from "@/components/TopBar";
import { RunResultsPanel } from "@/components/RunResultsPanel";

export default async function ResultPage({ params }: { params: Promise<{ id: string }> }): Promise<JSX.Element> {
  const { id } = await params;
  return <div className="min-h-screen bg-mars-bg"><TopBar /><main className="mx-auto max-w-6xl space-y-6 p-4 md:p-8"><RunResultsPanel key={id} runId={id} /></main></div>;
}
