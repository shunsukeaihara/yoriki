import axios, { type AxiosError, type AxiosInstance, type AxiosRequestTransformer } from 'axios'
import { camelCase, snakeCase } from 'change-case'
import qs from 'qs'

// 応答が返らないケースを永遠に待たずにエラーにする
export const API_TIMEOUT = 15 * 1000
// ファイルを送る要求だけ長くする（例: http.post(url, formData, { timeout: UPLOAD_TIMEOUT })）
export const UPLOAD_TIMEOUT = 60 * 1000

type KeyConverter = (key: string) => string

const convertKeys = (value: unknown, fn: KeyConverter): unknown => {
  if (Array.isArray(value)) return value.map((v) => convertKeys(v, fn))
  // File / Blob / Date / FormData はそのまま送る
  if (value && typeof value === 'object' && Object.getPrototypeOf(value) === Object.prototype) {
    return Object.fromEntries(Object.entries(value).map(([k, v]) => [fn(k), convertKeys(v, fn)]))
  }
  return value
}

/** API の snake_case を camelCase に変換する。数字で始まるキー（many=True のエラーの行番号）は変えない */
export const camelProps = <T = unknown>(value: unknown): T =>
  convertKeys(value, (k) => (/^\d/.test(k) ? k : camelCase(k))) as T
export const snakeProps = <T = unknown>(value: unknown): T => convertKeys(value, snakeCase) as T

const defaultTransformers = (): AxiosRequestTransformer[] => {
  const t = axios.defaults.transformRequest
  if (!t) return []
  return Array.isArray(t) ? t : [t]
}

export type HttpHooks = {
  /** 401（未ログイン）を受けたときの処理（ログイン画面への移動） */
  onUnauthorized?: () => Promise<void>
}

const RELOADED_AT_KEY = 'yoriki:session-lost-reloaded-at'
const RELOAD_INTERVAL = 30 * 1000

/**
 * 前段の認証プロキシ（oauth2-proxy）のセッションが切れたとみなして、画面ごと再読み込みする。
 * オフライン等で再読み込みを繰り返さないよう、30秒以内に一度再読み込みしていたら何もしない（呼び出し元はエラーとして扱う）。
 */
const reloadForSessionLost = (): boolean => {
  try {
    const last = Number(sessionStorage.getItem(RELOADED_AT_KEY) ?? 0)
    if (Date.now() - last < RELOAD_INTERVAL) return false
    sessionStorage.setItem(RELOADED_AT_KEY, String(Date.now()))
  } catch {
    return false
  }
  window.location.reload()
  return true
}

export const createHttp = (baseURL: string, getLocale: () => string, hooks: HttpHooks = {}): AxiosInstance => {
  const http = axios.create({
    baseURL,
    timeout: API_TIMEOUT,
    // 認証は Django のセッション Cookie で行う。書き込みの要求には CSRF トークンを付ける
    withCredentials: true,
    xsrfCookieName: 'csrftoken',
    xsrfHeaderName: 'X-CSRFToken',
    transformRequest: [(data) => snakeProps(data), ...defaultTransformers()],
    paramsSerializer: (params) => qs.stringify(snakeProps(params), { indices: false }),
  })

  http.interceptors.request.use((config) => {
    // Django の LocaleMiddleware でエラーメッセージ等を利用者の言語にする
    config.headers.set('Accept-Language', getLocale())
    return config
  })

  http.interceptors.response.use(
    (res) => {
      const contentType = String(res.headers['content-type'] ?? '')
      // JSON を返すはずの API に HTML が返ったら、前段の認証プロキシのログイン画面とみなす
      if (contentType.includes('text/html') && res.config.responseType !== 'blob') reloadForSessionLost()
      return contentType.includes('application/json') ? camelProps(res.data) : res
    },
    async (error: AxiosError) => {
      // Nuxt の createError / showError が HTTP ステータスを引き継げるようにする
      Object.assign(error, { statusCode: error.response?.status ?? 500 })
      // 応答を受け取れない（タイムアウトを除く）のは、認証プロキシのセッション切れで IdP へリダイレクトされ
      // CORS で止められた場合が多い。画面ごと再読み込みして、認証プロキシにログインさせる
      if (!error.response && error.code !== 'ECONNABORTED' && error.code !== 'ETIMEDOUT') reloadForSessionLost()
      // 未ログインは 401（バックエンドの utils.authentication.SessionAuthentication が返す）。
      // 権限不足の 403 ではログイン画面へ移動しない
      if (error.response?.status === 401 && hooks.onUnauthorized) {
        await hooks.onUnauthorized()
      }
      return Promise.reject(error)
    },
  )
  return http
}
