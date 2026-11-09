export function Metric({
  title,
  value,
  detail,
}: {
  title: string;
  value: string;
  detail: string;
}) {
  return (
    <div className="metric">
      <p>{title}</p>
      <strong>{value}</strong>
      <small>{detail}</small>
    </div>
  );
}
