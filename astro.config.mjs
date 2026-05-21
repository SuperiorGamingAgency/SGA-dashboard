import { defineConfig } from 'astro/config';
import tailwind from '@astrojs/tailwind'; // Updated this line

export default defineConfig({
  integrations: [tailwind()], // Updated this line
});