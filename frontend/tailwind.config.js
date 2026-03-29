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
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
      },
    },
  },
  plugins: [],
}
