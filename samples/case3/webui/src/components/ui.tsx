import type { ReactNode } from 'react'
import { AlertCircle, CheckCircle2, LoaderCircle, Minus, Plus, TriangleAlert } from 'lucide-react'

export function StatusPill({
  tone,
  children,
}: {
  tone: 'ok' | 'warn' | 'error' | 'neutral'
  children: ReactNode
}) {
  return <span className={`status-pill tone-${tone}`}>{children}</span>
}

export function PanelHeader({
  title,
  subtitle,
  action,
}: {
  title: string
  subtitle?: string
  action?: ReactNode
}) {
  return (
    <div className="panel-header">
      <div>
        <h2>{title}</h2>
        {subtitle && <p>{subtitle}</p>}
      </div>
      {action && <div className="panel-action">{action}</div>}
    </div>
  )
}

export function Metric({
  label,
  value,
  unit,
  tone,
}: {
  label: string
  value: string | number
  unit?: string
  tone?: 'teal' | 'amber' | 'red'
}) {
  return (
    <div className={`metric ${tone ? `metric-${tone}` : ''}`}>
      <span>{label}</span>
      <strong>
        {value}
        {unit && <small>{unit}</small>}
      </strong>
    </div>
  )
}

export function Field({ label, children, wide = false }: { label: string; children: ReactNode; wide?: boolean }) {
  return (
    <label className={`field ${wide ? 'field-wide' : ''}`}>
      <span>{label}</span>
      {children}
    </label>
  )
}

export function Stepper({
  value,
  min,
  max,
  onChange,
  label,
}: {
  value: number
  min: number
  max: number
  onChange: (value: number) => void
  label: string
}) {
  return (
    <div className="stepper" aria-label={label}>
      <button type="button" title="减小" onClick={() => onChange(Math.max(min, value - 1))} disabled={value <= min}>
        <Minus size={17} />
      </button>
      <strong>{value}</strong>
      <button type="button" title="增大" onClick={() => onChange(Math.min(max, value + 1))} disabled={value >= max}>
        <Plus size={17} />
      </button>
    </div>
  )
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { value: T; label: string }[]
  onChange: (value: T) => void
}) {
  return (
    <div className="segmented">
      {options.map((option) => (
        <button
          type="button"
          className={value === option.value ? 'is-active' : ''}
          key={option.value}
          onClick={() => onChange(option.value)}
        >
          {option.label}
        </button>
      ))}
    </div>
  )
}

export function Notice({
  tone,
  children,
}: {
  tone: 'loading' | 'error' | 'success' | 'warn'
  children: ReactNode
}) {
  const Icon = tone === 'loading'
    ? LoaderCircle
    : tone === 'error'
      ? AlertCircle
      : tone === 'warn'
        ? TriangleAlert
        : CheckCircle2
  return (
    <div className={`notice notice-${tone}`}>
      <Icon size={18} className={tone === 'loading' ? 'spin' : ''} />
      <span>{children}</span>
    </div>
  )
}
