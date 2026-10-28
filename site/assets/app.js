/* Theme toggle and scroll reveal for the landing page. */
;(function () {
  const root = document.documentElement
  const STORAGE_KEY = 'yoriki-lp-theme'

  function currentTheme() {
    const explicit = root.getAttribute('data-theme')
    if (explicit === 'light' || explicit === 'dark') return explicit
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  }
  function setTheme(theme) {
    root.setAttribute('data-theme', theme)
    try {
      localStorage.setItem(STORAGE_KEY, theme)
    } catch (_) {
      /* ignore */
    }
  }
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') root.setAttribute('data-theme', stored)
  } catch (_) {
    /* ignore */
  }
  const toggle = document.querySelector('.theme-toggle')
  if (toggle) toggle.addEventListener('click', () => setTheme(currentTheme() === 'dark' ? 'light' : 'dark'))

  // Reveal sections as they scroll into view (no-op when reduced motion is requested).
  const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  const targets = document.querySelectorAll('.card, .steps li, .checks li, .stage, .arch, .origin__inner')
  if (!reduce && 'IntersectionObserver' in window) {
    const io = new IntersectionObserver(
      (entries) => {
        for (const e of entries) {
          if (e.isIntersecting) {
            e.target.classList.add('is-visible')
            io.unobserve(e.target)
          }
        }
      },
      { rootMargin: '0px 0px -8% 0px', threshold: 0.1 },
    )
    targets.forEach((el, i) => {
      el.classList.add('reveal')
      el.style.transitionDelay = `${(i % 6) * 60}ms`
      io.observe(el)
    })
  }
})()
