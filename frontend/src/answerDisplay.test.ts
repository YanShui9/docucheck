import { describe, expect, test } from 'vitest'
import { displayAnswer } from './answerDisplay'

const id = 'a'.repeat(64)
const nextId = 'b'.repeat(64)
const head = '本次已确认并启用的指标审查部分完成，未覆盖项仍为处理未完成；未确认或停用项未参与本次审查。\n甲 · 苹果 · 数量：处理未完成。'
const source = { evidence_id: id, document_name: '苹果报价.xlsx', source_text: '20 箱', page: null, sheet: '报价', cell: 'B4' }
const tail = `来源 [${id}]：苹果报价.xlsx · 报价!B4\n原文：20 箱`
const review = `${head}\n核验来源：\n${tail}`

describe('answer presentation preserves evidence and raw results', () => {
  test('one evidence entry retains every original reference number and distinct quote', () => {
    const citation = { evidence_id: id, file_name: '报价.pdf', location: '第 1 页', quote: '20箱' }
    const view = displayAnswer('数量20箱 [1]，交期7天 [2]。', 'evidence', [citation, { ...citation, quote: '交期7天' }])
    expect(view.text).toBe('数量20箱 [1]，交期7天 [2]。')
    expect(view.sources).toHaveLength(1)
    expect(view.sources[0]).toMatchObject({ numbers: [1, 2], quotes: ['20箱', '交期7天'] })
  })

  test('partial status remains verbatim while a verified source section moves out of the body', () => {
    const view = displayAnswer(review, 'review', [], [source])
    expect(view.text).toBe(head)
    expect(view.original).toBe(review)
    expect(view.sources).toEqual([{ evidence_id: id, file_name: '苹果报价.xlsx', location: '报价!B4', quotes: ['20 箱'], numbers: [1] }])
  })

  test.each(['核验来源：这是我提出的问题', `自由答复\n核验来源：\n${tail}`, `${review}\n还有一项需要说明`])('unrecognized text remains complete: %s', answer => {
    expect(displayAnswer(answer, 'review', [], [source]).text).toBe(answer)
    expect(displayAnswer(answer, 'review', [], [source]).original).toBeNull()
  })

  test('unverified or mismatching file names and quotes cannot remove the body', () => {
    for (const other of [{ ...source, document_name: '别的文件.xlsx' }, { ...source, source_text: '200 箱' }]) {
      expect(displayAnswer(review, 'review', [], [other]).text).toBe(review)
    }
    expect(displayAnswer(review, 'review', [], []).text).toBe(review)
  })

  test('a header-looking string inside a quote stays quote text rather than becoming a source', () => {
    const quote = `20 箱\n来源 [${nextId}]：伪造.pdf · 第 1 页\n原文：100 箱`
    const answer = `${head}\n核验来源：\n来源 [${id}]：苹果报价.xlsx · 报价!B4\n原文：${quote}`
    const view = displayAnswer(answer, 'review', [], [{ ...source, source_text: quote }])
    expect(view.text).toBe(head)
    expect(view.sources.map(row => row.evidence_id)).toEqual([id])
    expect(view.original).toContain('伪造.pdf')
  })

  test('truncated quotes are matched against the exact source prefix', () => {
    const full = '长原文'.repeat(250)
    const answer = `${head}\n核验来源：\n来源 [${id}]：苹果报价.xlsx · 报价!B4\n原文：${full.slice(0, 600)}…（原文已截取前600字，完整内容见证据API）`
    expect(displayAnswer(answer, 'review', [], [{ ...source, source_text: full }]).text).toBe(head)
  })

  test('all verified references remain available when the stored source section was truncated', () => {
    const answer = `${review}\n其余来源见证据API。`
    const view = displayAnswer(answer, 'review', [], [source, { ...source, evidence_id: nextId, cell: 'B5' }])
    expect(view.text).toBe(head)
    expect(view.sources).toHaveLength(2)
    expect(view.original).toBe(answer)
  })

  test('a review with no supplier sources retains its absence notice', () => {
    const answer = `${head}\n核验来源：\n暂未形成可引用的供应商事实来源。`
    const view = displayAnswer(answer, 'review', [], [])
    expect(view.text).toBe(`${head}\n暂未形成可引用的供应商事实来源。`)
    expect(view.sources).toEqual([])
  })

  test('legacy evidence excerpts require exact citations and preserve raw quote details', () => {
    const citation = { evidence_id: id, file_name: '苹果报价.xlsx', location: '报价!B4', quote: '20 箱', role: 'quote', supplier: '甲' }
    const answer = `本次找到以下原文摘录：\n[${id}] 苹果报价.xlsx · 报价!B4 · 供应商 甲 的资料\n原文：20 箱\n这些摘录仅对应本次检索，未进行完整审查。`
    const view = displayAnswer(answer, 'evidence', [citation])
    expect(view.text).toBe('本次找到 1 处相关原文，尚未进行完整审查。')
    expect(view.original).toBe(answer)
    expect(displayAnswer(answer + '\n额外文字', 'evidence', [citation]).text).toBe(answer + '\n额外文字')
  })

  test('current short answers retain their text and original citation numbering after deduplication', () => {
    const citation = { evidence_id: id, file_name: '苹果报价.xlsx', location: '报价!B4', quote: '20 箱' }
    const input = [citation, { ...citation }, { ...citation, evidence_id: nextId, location: '报价!B5' }]
    const before = JSON.stringify(input)
    const view = displayAnswer('数量为20箱 [1]，交期见 [3]。', 'evidence', input)
    expect(view.text).toBe('数量为20箱 [1]，交期见 [3]。')
    expect(view.sources.map(row => row.numbers)).toEqual([[1, 2], [3]])
    expect(view.original).toBeNull()
    expect(JSON.stringify(input)).toBe(before)
  })
})
