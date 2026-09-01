import { RunWorkbench } from "@/features/discovery/components/RunWorkbench";

export default async function DiscoveryRunPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<JSX.Element> {
  const { id } = await params;
  return <RunWorkbench runId={id} />;
}
