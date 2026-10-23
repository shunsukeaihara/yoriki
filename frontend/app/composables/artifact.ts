// バックエンドの artifact.models.ArtifactKind と同じ値
export const ArtifactKind = {
  ATTACHMENT: 1,
  OUTPUT: 2,
} as const satisfies Record<string, ArtifactKindEnum>

/** ファイルの大きさの表示（1.2 MB 等） */
export const formatFileSize = (size: number) => {
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

/** ファイルの種類のアイコン */
export const fileIcon = (contentType: string) => {
  if (contentType.startsWith('image/')) return 'i-lucide-file-image'
  if (contentType === 'application/pdf') return 'i-lucide-file-text'
  if (contentType.includes('spreadsheet') || contentType.includes('excel') || contentType === 'text/csv')
    return 'i-lucide-file-spreadsheet'
  if (contentType.startsWith('text/') || contentType.includes('json')) return 'i-lucide-file-code'
  return 'i-lucide-file'
}

export type PendingAttachment = {
  key: string
  file: File
  /** アップロードし終えたもの */
  artifact: Artifact | null
  uploading: boolean
  error: string
}

/**
 * 入力欄で添付するファイル。選んだらアップロードし（会話がまだないときは、会話を作った後に upload() を呼ぶ）、
 * メッセージを送るときにアップロードし終えた添付の ID を付ける。送ったら clear() する
 */
export function useAttachmentUploads() {
  const api = useAPI()
  const items = ref<PendingAttachment[]>([])

  const add = (files: File[] | FileList | null) => {
    for (const file of Array.from(files ?? [])) {
      items.value.push({ key: crypto.randomUUID(), file, artifact: null, uploading: false, error: '' })
    }
  }

  const remove = (key: string) => {
    items.value = items.value.filter((item) => item.key !== key)
  }

  /** まだアップロードしていないものを送る。失敗したものはエラーを持たせて残す（消すか送り直せる） */
  const upload = async (conversationId: string) => {
    await Promise.all(
      items.value
        .filter((item) => !item.artifact && !item.uploading)
        .map(async (item) => {
          item.uploading = true
          item.error = ''
          try {
            item.artifact = await api.conversations(conversationId).artifacts().upload(item.file)
          } catch (err) {
            // 大きすぎる等の理由は file の欄のエラーで返る
            const errors = getValidationError(err)
            item.error = errors.file?.[0] ?? errors.nonFieldErrors?.[0] ?? makeErrorMsg(err)
          } finally {
            item.uploading = false
          }
        }),
    )
  }

  return {
    items,
    add,
    remove,
    upload,
    clear: () => (items.value = []),
    uploading: computed(() => items.value.some((item) => item.uploading)),
    failed: computed(() => items.value.some((item) => item.error)),
    ids: computed(() => items.value.flatMap((item) => (item.artifact ? [item.artifact.id] : []))),
  }
}

/** ファイルを選ぶダイアログと、`target` へのドロップ。選んだ・落としたファイルを `onFiles` に渡す */
export function useFilePicker(
  target: MaybeRefOrGetter<HTMLElement | null | undefined>,
  onFiles: (files: File[]) => void,
) {
  const dialog = useFileDialog({ multiple: true, reset: true })
  dialog.onChange((files) => {
    if (files?.length) onFiles(Array.from(files))
  })
  const { isOverDropZone } = useDropZone(target, {
    onDrop: (files) => {
      if (files?.length) onFiles(files)
    },
  })
  return { open: () => dialog.open(), isOverDropZone }
}
