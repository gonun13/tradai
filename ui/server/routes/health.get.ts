export default defineEventHandler(async () => {
  const config = useRuntimeConfig()
  const base = String(config.apiInternalBase).replace(/\/$/, '')
  return await $fetch(`${base}/health`)
})
