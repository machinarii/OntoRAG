import { describe, expect, test } from 'bun:test'
import { bibliographicAuthors, bibliographicTitle } from './documentDisplayName'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const base = { id: 'd1', file_path: 'book.textpack' } as any

describe('bibliographic display', () => {
  test('title from metadata is trimmed', () => {
    expect(bibliographicTitle({ ...base, metadata: { bibliographic: { title: ' T ' } } })).toBe('T')
  })
  test('no title -> null', () => {
    expect(bibliographicTitle(base)).toBeNull()
    expect(bibliographicTitle({ ...base, metadata: { bibliographic: { title: '' } } })).toBeNull()
    expect(bibliographicTitle({ ...base, metadata: { bibliographic: { title: 42 } } })).toBeNull()
  })
  test('authors joined, blanks and non-strings dropped', () => {
    expect(
      bibliographicAuthors({ ...base, metadata: { bibliographic: { authors: ['A', '', 3, 'B'] } } })
    ).toBe('A, B')
    expect(bibliographicAuthors(base)).toBeNull()
    expect(bibliographicAuthors({ ...base, metadata: { bibliographic: { authors: 'A' } } })).toBeNull()
  })
})
