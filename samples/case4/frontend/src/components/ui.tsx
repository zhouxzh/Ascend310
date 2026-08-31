import type { ButtonHTMLAttributes, ChangeEvent, ReactNode, SelectHTMLAttributes } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  CircleHelp,
  Info,
  LoaderCircle,
  XCircle,
  type LucideIcon,
} from "lucide-react";

export const cx = (...values: Array<string | false | null | undefined>): string =>
  values.filter(Boolean).join(" ");

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "quiet";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  icon?: LucideIcon;
  loading?: boolean;
  children: ReactNode;
}

export function Button({
  variant = "secondary",
  icon: Icon,
  loading = false,
  className,
  children,
  disabled,
  ...props
}: ButtonProps) {
  return (
    <button
      type="button"
      className={cx("button", `button--${variant}`, className)}
      disabled={disabled || loading}
      {...props}
    >
      {loading ? <LoaderCircle className="button__spinner" aria-hidden="true" /> : Icon ? <Icon aria-hidden="true" /> : null}
      <span>{children}</span>
    </button>
  );
}

interface IconButtonProps extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "children"> {
  icon: LucideIcon;
  label: string;
  active?: boolean;
  variant?: "default" | "danger";
}

export function IconButton({ icon: Icon, label, active, variant = "default", className, ...props }: IconButtonProps) {
  return (
    <button
      type="button"
      className={cx("icon-button", active && "is-active", variant === "danger" && "is-danger", className)}
      aria-label={label}
      title={label}
      {...props}
    >
      <Icon aria-hidden="true" />
    </button>
  );
}

interface PanelProps {
  title?: string;
  eyebrow?: string;
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

export function Panel({ title, eyebrow, action, children, className }: PanelProps) {
  return (
    <section className={cx("panel", className)}>
      {(title || eyebrow || action) && (
        <header className="panel__header">
          <div>
            {eyebrow ? <p className="panel__eyebrow">{eyebrow}</p> : null}
            {title ? <h2 className="panel__title">{title}</h2> : null}
          </div>
          {action ? <div className="panel__action">{action}</div> : null}
        </header>
      )}
      {children}
    </section>
  );
}

export type StatusTone = "neutral" | "success" | "warning" | "danger" | "info";

export function toneForStatus(value?: string): StatusTone {
  const normalized = (value || "").toLowerCase();
  // The established board reports Health: Alarm as a diagnostic warning;
  // only an actual runtime failure should render as a destructive state.
  if (/(error|failed|unavailable|missing|拒识|故障)/.test(normalized)) return "danger";
  if (/(warning|warn|queued|running|pending|conversion|alarm|告警|运行中)/.test(normalized)) return "warning";
  if (/(ok|ready|healthy|completed|accepted|通过|就绪|完成)/.test(normalized)) return "success";
  if (/(info|npu|cpu)/.test(normalized)) return "info";
  return "neutral";
}

interface StatusPillProps {
  tone?: StatusTone;
  children: ReactNode;
  icon?: boolean;
  className?: string;
}

export function StatusPill({ tone = "neutral", children, icon = true, className }: StatusPillProps) {
  const Icon = tone === "success" ? CheckCircle2 : tone === "warning" ? AlertTriangle : tone === "danger" ? XCircle : Info;
  return (
    <span className={cx("status-pill", `status-pill--${tone}`, className)}>
      {icon ? <Icon aria-hidden="true" /> : null}
      <span>{children}</span>
    </span>
  );
}

interface SelectFieldProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label: string;
  hint?: string;
  options: Array<{ value: string; label: string; disabled?: boolean }>;
}

export function SelectField({ label, hint, options, id, className, ...props }: SelectFieldProps) {
  const fieldId = id || `field-${label.replace(/\s/g, "-")}`;
  return (
    <label className={cx("field", className)} htmlFor={fieldId}>
      <span className="field__label">{label}</span>
      <select id={fieldId} className="field__control" {...props}>
        {options.map((option) => (
          <option value={option.value} key={option.value} disabled={option.disabled}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? <span className="field__hint">{hint}</span> : null}
    </label>
  );
}

interface TextFieldProps extends Omit<React.InputHTMLAttributes<HTMLInputElement>, "size"> {
  label: string;
  hint?: string;
}

export function TextField({ label, hint, id, className, ...props }: TextFieldProps) {
  const fieldId = id || `field-${label.replace(/\s/g, "-")}`;
  return (
    <label className={cx("field", className)} htmlFor={fieldId}>
      <span className="field__label">{label}</span>
      <input id={fieldId} className="field__control" {...props} />
      {hint ? <span className="field__hint">{hint}</span> : null}
    </label>
  );
}

interface ToggleProps {
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  description?: string;
}

export function Toggle({ label, checked, onChange, description }: ToggleProps) {
  return (
    <label className="toggle">
      <span className="toggle__copy">
        <span className="toggle__label">{label}</span>
        {description ? <span className="toggle__description">{description}</span> : null}
      </span>
      <input type="checkbox" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="toggle__track" aria-hidden="true"><span /></span>
    </label>
  );
}

interface RangeFieldProps {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  onChange: (value: number) => void;
}

export function RangeField({ label, value, min, max, step, onChange }: RangeFieldProps) {
  return (
    <label className="range-field">
      <span className="field__label">{label}</span>
      <div className="range-field__row">
        <input
          type="range"
          min={min}
          max={max}
          step={step}
          value={value}
          onChange={(event) => onChange(Number(event.target.value))}
        />
        <output>{value.toFixed(2)}</output>
      </div>
    </label>
  );
}

interface NoticeProps {
  tone?: StatusTone;
  children: ReactNode;
}

export function Notice({ tone = "info", children }: NoticeProps) {
  const Icon = tone === "danger" ? XCircle : tone === "warning" ? AlertTriangle : tone === "success" ? CheckCircle2 : CircleHelp;
  return (
    <div className={cx("notice", `notice--${tone}`)}>
      <Icon aria-hidden="true" />
      <span>{children}</span>
    </div>
  );
}

interface MetricTileProps {
  label: string;
  value: string | number;
  tone?: StatusTone;
  caption?: string;
}

export function MetricTile({ label, value, tone = "neutral", caption }: MetricTileProps) {
  return (
    <article className={cx("metric-tile", `metric-tile--${tone}`)}>
      <span className="metric-tile__label">{label}</span>
      <strong className="metric-tile__value">{value}</strong>
      {caption ? <span className="metric-tile__caption">{caption}</span> : null}
    </article>
  );
}

export function fileFromInput(event: ChangeEvent<HTMLInputElement>): File | undefined {
  return event.currentTarget.files?.[0];
}
