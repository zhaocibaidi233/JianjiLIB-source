import { describe, expect, it } from 'vitest'
import { router } from '../src/router'

describe('reader portal routes', () => {
  it('registers the catalog as a protected reader page', () => {
    const resolved = router.resolve('/reader/catalog')
    const leaf = resolved.matched.at(-1)

    expect(leaf?.path).toBe('/reader/catalog')
    expect(leaf?.meta.title).toBe('馆藏检索')
  })

  it('keeps the reader home as the index route', () => {
    const resolved = router.resolve('/reader')
    expect(resolved.matched.at(-1)?.path).toBe('/reader')
    expect(resolved.matched.at(-1)?.meta.title).toBe('发现')
  })

  it('accepts search keywords on the catalog route', () => {
    const resolved = router.resolve({ path: '/reader/catalog', query: { q: '人类简史' } })
    expect(resolved.query.q).toBe('人类简史')
    expect(resolved.href).toContain('/reader/catalog?q=')
  })
})
