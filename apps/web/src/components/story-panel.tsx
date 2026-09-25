import { stripStoryMarker } from "@/lib/plain-text";
import { storyParagraphs } from "@/lib/story-display";

export function StoryPanel({ title, story }: { title: string; story: string | null | undefined }) {
  /* 段首 emoji 記號喺呢度剝（見 stripStoryMarker）；剝完係空嘅段唔出 */
  const paragraphs = storyParagraphs(story).map(stripStoryMarker).filter(Boolean);
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
