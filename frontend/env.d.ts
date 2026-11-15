/// <reference types="vite/client" />
declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<object, object, unknown>
  export default component
}

declare module 'markdown-it-texmath' {
  import type MarkdownIt from 'markdown-it'
  import type { KatexOptions } from 'katex'

  type TexmathOptions = {
    engine: typeof import('katex')
    delimiters?: string | string[]
    katexOptions?: KatexOptions
  }

  const texmath: (markdown: MarkdownIt, options: TexmathOptions) => void
  export default texmath
}
