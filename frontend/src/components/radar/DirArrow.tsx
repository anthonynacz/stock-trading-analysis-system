import type { RadarDirection } from '../../types';
import { DIR_TEXT_CLASS, dirArrow } from './model';

/** ▲ / ▼ in the direction colour, with a screen-reader word. */
export function DirArrow({ dir, className = 'text-xs' }: { dir: RadarDirection; className?: string }) {
  return (
    <span className={`${DIR_TEXT_CLASS[dir] ?? ''} ${className}`}>
      <span aria-hidden="true">{dirArrow(dir)}</span>
      <span className="sr-only">{dir === 'down' ? 'Down' : 'Up'}</span>
    </span>
  );
}
