import { redirect } from "next/navigation";

// Retained historical links open the same experiment workspace as TensorBoard.
export default async function MultiExperimentPage({ params }: { params: Promise<{ id: string }> }): Promise<never> {
  const { id } = await params;
  redirect(`/lab?run=${encodeURIComponent(id)}&returnTo=${encodeURIComponent(`/runs/${id}`)}`);
}
