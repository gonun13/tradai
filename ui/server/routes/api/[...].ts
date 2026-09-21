export default defineEventHandler(async (event) => {
  const config = useRuntimeConfig()
  const base = String(config.apiInternalBase).replace(/\/$/, '')
  const path = getRequestURL(event).pathname.replace(/^\/api/, '') || '/'
  return proxyRequest(event, `${base}${path}${getRequestURL(event).search}`)
})
