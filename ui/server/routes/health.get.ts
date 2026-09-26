// Explicit return type: `$fetch` is typed from Nitro's route table, which includes this route.
export default defineEventHandler(async (): Promise<unknown> => {
  const config = useRuntimeConfig()
  const base = String(config.apiInternalBase).replace(/\/$/, '')
  return await $fetch(`${base}/health`, { timeout: 5000 })
})
