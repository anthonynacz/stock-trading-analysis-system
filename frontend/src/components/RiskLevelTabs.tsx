import { ReactNode } from 'react';
import { SegmentedControl } from './ui/SegmentedControl';
import { RISK_LEVELS, RiskLevel } from '../utils/options';

export function RiskLevelTabs({
  active,
  onChange,
  indicator,
  fullWidth = false,
  size = 'sm',
}: {
  active: RiskLevel;
  onChange: (level: RiskLevel) => void;
  /** Trailing node per tab (result dot, count); receives the tab's active state. */
  indicator?: (level: RiskLevel, isActive: boolean) => ReactNode;
  fullWidth?: boolean;
  size?: 'xs' | 'sm';
}) {
  return (
    <SegmentedControl
      options={RISK_LEVELS.map((level) => ({
        key: level,
        label: level,
        badge: indicator ? (isActive: boolean) => indicator(level, isActive) : undefined,
      }))}
      value={active}
      onChange={onChange}
      size={size}
      fullWidth={fullWidth}
      optionClassName={() => 'capitalize'}
    />
  );
}

export default RiskLevelTabs;
