// https://nuxt.com/docs/api/configuration/nuxt-config
export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: false },
  runtimeConfig: {
    apiInternalBase: 'http://127.0.0.1:8080',
    public: {
      apiBase: 'http://127.0.0.1:8080',
    },
  },
  nitro: {
    preset: 'node-server',
  },
})
