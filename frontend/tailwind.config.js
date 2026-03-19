/** @type {import('tailwindcss').Config} */
module.exports = {
    content: ['./src/**/*.{ts,tsx,js,jsx}'],
    theme: {
        extend: {
            colors: {
                superset: {
                    primary: '#20A7C9',
                    'primary-dark': '#1A85A0',
                    secondary: '#484848',
                    bg: '#F5F5F5',
                    'bg-dark': '#1B1B2F',
                    surface: '#FFFFFF',
                    'surface-dark': '#262640',
                    accent: '#FCC700',
                    error: '#E04355',
                    success: '#5AC189',
                },
            },
            fontFamily: {
                sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
            },
            boxShadow: {
                panel: '0 8px 32px rgba(0, 0, 0, 0.12)',
                'panel-hover': '0 12px 48px rgba(0, 0, 0, 0.18)',
                button: '0 4px 14px rgba(32, 167, 201, 0.4)',
            },
            animation: {
                'fade-in': 'fadeIn 0.2s ease-out',
                'slide-up': 'slideUp 0.3s cubic-bezier(0.16, 1, 0.3, 1)',
                'pulse-soft': 'pulseSoft 2s ease-in-out infinite',
            },
            keyframes: {
                fadeIn: {
                    '0%': { opacity: '0' },
                    '100%': { opacity: '1' },
                },
                slideUp: {
                    '0%': { opacity: '0', transform: 'translateY(16px) scale(0.96)' },
                    '100%': { opacity: '1', transform: 'translateY(0) scale(1)' },
                },
                pulseSoft: {
                    '0%, 100%': { opacity: '1' },
                    '50%': { opacity: '0.7' },
                },
            },
        },
    },
    plugins: [],
};
