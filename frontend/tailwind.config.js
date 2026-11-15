/** @type {import('tailwindcss').Config} */
export default {
  content: ["./public/**/*.html", "./src/**/*.{vue,js,ts,jsx,tsx}"],
  theme: {
    extend: {
      colors: {
        ojSuccess: {
          '50': '#F1F9EC',
          '100': '#E4F2DA',
          '200': '#D7ECC7',
          '300': '#C9E5A4',
          '400': '#A2D380',
          '500': '#67C23A',
        }
      }
    },
  },
  plugins: [],
}

