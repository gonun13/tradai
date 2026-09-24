<script setup lang="ts">
const route = useRoute()

await navigateTo(
  {
    path: '/portfolio',
    query: route.query,
  },
  { replace: true },
)
</script>

<template>
  <p class="mute">Opening Portfolio…</p>
</template>
