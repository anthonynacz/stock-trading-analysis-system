import { ReactNode } from 'react';

export interface TabItem<K extends string> {
  key: K;
  label: ReactNode;
  title?: string;
}

const SIZE = {
  sm: 'px-3 py-2 text-xs',
  md: 'px-4 py-2 text-sm',
} as const;

/** Underline-style tab bar for page-level sections. */
export function TabBar<K extends string>({
  tabs,
  active,
  onChange,
  size = 'sm',
  className = '',
}: {
  tabs: TabItem<K>[];
  active: K;
  onChange: (key: K) => void;
  size?: keyof typeof SIZE;
  className?: string;
}) {
  return (
    <div role="tablist" className={`flex gap-1 border-b border-border overflow-x-auto ${className}`}>
      {tabs.map((t) => {
        const selected = t.key === active;
        return (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={selected}
            title={t.title}
            onClick={() => onChange(t.key)}
            className={`${SIZE[size]} font-medium border-b-2 whitespace-nowrap transition-colors ${
              selected
                ? 'border-accent-500 text-text-primary'
                : 'border-transparent text-text-secondary hover:text-text-primary'
            }`}
          >
            {t.label}
          </button>
        );
      })}
    </div>
  );
}

export default TabBar;
