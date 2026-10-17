import type { AxiosInstance } from 'axios'

/** DRF のカーソル方式ページングの応答 */
export type Paginated<T> = {
  results: T[]
  next: string | null
  previous: string | null
}

export type ListParams = Record<string, unknown> & {
  pageSize?: number
  search?: string
  ordering?: string
}

/** ReadOnlyModelViewSet に対応するメソッド */
export function readOnlyResource<T, TParams extends ListParams = ListParams>(
  http: AxiosInstance,
  base: string,
  id?: string,
) {
  return {
    list: (params?: TParams) => http.get<Paginated<T>, Paginated<T>>(`${base}/`, { params }),
    get: () => http.get<T, T>(`${base}/${id}/`),
  }
}

/** ModelViewSet に対応するメソッド */
export function resource<T, W = Partial<T>, TParams extends ListParams = ListParams>(
  http: AxiosInstance,
  base: string,
  id?: string,
) {
  return {
    ...readOnlyResource<T, TParams>(http, base, id),
    create: (data: W) => http.post<T, T>(`${base}/`, data),
    update: (data: W) => http.put<T, T>(`${base}/${id}/`, data),
    patch: (data: Partial<W>) => http.patch<T, T>(`${base}/${id}/`, data),
    delete: () => http.delete<undefined, undefined>(`${base}/${id}/`),
  }
}
