// Used only by `just gates` (tools/gates/gates.py), never by `npm run lint`. gates.py passes the
// files and the complexity rules with their limits (`--rule`) and reads the scores.
import sonarjs from "eslint-plugin-sonarjs";
import tseslint from "typescript-eslint";

export default [
  {
    files: ["src/**/*.{ts,tsx}"],
    languageOptions: { parser: tseslint.parser },
    plugins: { sonarjs },
  },
];
