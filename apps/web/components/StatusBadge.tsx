type StatusBadgeProps = {
  value: string | null | undefined
}

export function StatusBadge({ value }: StatusBadgeProps) {
  const normalized = value ?? 'unknown'
  const className =
    normalized === 'allow' || normalized === 'enabled' || normalized === 'real' || normalized === 'live'
      ? 'badge badgeGreen'
      : normalized === 'block' || normalized === 'disabled' || normalized === 'synthetic'
        ? 'badge badgeRed'
        : normalized === 'reduce_only' || normalized === 'paused' || normalized === 'paper'
          ? 'badge badgeYellow'
          : 'badge badgeGray'

  return <span className={className}>{normalized}</span>
}
