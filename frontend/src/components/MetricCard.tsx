interface MetricCardProps {
  label: string;
  value: string | number;
  change?: number;
  icon?: string;
  subtitle?: string;
}

export default function MetricCard({
  label,
  value,
  change,
  icon,
  subtitle,
}: MetricCardProps) {
  return (
    <div className="bg-white dark:bg-zinc-900 rounded-lg border border-zinc-200 dark:border-zinc-800 p-5">
      <div className="flex items-start justify-between">
        <div>
          <p className="text-sm font-medium text-zinc-500 dark:text-zinc-400">
            {label}
          </p>
          <p className="mt-1 text-2xl font-semibold text-zinc-900 dark:text-white">
            {value}
          </p>
          {subtitle && (
            <p className="mt-1 text-xs text-zinc-400">{subtitle}</p>
          )}
        </div>
        {icon && (
          <span className="text-2xl">{icon}</span>
        )}
      </div>
      {change !== undefined && (
        <div className="mt-3">
          <span
            className={`text-xs font-medium ${
              change >= 0
                ? "text-emerald-600 dark:text-emerald-400"
                : "text-red-600 dark:text-red-400"
            }`}
          >
            {change >= 0 ? "↑" : "↓"} {Math.abs(change).toFixed(1)}%
          </span>
          <span className="text-xs text-zinc-400 ml-1">vs last period</span>
        </div>
      )}
    </div>
  );
}
