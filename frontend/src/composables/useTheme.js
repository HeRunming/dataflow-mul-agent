import { ref, watchEffect } from 'vue'

const STORAGE_KEY = 'dataflow-workbench-theme'

function stored() {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

const theme = ref(stored() || 'light')

watchEffect(() => {
  document.documentElement.dataset.theme = theme.value
  try {
    localStorage.setItem(STORAGE_KEY, theme.value)
  } catch {
    /* Private windows may block storage; the theme still applies. */
  }
})

export function useTheme() {
  return { theme, toggle: () => (theme.value = theme.value === 'dark' ? 'light' : 'dark') }
}
