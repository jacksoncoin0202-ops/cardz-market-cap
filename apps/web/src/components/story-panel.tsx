import { storyParagraphs } from "@/lib/story-display";

export function StoryPanel({ title, story }: { title: string; story: string | null | undefined }) {
  const paragraphs = storyParagraphs(story);
  if (!paragraphs.length) return null;
  return (
    <section className="story-panel">
      <h2>{title}</h2>
      {paragraphs.map((paragraph, index) => (
        <p key={`${index}-${paragraph.slice(0, 24)}`}>{paragraph}</p>
      ))}
    </section>
  );
}
