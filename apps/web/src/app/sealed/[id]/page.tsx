import { redirect } from "next/navigation";

export default async function SealedAliasProductPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  redirect(`/box/${id}`);
}
