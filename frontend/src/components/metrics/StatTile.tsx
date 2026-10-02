export default function StatTile({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="viz-tile">
      <div className="viz-tile-label">{label}</div>
      <div className="viz-tile-value">{value}</div>
      {sub && <div className="viz-tile-sub">{sub}</div>}
    </div>
  );
}
