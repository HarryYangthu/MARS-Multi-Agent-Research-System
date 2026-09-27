import { redirect } from "next/navigation";

export default async function Home({ searchParams }: {
  searchParams: Promise<Record<string, string | string[] | undefined>>;
}): Promise<never> {
  const query = await searchParams;
  // Preserve existing links which open a particular experiment.
  if (query.run || query.project) {
    const values = new URLSearchParams();
    for (const key of ["run", "project"]) {
      const value = query[key];
      if (typeof value === "string") values.set(key, value);
    }
    redirect(`/lab?${values.toString()}`);
  }
  redirect("/projects");
}
