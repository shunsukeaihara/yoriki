import { mockNuxtImport, mountSuspended } from '@nuxt/test-utils/runtime'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { SkillEvaluationCaseList, SkillEvaluationResultCard } from '#components'

const { caseDelete } = vi.hoisted(() => ({ caseDelete: vi.fn() }))

mockNuxtImport('useAPI', () => () => ({
  skills: () => ({
    evaluationCases: () => ({ delete: caseDelete }),
    fileUrl: (id: string) => `/api/files/${id}`,
  }),
}))
mockNuxtImport('useConfirmDialog', () => () => () => Promise.resolve(true))

const user = { id: 'u1', email: 'taro@example.co.jp', displayName: '太郎' }
const skill = (canPublish: boolean) =>
  ({
    id: 's1',
    name: '請求書の照合',
    scope: SkillScope.DEPARTMENT,
    canEdit: true,
    canPublish,
    version: 1,
  }) as Skill
const evaluationCase = (id: string, name: string, required: boolean): SkillEvaluationCase => ({
  id,
  name,
  prompt: '請求書を照合して',
  criteria: '',
  expectedResult: null,
  required,
  filesInfo: [],
  updatedByInfo: user,
  created: '2026-10-25T00:00:00Z',
  updated: '2026-10-25T00:00:00Z',
})

const flushPromises = () => new Promise((resolve) => setTimeout(resolve))

describe('SkillEvaluationCaseList', () => {
  beforeEach(() => {
    caseDelete.mockReset().mockResolvedValue(undefined)
  })

  it('必須のケースは、公開の権限がなければ変更・削除のボタンを出さない', async () => {
    const cases = [evaluationCase('c1', '必須のケース', true), evaluationCase('c2', '任意のケース', false)]
    const wrapper = await mountSuspended(SkillEvaluationCaseList, {
      props: { modelValue: cases, skill: skill(false) },
    })
    expect(wrapper.text()).toContain('公開の前に必須')
    expect(wrapper.findAll('button[aria-label="削除"]')).toHaveLength(1)
    await wrapper.setProps({ skill: skill(true) })
    expect(wrapper.findAll('button[aria-label="削除"]')).toHaveLength(2)
  })

  it('削除すると一覧から除く', async () => {
    const wrapper = await mountSuspended(SkillEvaluationCaseList, {
      props: { modelValue: [evaluationCase('c2', '任意のケース', false)], skill: skill(false) },
    })
    await wrapper.find('button[aria-label="削除"]').trigger('click')
    await flushPromises()
    expect(caseDelete).toHaveBeenCalled()
    expect(wrapper.emitted('update:modelValue')![0]![0]).toEqual([])
  })
})

const check = (overrides: Partial<EvaluationCheck>): EvaluationCheck => ({
  completed: true,
  statusDisplay: '完了',
  error: '',
  resultRequired: true,
  resultSubmitted: true,
  expectedChecked: true,
  mismatches: [],
  passed: true,
  reply: '照合しました。',
  result: { ok: true },
  cost: '0.01',
  ...overrides,
})

const result: SkillEvaluationResult = {
  order: 1,
  case: 'c1',
  caseName: '通常の請求書',
  required: true,
  caseData: { prompt: '請求書を照合して', criteria: '合計を確かめる', expectedResult: { ok: true }, files: [] },
  state: EvaluationResultState.DONE,
  stateDisplay: '完了',
  baseStatus: '完了',
  draftStatus: '完了',
  baseCheck: check({ passed: false, mismatches: [{ path: 'ok', expected: true, actual: false }], reply: '旧版' }),
  draftCheck: check({ reply: '新版' }),
  passed: true,
  verdict: EvaluationVerdict.BETTER,
  verdictDisplay: '良くなった',
  judgeReason: 'Draftの方が正確です。',
  judgeNote: '',
}

describe('SkillEvaluationResultCard', () => {
  it('通過・判定と、両方の結果・期待値との違いを出す', async () => {
    const wrapper = await mountSuspended(SkillEvaluationResultCard, { props: { result, hasBase: true } })
    const text = wrapper.text()
    expect(text).toContain('通過')
    expect(text).toContain('判定：良くなった')
    expect(text).toContain('Draftの方が正確です。')
    expect(text).toContain('公開中の版')
    expect(text).toContain('旧版')
    expect(text).toContain('新版')
    expect(text).toContain('期待する業務結果と違う')
    expect(text).toContain('ok：期待 true・実際 false')
  })

  it('最初の公開の前はDraftだけを出す', async () => {
    const draftOnly = { ...result, baseCheck: null, baseStatus: null, verdict: null, judgeReason: '' }
    const wrapper = await mountSuspended(SkillEvaluationResultCard, {
      props: { result: { ...draftOnly, judgeNote: '公開中の版がないので、比べていません。' }, hasBase: false },
    })
    expect(wrapper.text()).not.toContain('公開中の版の')
    expect(wrapper.text()).toContain('比べていません')
  })
})

describe('evaluationSummary', () => {
  const t = (key: string, params?: Record<string, unknown>) =>
    key.replace('{total}', String(params?.total ?? '')).replace('{passed}', String(params?.passed ?? ''))

  it('評価中と、終わった後の通過の数を分ける', () => {
    expect(evaluationSummary({ status: EvaluationStatus.RUNNING, passed: 0, total: 2 }, t)).toBe('評価中（2件）')
    expect(evaluationSummary({ status: EvaluationStatus.FINISHED, passed: 1, total: 2 }, t)).toBe('2件中1件が通過')
  })
})
