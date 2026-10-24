import type { AssistantCitation } from './api'

export interface ReviewSource {
  evidence_id: string
  document_name: string
  source_text: string
  page: number | null
  sheet: string | null
  cell: string | null
}
export type DisplaySource = Omit<AssistantCitation, 'quote'> & { numbers: number[]; quotes: string[] }
type Citation = AssistantCitation & { role?: string; supplier?: string | null }

const REVIEW_PREFIXES = [
  '本次已确认并启用的指标审查完成；未确认或停用项未参与本次审查。',
  '本次已确认并启用的指标审查部分完成，未覆盖项仍为处理未完成；未确认或停用项未参与本次审查。',
]
const SOURCE_MARKER = '\n核验来源：\n'
const DEGRADED_NOTICES = [
  '请先确认资料与品目的对应；本次没有扩大到其他品目的资料。',
  '现有向量块包含范围外原文，本次只采用确认范围内的词法检索。',
  '范围过滤超过本次预算，本次只采用确认范围内的词法检索。',
  '本次采用词法检索，语义索引尚不可用。',
]

export function hasReviewSources(answer: string, kind?: string): boolean {
  return kind === 'review' && REVIEW_PREFIXES.some(prefix => answer.startsWith(prefix)) && answer.includes(SOURCE_MARKER)
}

function unique(citations: Citation[]): DisplaySource[] {
  const seen = new Map<string, DisplaySource>()
  citations.forEach((citation, index) => {
    const existing = seen.get(citation.evidence_id)
    if (existing) {
      existing.numbers.push(index + 1)
      if (!existing.quotes.includes(citation.quote)) existing.quotes.push(citation.quote)
    } else {
      seen.set(citation.evidence_id, { evidence_id: citation.evidence_id, file_name: citation.file_name,
        location: citation.location, quotes: [citation.quote], numbers: [index + 1] })
    }
  })
  return [...seen.values()]
}

function location(source: ReviewSource): string | null {
  if (source.page !== null) return `第 ${source.page} 页`
  return source.sheet && source.cell ? `${source.sheet}!${source.cell}` : null
}

export function validReviewSources(value: unknown): value is ReviewSource[] {
  if (!Array.isArray(value)) return false
  const seen = new Set<string>()
  return value.every(row => {
    if (!row || typeof row !== 'object' || typeof row.evidence_id !== 'string' ||
        !/^[a-f0-9]{64}$/.test(row.evidence_id) || seen.has(row.evidence_id) ||
        typeof row.document_name !== 'string' || !row.document_name || typeof row.source_text !== 'string' ||
        !(row.page === null || Number.isInteger(row.page) && row.page > 0) ||
        !(row.sheet === null || typeof row.sheet === 'string') || !(row.cell === null || typeof row.cell === 'string')) return false
    seen.add(row.evidence_id)
    return true
  })
}

export function displayAnswer(answer: string, kind?: string, citations: Citation[] = [], reviewSources?: ReviewSource[]): {
  text: string; sources: DisplaySource[]; original: string | null
} {
  const fallback = { text: answer, sources: unique(citations), original: null }
  if (kind === 'evidence' && citations.length && answer.startsWith('本次找到以下原文摘录：\n')) {
    const lines = ['本次找到以下原文摘录：', ...citations.map(c => {
      const scope = c.role === 'requirement' ? '买方要求' : `供应商 ${c.supplier} 的资料`
      return `[${c.evidence_id}] ${c.file_name} · ${c.location} · ${scope}\n原文：${c.quote}`
    }), '这些摘录仅对应本次检索，未进行完整审查。']
    const expected = lines.join('\n')
    const notice = answer.slice(expected.length)
    if (answer.startsWith(expected) && (!notice || DEGRADED_NOTICES.some(text => notice === '\n' + text))) {
      return { text: `本次找到 ${fallback.sources.length} 处相关原文，尚未进行完整审查。${notice}`,
        sources: fallback.sources, original: answer }
    }
  }
  if (!hasReviewSources(answer, kind) || reviewSources === undefined || !validReviewSources(reviewSources)) return fallback
  const index = answer.indexOf(SOURCE_MARKER)
  const text = answer.slice(0, index)
  let rest = answer.slice(index + SOURCE_MARKER.length)
  if (rest === '暂未形成可引用的供应商事实来源。' && !reviewSources.length) {
    return { text: `${text}\n${rest}`, sources: [], original: answer }
  }
  const sourceMap = new Map(reviewSources.map(source => [source.evidence_id, source]))
  const ordered: ReviewSource[] = []
  // Consume each complete, authoritative quote. Header-looking document text
  // inside a quote is never parsed as a new citation.
  while (rest && rest !== '其余来源见证据API。') {
    const match = /^来源 \[([a-f0-9]{64})\]：/.exec(rest)
    const source = match ? sourceMap.get(match[1]!) : undefined
    if (!source || ordered.includes(source)) return fallback
    const at = location(source)
    if (!at) return fallback
    const characters = Array.from(source.source_text)
    const quote = characters.slice(0, 600).join('') + (characters.length > 600 ? '…（原文已截取前600字，完整内容见证据API）' : '')
    const expected = `来源 [${source.evidence_id}]：${source.document_name} · ${at}\n原文：${quote}`
    if (!rest.startsWith(expected)) return fallback
    ordered.push(source)
    rest = rest.slice(expected.length)
    if (rest && !rest.startsWith('\n')) return fallback
    if (rest) rest = rest.slice(1)
  }
  if (!ordered.length && rest !== '其余来源见证据API。') return fallback
  const all = [...ordered, ...reviewSources.filter(source => !ordered.includes(source))]
  return { text, original: answer, sources: all.map((source, index) => ({
    evidence_id: source.evidence_id, file_name: source.document_name,
    location: location(source) ?? '原文位置', quotes: [source.source_text], numbers: [index + 1],
  })) }
}
