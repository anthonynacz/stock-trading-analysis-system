/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        page: '#0f1117',
        card: '#161b22',
        border: '#21262d',
        'text-primary': '#e6edf3',
        'text-secondary': '#8b949e',
        'strong-buy': '#2ea043',
        buy: '#56d364',
        hold: '#d29922',
        sell: '#f85149',
        'strong-sell': '#da3633',
        'new-entrant': '#58a6ff',
        // Brand accent. Mapped to Tailwind's emerald palette so all
        // standard shade utilities (accent-50..950, /opacity, etc.) work.
        // Replacing the brand color later = swap this block alone.
        accent: {
          50:  '#ecfdf5',
          100: '#d1fae5',
          200: '#a7f3d0',
          300: '#6ee7b7',
          400: '#34d399',
          500: '#10b981',
          600: '#059669',
          700: '#047857',
          800: '#065f46',
          900: '#064e3b',
          950: '#022c22',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
