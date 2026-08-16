import type { Metadata } from "next";

import { ProjectPackRunCreator } from "@/features/project-pack/ProjectPackRunCreator";

export const metadata: Metadata = {
  title: "New V3.1 Idea Run · MARS",
  description: "Create a Project Pack driven Idea discovery run.",
};

export default function NewV31RunPage(): JSX.Element {
  return <ProjectPackRunCreator />;
}
