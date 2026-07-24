export default function Loading() {
  return (
    <div className="page-shell market-page-shell" aria-hidden="true">
      <div className="skeleton-hero">
        <div className="skeleton-block skeleton-kicker" />
        <div className="skeleton-block skeleton-title" />
        <div className="skeleton-block skeleton-copy" />
      </div>
      <div className="skeleton-block skeleton-strip" />
      <div className="skeleton-rows">
        {Array.from({ length: 8 }, (_, index) => (
          <div className="skeleton-row" key={index}>
            <div className="skeleton-block skeleton-rank" />
            <div className="skeleton-block skeleton-thumb" />
            <div className="skeleton-lines">
              <div className="skeleton-block skeleton-line-name" />
              <div className="skeleton-block skeleton-line-sub" />
            </div>
            <div className="skeleton-block skeleton-value" />
          </div>
        ))}
      </div>
    </div>
  );
}
