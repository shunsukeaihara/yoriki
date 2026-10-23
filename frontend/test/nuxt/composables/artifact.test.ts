import { mockNuxtImport } from '@nuxt/test-utils/runtime'
import { AxiosError, AxiosHeaders } from 'axios'
import { describe, expect, it, vi } from 'vitest'

const { mockUpload } = vi.hoisted(() => ({ mockUpload: vi.fn() }))
mockNuxtImport('useAPI', () => () => ({ conversations: () => ({ artifacts: () => ({ upload: mockUpload }) }) }))

const artifact = (id: string) =>
  ({
    id,
    kind: ArtifactKind.ATTACHMENT,
    name: `${id}.pdf`,
    path: '',
    contentType: 'application/pdf',
    size: 1,
    run: null,
    created: '2026-10-23T00:00:00Z',
  }) satisfies Artifact

describe('useAttachmentUploads', () => {
  it('選んだファイルをアップロードし、送れる ID を返す。失敗したものは残してエラーを出す', async () => {
    const error = new AxiosError('bad', '400', undefined, undefined, {
      status: 400,
      statusText: 'Bad Request',
      headers: { 'content-type': 'application/json' },
      config: { headers: new AxiosHeaders() },
      data: { file: ['ファイルが大きすぎます（50MBまで）'] },
    })
    mockUpload.mockResolvedValueOnce(artifact('a1')).mockRejectedValueOnce(error)
    const uploads = useAttachmentUploads()
    uploads.add([new File(['a'], 'a.pdf'), new File(['b'], 'b.pdf')])
    await uploads.upload('c1')

    expect(mockUpload).toHaveBeenCalledTimes(2)
    expect(uploads.ids.value).toEqual(['a1'])
    expect(uploads.failed.value).toBe(true)
    expect(uploads.items.value[1]!.error).toContain('ファイルが大きすぎます')

    // 失敗したものを外せば、送れる
    uploads.remove(uploads.items.value[1]!.key)
    expect(uploads.failed.value).toBe(false)
    // アップロード済みのものは送り直さない
    await uploads.upload('c1')
    expect(mockUpload).toHaveBeenCalledTimes(2)
    uploads.clear()
    expect(uploads.items.value).toEqual([])
  })
})

describe('formatFileSize', () => {
  it('B・KB・MB で表す', () => {
    expect(formatFileSize(512)).toBe('512 B')
    expect(formatFileSize(2048)).toBe('2.0 KB')
    expect(formatFileSize(3 * 1024 * 1024)).toBe('3.0 MB')
  })
})
