import type { Metadata } from "next";

import { IdeaDiscoveryWorkbench } from "@/features/idea-discovery/IdeaDiscoveryWorkbench";

export const metadata: Metadata = {
  title: "Idea Discovery · MARS",
  description: "Restore and review a Co-Scientist hypothesis discovery run.",
};

export default async function IdeaDiscoveryPage({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<JSX.Element> {
  const { id } = await params;
  return <IdeaDiscoveryWorkbench runId={id} />;
}
