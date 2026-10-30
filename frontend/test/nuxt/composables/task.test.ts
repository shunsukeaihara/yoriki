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

  it('行ごとの誤り（many=True の辞書）から項目の誤りを引く', () => {
    const errors = { '1': { name: ['同じ名前の項目があります'] } }
    expect(rowErrorsOf(errors, 1, 'name')).toEqual(['同じ名前の項目があります'])
    expect(rowErrorsOf(errors, 0, 'name')).toBeUndefined()
    expect(rowErrorsOf(['入力の項目は30個までです'], 0, 'name')).toBeUndefined()
  })
})

describe('inputErrorsOf', () => {
  it('手動実行の誤りは、キーが camelCase に変換されているので、項目の名前も変換して引く', () => {
    const errors = { invoiceFile: ['この項目は必須です'], amount: '整数を入力してください' }
    expect(inputErrorsOf(errors, 'invoice_file')).toEqual(['この項目は必須です'])
    expect(inputErrorsOf(errors, 'amount')).toEqual(['整数を入力してください'])
    expect(inputErrorsOf(errors, 'vendor')).toBeUndefined()
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
