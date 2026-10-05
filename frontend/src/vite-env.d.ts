/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_API_URL?: string
  readonly VITE_INTAKE_URL?: string
  readonly VITE_DEV_MAILBOX_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
