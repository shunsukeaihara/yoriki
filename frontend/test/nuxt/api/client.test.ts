import { describe, expect, it } from 'vitest'

describe('camelProps', () => {
  it('ネストしたキーを camelCase にする', () => {
    expect(camelProps({ display_name: 'a', all_permissions: [{ app_label: 'x' }] })).toEqual({
      displayName: 'a',
      allPermissions: [{ appLabel: 'x' }],
    })
  })

  it('数字で始まるキー（many=True のエラーの行番号）は変えない', () => {
    expect(camelProps({ items: { '1': { unit_price: ['必須'] } } })).toEqual({
      items: { '1': { unitPrice: ['必須'] } },
    })
  })

  it('利用者が決めるキーを持つJSON（Schema・業務結果・Toolの引数・列名）の中は変えない', () => {
    expect(
      camelProps({
        output_schema: { properties: { invoice_no: { type: 'string' } } },
        tool_call: { arguments: { file_path: 'a.txt' } },
        tables: [{ schema: 'dbo', columns: { VendorCode: '取引先コード' } }],
      }),
    ).toEqual({
      outputSchema: { properties: { invoice_no: { type: 'string' } } },
      toolCall: { arguments: { file_path: 'a.txt' } },
      tables: [{ schema: 'dbo', columns: { VendorCode: '取引先コード' } }],
    })
  })
})

describe('snakeProps', () => {
  it('ネストしたキーを snake_case にする', () => {
    expect(snakeProps({ displayName: 'a', items: [{ unitPrice: '1.00' }] })).toEqual({
      display_name: 'a',
      items: [{ unit_price: '1.00' }],
    })
  })

  it('利用者が決めるキーを持つJSONの中は変えない', () => {
    expect(
      snakeProps({
        outputSchema: { properties: { invoiceNo: { type: 'string' } } },
        expectedResult: { VendorCode: 'A01' },
        tables: [{ columns: { vendorName: '取引先名' } }],
      }),
    ).toEqual({
      output_schema: { properties: { invoiceNo: { type: 'string' } } },
      expected_result: { VendorCode: 'A01' },
      tables: [{ columns: { vendorName: '取引先名' } }],
    })
  })

  it('File・Date・FormData は変換せずにそのまま渡す', () => {
    const file = new File(['x'], 'a.txt')
    const date = new Date()
    const formData = new FormData()
    const result = snakeProps<Record<string, unknown>>({ myFile: file, sentAt: date, body: formData })
    expect(result.my_file).toBe(file)
    expect(result.sent_at).toBe(date)
    expect(result.body).toBe(formData)
  })
})
