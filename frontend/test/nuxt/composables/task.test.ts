import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it } from 'vitest'

describe('入力の項目の行', () => {
  it('選択肢は1行に1つの文で編集し、送るときに空行を除いた並びにする。型に関係のない値は送らない', () => {
    const rows = toInputRows([
      {
        name: 'kind',
        label: '種類',
        type: TaskInputType.CHOICE,
        required: true,
        description: '',
        choices: ['請求書', '見積書'],
        maxLength: null,
      },
    ])
    expect(rows[0]!.choicesText).toBe('請求書\n見積書')
    rows[0]!.choicesText = '請求書\n\n 納品書 \n'
    rows[0]!.maxLength = 10
    expect(fromInputRows(rows)).toEqual([
      {
        name: 'kind',
        label: '種類',
        type: TaskInputType.CHOICE,
        required: true,
        description: '',
        choices: ['請求書', '納品書'],
        maxLength: null,
      },
    ])
    rows[0]!.type = TaskInputType.STRING
    expect(fromInputRows(rows)[0]).toMatchObject({ choices: [], maxLength: 10 })
  })

  it('行ごとの誤り（many=True の並びと辞書）から項目の誤りを引く', () => {
    for (const errors of [[{}, { name: ['名前を入力してください'] }], { '1': { name: ['名前を入力してください'] } }]) {
      expect(rowErrorsOf(errors, 1, 'name')).toEqual(['名前を入力してください'])
      expect(rowErrorsOf(errors, 0, 'name')).toBeUndefined()
    }
    // 項目全体の誤り（文の並び）は行の誤りではない
    expect(rowErrorsOf(['入力の項目は30個までです'], 0, 'name')).toBeUndefined()
  })
})

describe('taskInputErrors・inputErrorsOf', () => {
  it('手動実行の誤りは、camelCase に変換する前の応答から項目の名前のまま引く', () => {
    const error = new AxiosError('bad', '400', undefined, undefined, {
      status: 400,
      statusText: 'Bad Request',
      headers: { 'content-type': 'application/json' },
      config: { headers: new AxiosHeaders() },
      data: { inputs: { foo_bar: ['この項目は必須です'], foo__bar: '整数を入力してください' } },
    })
    const errors = taskInputErrors(error) as Record<string, unknown>
    expect(inputErrorsOf(errors, 'foo_bar')).toEqual(['この項目は必須です'])
    expect(inputErrorsOf(errors, 'foo__bar')).toEqual(['整数を入力してください'])
    expect(inputErrorsOf(errors, 'vendor')).toBeUndefined()
  })

  it('入力全体の誤りは文の並び、400以外は誤りなし', () => {
    const error = (status: number, data: unknown) =>
      new AxiosError('bad', String(status), undefined, undefined, {
        status,
        statusText: '',
        headers: { 'content-type': 'application/json' },
        config: { headers: new AxiosHeaders() },
        data,
      })
    expect(taskInputErrors(error(400, { inputs: ['JSONのオブジェクトで入力してください'] }))).toEqual([
      'JSONのオブジェクトで入力してください',
    ])
    expect(taskInputErrors(error(409, { detail: '競合' }))).toBeUndefined()
  })
})

describe('taskChoiceParams', () => {
  it('候補のAPIにはタスクの範囲と部門を渡す', () => {
    expect(taskChoiceParams({ scope: TaskScope.DEPARTMENT, departmentInfo: { id: 'd1', name: '経理部' } })).toEqual({
      scope: TaskScope.DEPARTMENT,
      department: 'd1',
      pageSize: 200,
    })
    expect(taskChoiceParams({ scope: TaskScope.COMPANY, departmentInfo: null }).department).toBeNull()
  })
})
