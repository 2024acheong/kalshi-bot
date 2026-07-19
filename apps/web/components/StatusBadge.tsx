type StatusBadgeProps = {
  value: string | null | undefined
}

export function StatusBadge({ value }: StatusBadgeProps) {
  const normalized = value ?? 'unknown'
  const className =
    normalized === 'allow'
      ? 'badge badgeGreen'
      : normalized === 'block'
        ? 'badge badgeRed'
        : normalized === 'reduce_only'
          ? 'badge badgeYellow'
          : 'badge badgeGray'

  return <span className={className}>{normalized}</span>
}
