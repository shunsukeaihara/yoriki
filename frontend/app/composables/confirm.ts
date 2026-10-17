import { DConfirmModal } from '#components'

export type ConfirmOption = {
  title: string
  description?: string
  confirmLabel?: string
  cancelLabel?: string
  color?: 'primary' | 'error'
}

/** 確認ダイアログを開き、OK なら true を返す */
export const useConfirmDialog = () => {
  const modal = useOverlay().create(DConfirmModal)
  return async (option: ConfirmOption): Promise<boolean> => {
    const result = await modal.open(option).result
    return result === true
  }
}
