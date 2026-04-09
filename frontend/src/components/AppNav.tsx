import { NavLink } from 'react-router-dom';

const links = [
  { to: '/', label: 'Dashboard' },
  { to: '/universe', label: 'Universe' },
  { to: '/research', label: 'Research' },
] as const;

export default function AppNav() {
  return (
    <nav className="bg-card border-b border-border px-4">
      <div className="max-w-7xl mx-auto flex items-center h-10 gap-6">
        <span className="text-sm font-bold text-text-primary tracking-wide mr-2">
          EdgeFlow
        </span>
        {links.map((l) => (
          <NavLink
            key={l.to}
            to={l.to}
            end={l.to === '/'}
            className={({ isActive }) =>
              `text-sm transition-colors ${
                isActive
                  ? 'text-text-primary font-semibold border-b-2 border-purple-500'
                  : 'text-text-secondary hover:text-text-primary'
              }`
            }
          >
            {l.label}
          </NavLink>
        ))}
      </div>
    </nav>
  );
}
