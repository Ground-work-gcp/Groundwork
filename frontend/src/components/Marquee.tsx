/** A slow, seamless ticker: the track is duplicated so translateX(-50%) loops without a seam. */
/* What Groundwork runs on in production. A ticker should not name a dependency
   the live system does without. */
export const STACK = [
  "Cloud Run",
  "Cloud SQL for PostgreSQL",
  "Cloud Storage",
  "Document AI",
  "Gemini on Vertex AI",
  "Sensitive Data Protection",
  "Secret Manager",
  "Artifact Registry",
  "FastAPI",
  "React",
];

export function Marquee({ items = STACK, label = "Built on" }: { items?: string[]; label?: string }) {
  const track = (
    <>
      {items.map((it) => (
        <span key={it} className="mq-item">
          {it}
        </span>
      ))}
    </>
  );
  return (
    <div className="marquee" role="marquee" aria-label={`${label}: ${items.join(", ")}`}>
      {label ? <span className="mq-label">{label}</span> : null}
      <div className="mq-clip">
        <div className="mq-track">
          <div className="mq-half" aria-hidden="false">
            {track}
          </div>
          <div className="mq-half" aria-hidden="true">
            {track}
          </div>
        </div>
      </div>
    </div>
  );
}
