export default function NotificationToggle({ label, on, onToggle }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={!!on} onChange={onToggle} />
      <span>{label}</span>
    </label>
  );
}
