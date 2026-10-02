export default function NotificationToggle({ label, on, onToggle, disabled = false }) {
  return (
    <label className="toggle">
      <input type="checkbox" checked={!!on} onChange={onToggle} disabled={disabled} />
      <span>{label}</span>
    </label>
  );
}
