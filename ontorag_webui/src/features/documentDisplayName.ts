import type { DocStatusResponse } from '@/api/ontorag'

/**
 * Bibliographic display helpers for documents produced by a converter engine
 * (pdf2md): the record's file_path names the generated .textpack, while
 * metadata.bibliographic carries the title/authors pdf2md recovered from the
 * source. Both return null when the document has no such metadata so callers
 * fall back to the file name.
 */
export const bibliographicTitle = (doc: DocStatusResponse): string | null => {
  const title = doc.metadata?.bibliographic?.title
  return typeof title === 'string' && title.trim() !== '' ? title.trim() : null
}

export const bibliographicAuthors = (doc: DocStatusResponse): string | null => {
  const authors = doc.metadata?.bibliographic?.authors
  if (!Array.isArray(authors)) return null
  const names = authors.filter(
    (a): a is string => typeof a === 'string' && a.trim() !== ''
  )
  return names.length ? names.join(', ') : null
}
