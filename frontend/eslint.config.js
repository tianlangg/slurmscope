import tseslint from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'

export default [
  { ignores: ['dist/**', 'node_modules/**'] },
  ...tseslint.configs.recommended,
  {
    files: ['**/*.{ts,tsx}'],
    plugins: { 'react-hooks': reactHooks },
    rules: { ...reactHooks.configs.recommended.rules },
  },
]
